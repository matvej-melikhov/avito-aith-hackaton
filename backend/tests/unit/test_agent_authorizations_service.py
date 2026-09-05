"""Focused application tests for interactive AgentAuthorization lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import cast
from uuid import UUID

import pytest

from review_platform.application.audit import AuditEvent, AuditRecorder
from review_platform.application.authorization import AuthorizationDenied, Authorizer
from review_platform.application.idempotency import IdempotencyReceipt
from review_platform.application.request_context import (
    AgentScope,
    AuthVersionSnapshot,
    RequestActor,
)
from review_platform.application.services.agent_authorizations import (
    AgentAuthorizationConflict,
    AgentAuthorizationGrantResult,
    AgentAuthorizationPermissionDenied,
    AgentAuthorizationService,
    InvalidAgentAuthorizationGrant,
    MembershipAuthority,
)
from review_platform.infrastructure.auth.agent_tokens import (
    IssuedAgentToken,
    digest_agent_token,
    issue_agent_token,
)
from review_platform.infrastructure.db.repositories.agents import AgentAuthorizationRecord

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000190001")
USER = UUID("00000000-0000-7000-8000-000000190002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000190003")
AGENT = UUID("00000000-0000-7000-8000-000000190004")
REQUEST = UUID("00000000-0000-7000-8000-000000190005")
TRACE = UUID("00000000-0000-7000-8000-000000190006")
SCOPES: tuple[AgentScope, ...] = (
    "courses:read",
    "reviews:read",
    "reviews:write",
)


class FakeGuard:
    def __init__(self) -> None:
        self.early: list[RequestActor] = []
        self.locked: list[tuple[RequestActor, object]] = []

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.early.append(actor)
        return _snapshot(actor)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        self.locked.append((actor, transaction))
        return _snapshot(actor)


class FakeMembershipRepository:
    def __init__(self, membership: MembershipAuthority | None) -> None:
        self.membership = membership
        self.calls: list[tuple[UUID, UUID, object]] = []

    async def lock_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        transaction: object,
    ) -> MembershipAuthority | None:
        self.calls.append((organization_id, membership_id, transaction))
        return self.membership


class FakeAuthorizationStore:
    def __init__(self) -> None:
        self.records: dict[UUID, AgentAuthorizationRecord] = {}
        self.invalidations: list[tuple[UUID, UUID, int | None, object]] = []
        self.revoke_calls = 0

    async def reserve(
        self,
        candidate: AgentAuthorizationRecord,
        *,
        transaction: object,
    ) -> tuple[AgentAuthorizationRecord, bool]:
        del transaction
        for record in self.records.values():
            if record.token_digest == candidate.token_digest:
                return record, False
        self.records[candidate.authorization_id] = candidate
        return candidate, True

    async def lock_by_id(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        transaction: object,
    ) -> AgentAuthorizationRecord | None:
        del transaction
        record = self.records.get(authorization_id)
        if record is None or record.organization_id != organization_id:
            return None
        return record

    async def revoke_linked(
        self,
        organization_id: UUID,
        user_id: UUID,
        authorization_id: UUID,
        *,
        expected_revision: int,
        expected_membership_revision: int,
        expected_auth_epoch: int,
        revoked_at: datetime,
        transaction: object,
    ) -> bool:
        del transaction
        self.revoke_calls += 1
        record = self.records.get(authorization_id)
        if (
            record is None
            or record.organization_id != organization_id
            or record.user_id != user_id
            or record.revision != expected_revision
            or record.membership_revision != expected_membership_revision
            or record.auth_epoch != expected_auth_epoch
            or record.status != "active"
        ):
            return False
        self.records[authorization_id] = replace(
            record,
            status="revoked",
            revision=expected_revision + 1,
            revoked_at=revoked_at,
        )
        return True

    async def invalidate_pending_receipts(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        authorization_revision: int | None = None,
        transaction: object,
    ) -> int:
        self.invalidations.append(
            (
                organization_id,
                authorization_id,
                authorization_revision,
                transaction,
            )
        )
        return 2


class FakeReceiptRepository:
    def __init__(self) -> None:
        self.receipts: dict[tuple[UUID, str], IdempotencyReceipt] = {}

    async def reserve(
        self, proposed: IdempotencyReceipt, *, transaction: object
    ) -> tuple[IdempotencyReceipt, bool]:
        del transaction
        key = (proposed.organization_id, proposed.idempotency_key)
        existing = self.receipts.get(key)
        if existing is not None:
            return existing, False
        self.receipts[key] = proposed
        return proposed, True


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        del transaction
        self.events.append(event)


@dataclass
class Harness:
    service: AgentAuthorizationService
    actor: RequestActor
    transaction: object
    guard: FakeGuard
    memberships: FakeMembershipRepository
    authorizations: FakeAuthorizationStore
    receipts: FakeReceiptRepository
    audit: FakeAuditRepository
    token_calls: list[int]


def _harness() -> Harness:
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )
    membership = MembershipAuthority(
        organization_id=ORG,
        membership_id=MEMBERSHIP,
        user_id=USER,
        roles=frozenset({"reviewer"}),
        status="active",
        revision=3,
        auth_epoch=2,
    )
    guard = FakeGuard()
    memberships = FakeMembershipRepository(membership)
    authorizations = FakeAuthorizationStore()
    receipts = FakeReceiptRepository()
    audit = FakeAuditRepository()
    token_calls: list[int] = []
    identifiers = count(1)

    def next_id() -> UUID:
        return UUID(int=next(identifiers))

    def issue() -> IssuedAgentToken:
        token_calls.append(1)
        return issue_agent_token(random_bytes=lambda size: b"\x19" * size)

    service = AgentAuthorizationService(
        membership_repository=memberships,
        authorization_repository=authorizations,
        receipt_repository=receipts,
        authorizer=Authorizer(guard, clock=lambda: NOW),
        audit=AuditRecorder(
            audit,
            event_id_factory=lambda: UUID(int=900),
            clock=lambda: NOW,
        ),
        token_issuer=issue,
        id_factory=next_id,
        clock=lambda: NOW,
    )
    return Harness(
        service=service,
        actor=actor,
        transaction=object(),
        guard=guard,
        memberships=memberships,
        authorizations=authorizations,
        receipts=receipts,
        audit=audit,
        token_calls=token_calls,
    )


async def _grant(
    harness: Harness,
    *,
    key: str = "agent-grant-key-0001",
    agent_id: UUID = AGENT,
    scopes: tuple[AgentScope, ...] = SCOPES,
    expires_at: datetime = NOW + timedelta(hours=1),
) -> AgentAuthorizationGrantResult:
    return await harness.service.grant(
        transaction=harness.transaction,
        organization_id=ORG,
        membership_id=MEMBERSHIP,
        expected_membership_revision=3,
        agent_id=agent_id,
        scopes=scopes,
        expires_at=expires_at,
        idempotency_key=key,
        actor=harness.actor,
        request_id=REQUEST,
        trace_id=TRACE,
    )


async def test_grant_returns_secret_once_and_persists_digest_only() -> None:
    harness = _harness()

    created = await _grant(harness)

    assert created.replayed is False
    assert created.access_token is not None
    secret = created.access_token.reveal()
    stored = harness.authorizations.records[created.authorization.authorization_id]
    assert stored.token_digest == digest_agent_token(secret)
    assert secret != stored.token_digest
    assert secret not in repr(stored)
    assert created.authorization.scopes == SCOPES
    assert harness.token_calls == [1]
    assert harness.audit.events[0].sanitized_details == {
        "agent_id": str(AGENT),
        "scopes": list(SCOPES),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    assert secret not in repr(harness.receipts.receipts)
    assert secret not in repr(harness.audit.events)

    replay = await _grant(harness)

    assert replay.replayed is True
    assert replay.access_token is None
    assert replay.authorization == created.authorization
    assert harness.token_calls == [1]
    assert len(harness.authorizations.records) == 1
    assert len(harness.audit.events) == 1
    assert len(harness.guard.locked) == 2


async def test_grant_rejects_unknown_or_duplicate_scope_and_bounded_ttl() -> None:
    harness = _harness()

    with pytest.raises(InvalidAgentAuthorizationGrant, match="unknown"):
        await _grant(
            harness,
            scopes=cast(tuple[AgentScope, ...], ("reviews:read", "admin:all")),
        )
    with pytest.raises(InvalidAgentAuthorizationGrant, match="unique"):
        await _grant(harness, scopes=("reviews:read", "reviews:read"))
    with pytest.raises(InvalidAgentAuthorizationGrant, match="future"):
        await _grant(harness, expires_at=NOW)
    with pytest.raises(InvalidAgentAuthorizationGrant, match="maximum TTL"):
        await _grant(harness, expires_at=NOW + timedelta(days=31))

    assert not harness.receipts.receipts
    assert not harness.authorizations.records
    assert not harness.token_calls


async def test_grant_rejects_agent_actor_and_non_owner_membership() -> None:
    harness = _harness()
    harness.actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=UUID(int=99),
        agent_authorization_revision=0,
        scopes={"reviews:write"},
        expires_at=NOW + timedelta(hours=1),
    )

    with pytest.raises(AuthorizationDenied, match="actor type"):
        await _grant(harness)

    harness = _harness()
    assert harness.memberships.membership is not None
    harness.memberships.membership = replace(
        harness.memberships.membership,
        user_id=UUID(int=700),
    )
    with pytest.raises(
        AgentAuthorizationPermissionDenied,
        match="represented membership owner",
    ):
        await _grant(harness)


async def test_same_key_different_grant_payload_is_a_typed_conflict() -> None:
    harness = _harness()
    await _grant(harness)

    with pytest.raises(AgentAuthorizationConflict, match="different canonical payload"):
        await _grant(harness, agent_id=UUID(int=701))

    assert harness.token_calls == [1]
    assert len(harness.authorizations.records) == 1


async def test_revoke_is_cas_bound_invalidates_pending_and_replays_as_noop() -> None:
    harness = _harness()
    created = await _grant(harness)
    authorization_id = created.authorization.authorization_id

    revoked = await harness.service.revoke(
        transaction=harness.transaction,
        organization_id=ORG,
        authorization_id=authorization_id,
        expected_authorization_revision=0,
        reason="work completed",
        idempotency_key="agent-revoke-key-001",
        actor=harness.actor,
        request_id=UUID(int=801),
        trace_id=TRACE,
    )

    assert revoked.replayed is False
    assert revoked.revision == 1
    assert revoked.invalidated_receipts == 2
    assert harness.authorizations.records[authorization_id].status == "revoked"
    assert harness.authorizations.invalidations == [(ORG, authorization_id, 0, harness.transaction)]
    assert harness.audit.events[-1].action == "revoke_agent_authorization"

    replay = await harness.service.revoke(
        transaction=harness.transaction,
        organization_id=ORG,
        authorization_id=authorization_id,
        expected_authorization_revision=0,
        reason="work completed",
        idempotency_key="agent-revoke-key-001",
        actor=harness.actor,
        request_id=UUID(int=802),
        trace_id=TRACE,
    )
    assert replay.replayed is True
    assert replay.revision == 1
    assert replay.invalidated_receipts == 0
    assert harness.authorizations.revoke_calls == 1
    assert len(harness.authorizations.invalidations) == 1
    assert len(harness.audit.events) == 2


async def test_distinct_revoke_commands_have_one_cas_winner() -> None:
    harness = _harness()
    created = await _grant(harness)
    authorization_id = created.authorization.authorization_id

    await harness.service.revoke(
        transaction=harness.transaction,
        organization_id=ORG,
        authorization_id=authorization_id,
        expected_authorization_revision=0,
        reason="first contender",
        idempotency_key="agent-revoke-race-001",
        actor=harness.actor,
        request_id=UUID(int=901),
        trace_id=TRACE,
    )
    with pytest.raises(AgentAuthorizationConflict, match="inactive, or stale"):
        await harness.service.revoke(
            transaction=harness.transaction,
            organization_id=ORG,
            authorization_id=authorization_id,
            expected_authorization_revision=0,
            reason="second contender",
            idempotency_key="agent-revoke-race-002",
            actor=harness.actor,
            request_id=UUID(int=902),
            trace_id=TRACE,
        )

    assert harness.authorizations.revoke_calls == 2
    assert harness.authorizations.records[authorization_id].revision == 1
    assert len(harness.authorizations.invalidations) == 1


def _snapshot(actor: RequestActor) -> AuthVersionSnapshot:
    assert actor.user_id is not None
    assert actor.membership_revision is not None
    assert actor.auth_epoch is not None
    return AuthVersionSnapshot(
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        roles=actor.roles,
        membership_revision=actor.membership_revision,
        auth_epoch=actor.auth_epoch,
        active=True,
        agent_authorization_id=actor.agent_authorization_id,
        agent_authorization_revision=actor.agent_authorization_revision,
        agent_scopes=actor.scopes,
        agent_expires_at=actor.expires_at,
        agent_active=True,
    )
