"""MySQL-backed executable specification for the concrete user auth guard."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from review_platform.application.auth_guards.membership import (
    MembershipInactive,
    MembershipNotFound,
    StaleMembershipAuthority,
    UserMembershipAuthGuard,
)
from review_platform.application.request_context import AuthVersionGuard, RequestActor
from review_platform.infrastructure.db.models import OrganizationMembership, User
from review_platform.infrastructure.db.repositories.identity import (
    OrganizationMembershipRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
USER_ID = UUID("00000000-0000-7000-8000-000000000551")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000552")


def _actor(
    *,
    organization_id: UUID = ORG_A,
    membership_revision: int = 3,
    auth_epoch: int = 2,
) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=USER_ID,
        roles={"methodologist", "reviewer"},
        membership_revision=membership_revision,
        auth_epoch=auth_epoch,
    )


async def _seed_membership(
    factory: AsyncSessionFactory,
    *,
    status: str = "active",
) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=USER_ID, display_name="Guard User", status="active"))
        await session.flush()
        await OrganizationMembershipRepository(session).add(
            OrganizationMembership(
                id=MEMBERSHIP_ID,
                organization_id=ORG_A,
                user_id=USER_ID,
                roles=["methodologist", "reviewer"],
                status=status,
                revision=3,
                auth_epoch=2,
            )
        )


async def test_guard_satisfies_protocol_and_returns_current_user_snapshot(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_membership(foundation_session_factory)
    guard: AuthVersionGuard = UserMembershipAuthGuard(foundation_session_factory)

    early = await guard.revalidate(actor=_actor())
    async with foundation_session_factory() as session:
        final = await guard.lock_and_revalidate(actor=_actor(), transaction=session)
        assert session.in_transaction()
        await session.rollback()

    assert early == final
    assert final.organization_id == ORG_A
    assert final.user_id == USER_ID
    assert final.roles == frozenset({"methodologist", "reviewer"})
    assert final.membership_revision == 3
    assert final.auth_epoch == 2
    assert final.active is True


@pytest.mark.parametrize(
    ("revision", "epoch"),
    [(2, 2), (3, 1), (4, 2), (3, 3)],
)
async def test_guard_rejects_stale_membership_revision_or_auth_epoch(
    foundation_session_factory: AsyncSessionFactory,
    revision: int,
    epoch: int,
) -> None:
    await _seed_membership(foundation_session_factory)
    guard = UserMembershipAuthGuard(foundation_session_factory)

    with pytest.raises(StaleMembershipAuthority):
        await guard.revalidate(
            actor=_actor(membership_revision=revision, auth_epoch=epoch)
        )


async def test_guard_rejects_archived_membership(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_membership(foundation_session_factory, status="archived")
    guard = UserMembershipAuthGuard(foundation_session_factory)

    with pytest.raises(MembershipInactive):
        await guard.revalidate(actor=_actor())


async def test_guard_never_falls_back_to_cross_tenant_user_lookup(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_membership(foundation_session_factory)
    guard = UserMembershipAuthGuard(foundation_session_factory)

    with pytest.raises(MembershipNotFound):
        await guard.revalidate(actor=_actor(organization_id=ORG_B))


async def test_final_revalidation_locks_organization_then_membership_and_sees_revocation(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_membership(foundation_session_factory)
    guard = UserMembershipAuthGuard(foundation_session_factory)
    assert (await guard.revalidate(actor=_actor())).active

    async with session_scope(foundation_session_factory) as mutation_session:
        membership = await OrganizationMembershipRepository(mutation_session).get_by_user(
            ORG_A,
            USER_ID,
            for_update=True,
        )
        assert membership is not None
        membership.auth_epoch += 1
        membership.revision += 1

    statements: list[str] = []
    async with foundation_session_factory() as session:
        sync_engine = cast(Engine, session.get_bind())

        def record_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(" ".join(statement.casefold().split()))

        event.listen(sync_engine, "before_cursor_execute", record_statement)
        try:
            with pytest.raises(StaleMembershipAuthority):
                await guard.lock_and_revalidate(actor=_actor(), transaction=session)
        finally:
            event.remove(sync_engine, "before_cursor_execute", record_statement)
            await session.rollback()

    locked_selects = [
        statement
        for statement in statements
        if statement.startswith("select") and "for update" in statement
    ]
    assert len(locked_selects) == 2
    assert " from organization " in locked_selects[0]
    assert " from organization_membership " in locked_selects[1]
    assert "organization_membership.organization_id" in locked_selects[1]
    assert "organization_membership.user_id" in locked_selects[1]
