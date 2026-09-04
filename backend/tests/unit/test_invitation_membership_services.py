"""MySQL-backed invitation and membership service tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditRecorder
from review_platform.application.auth_guards.membership import UserMembershipAuthGuard
from review_platform.application.request_context import RequestActor
from review_platform.application.services.invitations import (
    InvitationConflict,
    InvitationEmailIntent,
    InvitationEmailIntentPort,
    InvitationSecretProtector,
    InvitationService,
    VerifiedEmailIdentity,
    VerifiedIdentityMismatch,
)
from review_platform.application.services.memberships import (
    LastMethodologistViolation,
    MembershipService,
    StudentSelfEscalation,
)
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    AuditEvent,
    CommandReceipt,
    Invitation,
    OrganizationMembership,
    OutboxMessage,
    Session,
    User,
)
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER_A = UUID("00000000-0000-7000-8000-000000000601")
USER_B = UUID("00000000-0000-7000-8000-000000000602")
MEMBERSHIP_A = UUID("00000000-0000-7000-8000-000000000603")
MEMBERSHIP_B = UUID("00000000-0000-7000-8000-000000000604")
INVITATION_ID = UUID("00000000-0000-7000-8000-000000000605")
REQUEST_ID = UUID("00000000-0000-7000-8000-000000000606")
TRACE_ID = UUID("00000000-0000-7000-8000-000000000607")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
TOKEN = "invitation-token-value-which-is-long-enough"


class DigestProtector(InvitationSecretProtector):
    def seal(self, magic_link: str) -> str:
        return f"sealed:{sha256_digest(magic_link)}"


class SqlInvitationEmailIntents(InvitationEmailIntentPort):
    async def enqueue(self, intent: InvitationEmailIntent, *, transaction: object) -> None:
        assert isinstance(transaction, AsyncSession)
        await OutboxService(OutboxMessageRepository(transaction), clock=lambda: NOW).create(
            OutboxDraft(
                organization_id=intent.organization_id,
                message_id=intent.message_id,
                aggregate_type="invitation",
                aggregate_id=intent.invitation_id,
                event_type="InvitationEmailRequested",
                payload_version="1.1.0",
                payload={
                    "invitation_id": str(intent.invitation_id),
                    "recipient": intent.recipient,
                    "sealed_magic_link": intent.sealed_magic_link,
                    "expires_at": intent.expires_at.isoformat(),
                },
                available_at=NOW,
                max_attempts=5,
            )
        )


def _audit() -> AuditRecorder:
    return AuditRecorder(
        SqlAppendOnlyAuditRepository(),
        event_id_factory=uuid4,
        clock=lambda: NOW,
    )


def _invitation_service() -> InvitationService:
    return InvitationService(
        email_intents=SqlInvitationEmailIntents(),
        secret_protector=DigestProtector(),
        audit=_audit(),
        magic_link_base_url="https://review.example.test/auth/reviewer/magic-link",
        id_factory=uuid4,
        token_factory=lambda: TOKEN,
        clock=lambda: NOW,
    )


def _actor(
    user_id: UUID,
    *,
    roles: set[str],
    membership_revision: int = 0,
    auth_epoch: int = 0,
) -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=user_id,
        roles=roles,  # type: ignore[arg-type]
        membership_revision=membership_revision,
        auth_epoch=auth_epoch,
    )


async def _seed_users_and_memberships(
    factory: AsyncSessionFactory,
    *,
    roles_a: list[str],
    roles_b: list[str] | None = None,
) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=USER_A, display_name="Methodologist A", status="active"),
                User(id=USER_B, display_name="User B", status="active"),
            ]
        )
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP_A,
                organization_id=ORG,
                user_id=USER_A,
                roles=roles_a,
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )
        if roles_b is not None:
            session.add(
                OrganizationMembership(
                    id=MEMBERSHIP_B,
                    organization_id=ORG,
                    user_id=USER_B,
                    roles=roles_b,
                    status="active",
                    revision=0,
                    auth_epoch=0,
                )
            )


async def test_issue_persists_only_digest_plus_sealed_email_outbox_and_audit(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_users_and_memberships(
        foundation_session_factory,
        roles_a=["methodologist"],
    )
    actor = _actor(USER_A, roles={"methodologist"})
    async with session_scope(foundation_session_factory) as session:
        result = await _invitation_service().issue(
            transaction=session,
            actor=actor,
            email=" Reviewer@Example.TEST ",
            role="reviewer",
            expires_at=NOW + timedelta(hours=1),
            request_id=REQUEST_ID,
            trace_id=TRACE_ID,
        )

    async with foundation_session_factory() as session:
        invitation = (
            await session.execute(select(Invitation).where(Invitation.id == result.invitation_id))
        ).scalar_one()
        outbox = (await session.execute(select(OutboxMessage))).scalar_one()
        audit = (await session.execute(select(AuditEvent))).scalar_one()

    assert result.normalized_email == "reviewer@example.test"
    assert invitation.token_digest == sha256_digest(TOKEN)
    assert TOKEN not in json.dumps(outbox.payload, sort_keys=True)
    assert outbox.event_type == "InvitationEmailRequested"
    assert outbox.aggregate_id == invitation.id
    assert outbox.payload["sealed_magic_link"].startswith("sealed:sha256:")
    assert (audit.action, audit.entity_id, audit.before_revision, audit.after_revision) == (
        "create_invitation",
        invitation.id,
        None,
        0,
    )


async def test_consume_and_revoke_are_one_linearizable_winner(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_users_and_memberships(
        foundation_session_factory,
        roles_a=["methodologist"],
        roles_b=["reviewer"],
    )
    async with session_scope(foundation_session_factory) as session:
        session.add(
            Invitation(
                id=INVITATION_ID,
                organization_id=ORG,
                role="reviewer",
                normalized_email="reviewer@example.test",
                token_digest=sha256_digest(TOKEN),
                expires_at=NOW + timedelta(hours=1),
                issued_by=USER_A,
                status="active",
                revision=0,
            )
        )

    identity = VerifiedEmailIdentity(
        organization_id=ORG,
        user_id=USER_B,
        provider="email_magic_link",
        issuer="review-platform",
        subject="reviewer@example.test",
        verified_email="reviewer@example.test",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        state_id=UUID("00000000-0000-7000-8000-000000000608"),
    )
    outcomes: list[str] = []

    async def consume() -> None:
        try:
            async with session_scope(foundation_session_factory) as session:
                await _invitation_service().consume(
                    transaction=session,
                    actor=_actor(USER_B, roles={"reviewer"}),
                    invitation_id=INVITATION_ID,
                    expected_revision=0,
                    token=TOKEN,
                    identity=identity,
                    request_id=REQUEST_ID,
                    trace_id=TRACE_ID,
                )
            outcomes.append("consumed")
        except InvitationConflict:
            outcomes.append("consume_conflict")

    async def revoke() -> None:
        try:
            async with session_scope(foundation_session_factory) as session:
                await _invitation_service().revoke(
                    transaction=session,
                    actor=_actor(USER_A, roles={"methodologist"}),
                    invitation_id=INVITATION_ID,
                    expected_revision=0,
                    request_id=REQUEST_ID,
                    trace_id=TRACE_ID,
                )
            outcomes.append("revoked")
        except InvitationConflict:
            outcomes.append("revoke_conflict")

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        task_group.start_soon(revoke)

    assert len({"consumed", "revoked"}.intersection(outcomes)) == 1
    assert len(outcomes) == 2
    async with foundation_session_factory() as session:
        invitation = (
            await session.execute(select(Invitation).where(Invitation.id == INVITATION_ID))
        ).scalar_one()
        audits = (await session.execute(select(AuditEvent))).scalars().all()
    assert invitation.status in {"consumed", "revoked"}
    assert invitation.revision == 1
    assert len(audits) == 1


async def test_consume_requires_exact_verified_email_identity(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_users_and_memberships(
        foundation_session_factory,
        roles_a=["methodologist"],
        roles_b=["reviewer"],
    )
    async with session_scope(foundation_session_factory) as session:
        session.add(
            Invitation(
                id=INVITATION_ID,
                organization_id=ORG,
                role="reviewer",
                normalized_email="reviewer@example.test",
                token_digest=sha256_digest(TOKEN),
                expires_at=NOW + timedelta(hours=1),
                issued_by=USER_A,
                status="active",
                revision=0,
            )
        )

    mismatched_identity = VerifiedEmailIdentity(
        organization_id=ORG,
        user_id=USER_B,
        provider="email_magic_link",
        issuer="review-platform",
        subject="someone-else@example.test",
        verified_email="someone-else@example.test",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        state_id=UUID("00000000-0000-7000-8000-000000000608"),
    )
    async with foundation_session_factory() as session:
        with pytest.raises(VerifiedIdentityMismatch, match="verified email"):
            await _invitation_service().consume(
                transaction=session,
                actor=_actor(USER_B, roles={"reviewer"}),
                invitation_id=INVITATION_ID,
                expected_revision=0,
                token=TOKEN,
                identity=mismatched_identity,
                request_id=REQUEST_ID,
                trace_id=TRACE_ID,
            )
        await session.rollback()


async def test_role_mutation_invalidates_derived_authority_and_audits(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_users_and_memberships(
        foundation_session_factory,
        roles_a=["methodologist"],
        roles_b=["student"],
    )
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                Session(
                    id=UUID("00000000-0000-7000-8000-000000000609"),
                    organization_id=ORG,
                    user_id=USER_B,
                    membership_id=MEMBERSHIP_B,
                    membership_revision=0,
                    auth_epoch=0,
                    token_digest="sha256:" + "1" * 64,
                    expires_at=NOW + timedelta(hours=1),
                    status="active",
                ),
                AgentAuthorization(
                    id=UUID("00000000-0000-7000-8000-000000000610"),
                    organization_id=ORG,
                    user_id=USER_B,
                    agent_id=UUID("00000000-0000-7000-8000-000000000611"),
                    scopes=["courses:read"],
                    membership_revision=0,
                    auth_epoch=0,
                    token_digest="sha256:" + "2" * 64,
                    status="active",
                    expires_at=NOW + timedelta(hours=1),
                    revision=0,
                ),
                CommandReceipt(
                    id=UUID("00000000-0000-7000-8000-000000000612"),
                    organization_id=ORG,
                    idempotency_key="pending-membership-command-0001",
                    request_id=REQUEST_ID,
                    command_name="preflight_submission",
                    target_id=MEMBERSHIP_B,
                    expected_revision=0,
                    payload_digest="sha256:" + "3" * 64,
                    actor_snapshot={"user_id": str(USER_B)},
                    status="reserved",
                    result_reference={"kind": "command", "id": "pending"},
                ),
            ]
        )

    service = MembershipService(
        auth_guard=UserMembershipAuthGuard(foundation_session_factory),
        audit=_audit(),
        clock=lambda: NOW,
    )
    async with session_scope(foundation_session_factory) as session:
        result = await service.change_roles(
            transaction=session,
            actor=_actor(USER_A, roles={"methodologist"}),
            membership_id=MEMBERSHIP_B,
            expected_revision=0,
            roles={"student", "reviewer"},
            request_id=REQUEST_ID,
            trace_id=TRACE_ID,
        )

    assert (result.revision, result.auth_epoch) == (1, 1)
    assert (result.revoked_sessions, result.revoked_agent_authorizations) == (1, 1)
    assert result.invalidated_receipts == 1
    async with foundation_session_factory() as session:
        membership = await session.get(OrganizationMembership, MEMBERSHIP_B)
        session_row = (await session.execute(select(Session))).scalar_one()
        authorization = (await session.execute(select(AgentAuthorization))).scalar_one()
        receipt = (await session.execute(select(CommandReceipt))).scalar_one()
        audit = (await session.execute(select(AuditEvent))).scalar_one()
    assert membership is not None
    assert membership.roles == ["reviewer", "student"]
    assert session_row.status == "revoked"
    assert authorization.status == "revoked"
    assert receipt.status == "invalidated"
    assert (audit.action, audit.before_revision, audit.after_revision) == (
        "change_membership_roles",
        0,
        1,
    )


async def test_two_concurrent_removals_leave_one_active_methodologist(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_users_and_memberships(
        foundation_session_factory,
        roles_a=["methodologist", "reviewer"],
        roles_b=["methodologist", "reviewer"],
    )
    outcomes: list[str] = []

    async def remove(user_id: UUID, membership_id: UUID) -> None:
        service = MembershipService(
            auth_guard=UserMembershipAuthGuard(foundation_session_factory),
            audit=_audit(),
            clock=lambda: NOW,
        )
        try:
            async with session_scope(foundation_session_factory) as session:
                await service.change_roles(
                    transaction=session,
                    actor=_actor(user_id, roles={"methodologist", "reviewer"}),
                    membership_id=membership_id,
                    expected_revision=0,
                    roles={"reviewer"},
                    request_id=uuid4(),
                    trace_id=uuid4(),
                )
            outcomes.append("removed")
        except LastMethodologistViolation:
            outcomes.append("last_methodologist")

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(remove, USER_A, MEMBERSHIP_A)
        task_group.start_soon(remove, USER_B, MEMBERSHIP_B)

    assert sorted(outcomes) == ["last_methodologist", "removed"]
    async with foundation_session_factory() as session:
        memberships = (
            await session.execute(
                select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == ORG,
                    OrganizationMembership.status == "active",
                )
            )
        ).scalars().all()
    assert sum("methodologist" in membership.roles for membership in memberships) == 1


async def test_student_cannot_self_escalate(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_users_and_memberships(
        foundation_session_factory,
        roles_a=["methodologist"],
        roles_b=["student"],
    )
    service = MembershipService(
        auth_guard=UserMembershipAuthGuard(foundation_session_factory),
        audit=_audit(),
        clock=lambda: NOW,
    )

    async with foundation_session_factory() as session:
        with pytest.raises(StudentSelfEscalation):
            await service.change_roles(
                transaction=session,
                actor=_actor(USER_B, roles={"student"}),
                membership_id=MEMBERSHIP_B,
                expected_revision=0,
                roles={"student", "reviewer"},
                request_id=REQUEST_ID,
                trace_id=TRACE_ID,
            )
        await session.rollback()
