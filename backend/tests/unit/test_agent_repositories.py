"""MySQL tests for tenant-scoped AgentAuthorization persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import anyio
import pytest
from sqlalchemy import select

from review_platform.infrastructure.auth.agent_tokens import issue_agent_token
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    CommandReceipt,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.repositories.agents import (
    AgentAuthorizationCollision,
    AgentAuthorizationRecord,
    InvalidAgentAuthorizationTransaction,
    SqlAgentAuthorizationRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
USER = UUID("00000000-0000-7000-8000-000000151001")
OTHER_USER = UUID("00000000-0000-7000-8000-000000151002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000151003")
OTHER_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000151004")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000151005")
AGENT = UUID("00000000-0000-7000-8000-000000151006")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _seed_memberships(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=USER, display_name="Agent owner"),
                User(id=OTHER_USER, display_name="Other owner"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=USER,
                    roles=["reviewer"],
                    status="active",
                    revision=3,
                    auth_epoch=2,
                ),
                OrganizationMembership(
                    id=OTHER_MEMBERSHIP,
                    organization_id=OTHER_ORG,
                    user_id=OTHER_USER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
            ]
        )


def _issued_digest() -> tuple[str, str]:
    issued = issue_agent_token(random_bytes=lambda size: b"A" * size)
    return issued.access_token.reveal(), issued.token_digest


def _candidate(token_digest: str) -> AgentAuthorizationRecord:
    return AgentAuthorizationRecord(
        authorization_id=AUTHORIZATION,
        organization_id=ORG,
        user_id=USER,
        agent_id=AGENT,
        scopes=("reviews:read", "reviews:write"),
        membership_revision=3,
        auth_epoch=2,
        token_digest=token_digest,
        status="active",
        expires_at=NOW + timedelta(hours=1),
        revision=0,
    )


async def test_digest_reserve_race_and_lookup_do_not_leak_tenant_or_raw_token(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_memberships(foundation_session_factory)
    raw_token, token_digest = _issued_digest()
    repository = SqlAgentAuthorizationRepository()
    results: list[tuple[UUID, bool]] = []

    async def reserve() -> None:
        async with session_scope(foundation_session_factory) as session:
            stored, created = await repository.reserve(
                _candidate(token_digest),
                transaction=session,
            )
            results.append((stored.authorization_id, created))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reserve)
        tasks.start_soon(reserve)

    assert sorted(created for _, created in results) == [False, True]
    assert {identity for identity, _ in results} == {AUTHORIZATION}
    async with foundation_session_factory() as session:
        global_result = await repository.lookup_by_digest(
            token_digest,
            transaction=session,
        )
        assert global_result is not None
        assert global_result.organization_id == ORG
        assert (
            await repository.lookup_tenant_by_digest(
                OTHER_ORG,
                token_digest,
                transaction=session,
            )
            is None
        )
        assert (
            await repository.lock_by_id(
                OTHER_ORG,
                AUTHORIZATION,
                transaction=session,
            )
            is None
        )
        with pytest.raises(ValueError, match="digest"):
            await repository.lookup_by_digest(raw_token, transaction=session)
        with pytest.raises(AgentAuthorizationCollision, match="already bound"):
            await repository.reserve(
                replace(
                    _candidate(token_digest),
                    authorization_id=UUID("00000000-0000-7000-8000-000000151007"),
                    organization_id=OTHER_ORG,
                    user_id=OTHER_USER,
                    membership_revision=0,
                    auth_epoch=0,
                ),
                transaction=session,
            )


async def test_active_resolution_checks_membership_revision_auth_epoch_and_expiry(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_memberships(foundation_session_factory)
    _, token_digest = _issued_digest()
    repository = SqlAgentAuthorizationRepository()
    async with session_scope(foundation_session_factory) as session:
        await repository.reserve(_candidate(token_digest), transaction=session)

    async with session_scope(foundation_session_factory) as session:
        active = await repository.resolve_active_digest(
            token_digest,
            now=NOW,
            transaction=session,
        )
        assert active is not None
        assert active.membership_id == MEMBERSHIP
        assert active.authorization.authorization_id == AUTHORIZATION
        locked = await repository.lock_membership_then_authorization(
            ORG,
            USER,
            AUTHORIZATION,
            transaction=session,
        )
        assert locked == active
        membership = await session.get(OrganizationMembership, MEMBERSHIP)
        assert membership is not None
        membership.auth_epoch = 3

    async with foundation_session_factory() as session:
        assert (
            await repository.resolve_active_digest(
                token_digest,
                now=NOW,
                transaction=session,
            )
            is None
        )
        membership = await session.get(OrganizationMembership, MEMBERSHIP)
        assert membership is not None
        membership.auth_epoch = 2
        await session.flush()
        assert (
            await repository.resolve_active_digest(
                token_digest,
                now=NOW + timedelta(hours=2),
                transaction=session,
            )
            is None
        )


async def test_revoke_cas_has_one_winner_and_invalidates_only_linked_pending_commands(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_memberships(foundation_session_factory)
    _, token_digest = _issued_digest()
    repository = SqlAgentAuthorizationRepository()
    async with session_scope(foundation_session_factory) as session:
        await repository.reserve(_candidate(token_digest), transaction=session)
        session.add_all(
            [
                _receipt(151010, "reserved", AUTHORIZATION, ORG),
                _receipt(151011, "processing", AUTHORIZATION, ORG),
                _receipt(151012, "succeeded", AUTHORIZATION, ORG),
                _receipt(
                    151013,
                    "reserved",
                    UUID("00000000-0000-7000-8000-000000151099"),
                    ORG,
                ),
            ]
        )

    outcomes: list[bool] = []

    async def revoke() -> None:
        async with session_scope(foundation_session_factory) as session:
            outcomes.append(
                await repository.revoke_linked(
                    ORG,
                    USER,
                    AUTHORIZATION,
                    expected_revision=0,
                    expected_membership_revision=3,
                    expected_auth_epoch=2,
                    revoked_at=NOW,
                    transaction=session,
                )
            )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(revoke)
        tasks.start_soon(revoke)
    assert sorted(outcomes) == [False, True]

    async with session_scope(foundation_session_factory) as session:
        assert (
            await repository.invalidate_pending_receipts(
                ORG,
                AUTHORIZATION,
                authorization_revision=0,
                transaction=session,
            )
            == 2
        )

    async with foundation_session_factory() as session:
        authorization = await session.get(AgentAuthorization, AUTHORIZATION)
        assert authorization is not None
        assert (authorization.status, authorization.revision, authorization.revoked_at) == (
            "revoked",
            1,
            NOW,
        )
        receipts = (
            await session.scalars(
                select(CommandReceipt)
                .where(CommandReceipt.organization_id == ORG)
                .order_by(CommandReceipt.id)
            )
        ).all()
        assert [receipt.status for receipt in receipts] == [
            "invalidated",
            "invalidated",
            "succeeded",
            "reserved",
        ]


async def test_repository_requires_caller_owned_session_and_never_commits(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_memberships(foundation_session_factory)
    _, token_digest = _issued_digest()
    repository = SqlAgentAuthorizationRepository()
    with pytest.raises(InvalidAgentAuthorizationTransaction, match="caller-owned"):
        await repository.lookup_by_digest(token_digest, transaction=object())

    async with foundation_session_factory() as session:
        await repository.reserve(_candidate(token_digest), transaction=session)
        await session.rollback()
    async with foundation_session_factory() as session:
        assert await session.get(AgentAuthorization, AUTHORIZATION) is None


def _receipt(
    suffix: int,
    status: str,
    authorization_id: UUID,
    organization_id: UUID,
) -> CommandReceipt:
    identity = UUID(f"00000000-0000-7000-8000-{suffix:012d}")
    return CommandReceipt(
        id=identity,
        organization_id=organization_id,
        idempotency_key=f"agent-pending-command-{suffix}",
        request_id=identity,
        command_name="save_review_revision",
        target_id=identity,
        expected_revision=0,
        payload_digest="sha256:" + "f" * 64,
        actor_snapshot={
            "type": "agent",
            "agent_authorization_id": str(authorization_id),
            "agent_authorization_revision": 0,
        },
        status=status,
        result_reference=None,
    )
