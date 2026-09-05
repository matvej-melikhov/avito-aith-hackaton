from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from review_platform.infrastructure.auth.agent_tokens import issue_agent_token
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.repositories.agents import (
    AgentAuthorizationRecord,
    LockedAgentAuthority,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.mcp.authentication import (
    MCPAuthenticationError,
    MCPBearerAuthenticator,
    resolve_mcp_bearer,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG_A = UUID("00000000-0000-7000-8000-000000182001")
ORG_B = UUID("00000000-0000-7000-8000-000000182002")
USER_A = UUID("00000000-0000-7000-8000-000000182003")
USER_B = UUID("00000000-0000-7000-8000-000000182004")
MEMBERSHIP_A = UUID("00000000-0000-7000-8000-000000182005")
MEMBERSHIP_B = UUID("00000000-0000-7000-8000-000000182006")
AGENT_A = UUID("00000000-0000-7000-8000-000000182007")
AGENT_B = UUID("00000000-0000-7000-8000-000000182008")
AUTHORIZATION_A = UUID("00000000-0000-7000-8000-000000182009")
AUTHORIZATION_B = UUID("00000000-0000-7000-8000-000000182010")


@dataclass(slots=True)
class RecordingResolver:
    result: LockedAgentAuthority | None
    calls: list[tuple[str, datetime, object]]

    async def resolve_active_digest(
        self,
        token_digest: str,
        *,
        now: datetime,
        transaction: object,
    ) -> LockedAgentAuthority | None:
        self.calls.append((token_digest, now, transaction))
        return self.result


def _authority(*, token_digest: str) -> LockedAgentAuthority:
    return LockedAgentAuthority(
        organization_id=ORG_A,
        membership_id=MEMBERSHIP_A,
        user_id=USER_A,
        roles=("reviewer", "methodologist"),
        membership_status="active",
        membership_revision=7,
        auth_epoch=3,
        authorization=AgentAuthorizationRecord(
            authorization_id=AUTHORIZATION_A,
            organization_id=ORG_A,
            user_id=USER_A,
            agent_id=AGENT_A,
            scopes=("courses:read", "reviews:read"),
            membership_revision=7,
            auth_epoch=3,
            token_digest=token_digest,
            status="active",
            expires_at=NOW + timedelta(hours=1),
            revision=5,
        ),
    )


async def test_bearer_alone_builds_exact_current_agent_actor() -> None:
    issued = issue_agent_token(random_bytes=lambda size: b"a" * size)
    transaction = object()
    repository = RecordingResolver(_authority(token_digest=issued.token_digest), [])

    actor = await resolve_mcp_bearer(
        f"Bearer {issued.access_token.reveal()}",
        transaction=transaction,
        repository=repository,
        clock=lambda: NOW,
    )

    assert actor.actor_type == "agent"
    assert actor.organization_id == ORG_A
    assert actor.user_id == USER_A
    assert actor.roles == {"reviewer", "methodologist"}
    assert actor.membership_revision == 7
    assert actor.auth_epoch == 3
    assert actor.agent_id == AGENT_A
    assert actor.agent_authorization_id == AUTHORIZATION_A
    assert actor.agent_authorization_revision == 5
    assert actor.scopes == {"courses:read", "reviews:read"}
    assert actor.expires_at == NOW + timedelta(hours=1)
    assert repository.calls == [(issued.token_digest, NOW, transaction)]
    assert "organization" not in inspect.signature(MCPBearerAuthenticator.resolve).parameters


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic credentials",
        "Bearer",
        "Bearer  raw-token",
        "Bearer raw-token extra",
        "Bearer rpat_v0." + "A" * 43,
    ],
)
async def test_invalid_bearers_are_generic_and_never_reach_repository(
    header: str | None,
) -> None:
    repository = RecordingResolver(None, [])

    with pytest.raises(MCPAuthenticationError) as raised:
        await MCPBearerAuthenticator(repository, clock=lambda: NOW).resolve(
            header,
            transaction=object(),
        )

    assert str(raised.value) == "invalid agent bearer credentials"
    assert repr(header) not in repr(raised.value)
    assert repository.calls == []


@pytest.mark.parametrize("mutation", ["revoked", "expired", "version_mismatch"])
async def test_inactive_or_version_mismatched_authority_is_one_generic_failure(
    mutation: str,
) -> None:
    issued = issue_agent_token(random_bytes=lambda size: b"b" * size)
    authority = _authority(token_digest=issued.token_digest)
    authorization = authority.authorization
    if mutation == "revoked":
        authorization = replace(
            authorization,
            status="revoked",
            revoked_at=NOW,
        )
    elif mutation == "expired":
        authorization = replace(authorization, expires_at=NOW)
    else:
        authorization = replace(authorization, membership_revision=8)
    authority = LockedAgentAuthority(
        organization_id=authority.organization_id,
        membership_id=authority.membership_id,
        user_id=authority.user_id,
        roles=authority.roles,
        membership_status=authority.membership_status,
        membership_revision=authority.membership_revision,
        auth_epoch=authority.auth_epoch,
        authorization=authorization,
    )

    with pytest.raises(MCPAuthenticationError) as raised:
        await MCPBearerAuthenticator(
            RecordingResolver(authority, []),
            clock=lambda: NOW,
        ).resolve(
            f"Bearer {issued.access_token.reveal()}",
            transaction=object(),
        )

    assert str(raised.value) == "invalid agent bearer credentials"


async def test_real_mysql_resolution_is_global_by_digest_and_tenant_exact(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    first = issue_agent_token(random_bytes=lambda size: b"c" * size)
    second = issue_agent_token(random_bytes=lambda size: b"d" * size)
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                Organization(id=ORG_A, slug="mcp-auth-a", name="MCP Auth A"),
                Organization(id=ORG_B, slug="mcp-auth-b", name="MCP Auth B"),
                User(id=USER_A, display_name="MCP User A", status="active"),
                User(id=USER_B, display_name="MCP User B", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_A,
                    organization_id=ORG_A,
                    user_id=USER_A,
                    roles=["reviewer"],
                    status="active",
                    revision=2,
                    auth_epoch=1,
                ),
                OrganizationMembership(
                    id=MEMBERSHIP_B,
                    organization_id=ORG_B,
                    user_id=USER_B,
                    roles=["methodologist"],
                    status="active",
                    revision=4,
                    auth_epoch=3,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _authorization(
                    AUTHORIZATION_A,
                    ORG_A,
                    USER_A,
                    AGENT_A,
                    first.token_digest,
                    scopes=["courses:read"],
                    membership_revision=2,
                    auth_epoch=1,
                    revision=5,
                ),
                _authorization(
                    AUTHORIZATION_B,
                    ORG_B,
                    USER_B,
                    AGENT_B,
                    second.token_digest,
                    scopes=["operations:read"],
                    membership_revision=4,
                    auth_epoch=3,
                    revision=6,
                ),
            ]
        )

    authenticator = MCPBearerAuthenticator(clock=lambda: NOW)
    async with foundation_session_factory() as session:
        actor_a = await authenticator.resolve(
            f"Bearer {first.access_token.reveal()}",
            transaction=session,
        )
        actor_b = await authenticator.resolve(
            f"Bearer {second.access_token.reveal()}",
            transaction=session,
        )

    assert (actor_a.organization_id, actor_a.user_id, actor_a.scopes) == (
        ORG_A,
        USER_A,
        {"courses:read"},
    )
    assert (actor_b.organization_id, actor_b.user_id, actor_b.scopes) == (
        ORG_B,
        USER_B,
        {"operations:read"},
    )


async def test_real_mysql_revoke_and_membership_epoch_change_are_immediate(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    issued = issue_agent_token(random_bytes=lambda size: b"e" * size)
    await _seed_one(foundation_session_factory, issued.token_digest)
    authenticator = MCPBearerAuthenticator(clock=lambda: NOW)
    header = f"Bearer {issued.access_token.reveal()}"

    async with foundation_session_factory() as session:
        assert (await authenticator.resolve(header, transaction=session)).organization_id == ORG_A
    async with session_scope(foundation_session_factory) as session:
        authorization = await session.scalar(
            select(AgentAuthorization).where(AgentAuthorization.id == AUTHORIZATION_A)
        )
        assert authorization is not None
        authorization.status = "revoked"
        authorization.revoked_at = NOW
        authorization.revision += 1
    async with foundation_session_factory() as session:
        with pytest.raises(MCPAuthenticationError):
            await authenticator.resolve(header, transaction=session)

    async with session_scope(foundation_session_factory) as session:
        authorization = await session.scalar(
            select(AgentAuthorization).where(AgentAuthorization.id == AUTHORIZATION_A)
        )
        membership = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.id == MEMBERSHIP_A
            )
        )
        assert authorization is not None and membership is not None
        authorization.status = "active"
        authorization.revoked_at = None
        membership.auth_epoch += 1
    async with foundation_session_factory() as session:
        with pytest.raises(MCPAuthenticationError):
            await authenticator.resolve(header, transaction=session)


async def test_raw_bearer_never_appears_in_authentication_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    issued = issue_agent_token(random_bytes=lambda size: b"f" * size)
    raw = issued.access_token.reveal()
    repository = RecordingResolver(None, [])

    with pytest.raises(MCPAuthenticationError) as raised:
        await MCPBearerAuthenticator(repository, clock=lambda: NOW).resolve(
            f"Bearer {raw}",
            transaction=object(),
        )

    assert raw not in str(raised.value)
    assert raw not in repr(raised.value)
    assert raw not in caplog.text
    assert repository.calls[0][0] == issued.token_digest
    assert raw not in repr(repository.calls)


def _authorization(
    identity: UUID,
    organization_id: UUID,
    user_id: UUID,
    agent_id: UUID,
    token_digest: str,
    *,
    scopes: list[str],
    membership_revision: int,
    auth_epoch: int,
    revision: int,
) -> AgentAuthorization:
    return AgentAuthorization(
        id=identity,
        organization_id=organization_id,
        user_id=user_id,
        agent_id=agent_id,
        scopes=scopes,
        membership_revision=membership_revision,
        auth_epoch=auth_epoch,
        token_digest=token_digest,
        status="active",
        expires_at=NOW + timedelta(hours=1),
        revoked_at=None,
        revision=revision,
    )


async def _seed_one(factory: AsyncSessionFactory, token_digest: str) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORG_A, slug="mcp-auth-one", name="MCP Auth One"),
                User(id=USER_A, display_name="MCP User", status="active"),
            ]
        )
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP_A,
                organization_id=ORG_A,
                user_id=USER_A,
                roles=["reviewer"],
                status="active",
                revision=2,
                auth_epoch=1,
            )
        )
        await session.flush()
        session.add(
            _authorization(
                AUTHORIZATION_A,
                ORG_A,
                USER_A,
                AGENT_A,
                token_digest,
                scopes=["courses:read", "reviews:read"],
                membership_revision=2,
                auth_epoch=1,
                revision=5,
            )
        )
