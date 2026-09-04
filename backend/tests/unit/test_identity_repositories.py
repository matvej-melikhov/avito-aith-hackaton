"""Tenant isolation and one-time transition tests for identity repositories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from inspect import getmembers, getsource, iscoroutinefunction, signature
from uuid import UUID

import anyio
import pytest
from sqlalchemy import delete
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    ExternalCredential,
    ExternalIdentity,
    Invitation,
    OAuthState,
    Organization,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.repositories.identity import (
    AgentAuthorizationRepository,
    ExternalCredentialRepository,
    InvitationRepository,
    OAuthStateRepository,
    OrganizationMembershipRepository,
    SessionRepository,
    UserIdentityRepository,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)

ORG_A = UUID("00000000-0000-7000-8000-000000000951")
ORG_B = UUID("00000000-0000-7000-8000-000000000952")
USER_ID = UUID("00000000-0000-7000-8000-000000000953")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000954")
INVITATION_ID = UUID("00000000-0000-7000-8000-000000000955")
SESSION_ID = UUID("00000000-0000-7000-8000-000000000956")
OAUTH_STATE_ID = UUID("00000000-0000-7000-8000-000000000957")
AUTHORIZATION_ID = UUID("00000000-0000-7000-8000-000000000958")
CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000959")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
INVITATION_DIGEST = "sha256:" + "1" * 64
SESSION_DIGEST = "sha256:" + "2" * 64
STATE_DIGEST = "sha256:" + "3" * 64
AUTHORIZATION_DIGEST = "sha256:" + "4" * 64


def test_every_tenant_lookup_requires_an_explicit_organization() -> None:
    tenant_methods = (
        OrganizationMembershipRepository.get,
        OrganizationMembershipRepository.get_by_user,
        OrganizationMembershipRepository.list_for_organization,
        OrganizationMembershipRepository.lock_users,
        OrganizationMembershipRepository.compare_and_set,
        InvitationRepository.get,
        InvitationRepository.get_by_token_digest,
        InvitationRepository.list_for_organization,
        InvitationRepository.consume_once,
        InvitationRepository.revoke_once,
        OAuthStateRepository.get,
        OAuthStateRepository.get_by_state_digest,
        OAuthStateRepository.consume_once,
        SessionRepository.get,
        SessionRepository.get_by_token_digest,
        SessionRepository.list_for_user,
        SessionRepository.revoke_once,
        SessionRepository.revoke_for_membership,
        AgentAuthorizationRepository.get,
        AgentAuthorizationRepository.get_by_token_digest,
        AgentAuthorizationRepository.list_for_user,
        AgentAuthorizationRepository.compare_and_set,
        AgentAuthorizationRepository.revoke_once,
        AgentAuthorizationRepository.revoke_for_user,
        ExternalCredentialRepository.get_exact,
        ExternalCredentialRepository.list_versions,
        ExternalCredentialRepository.revoke_exact,
    )
    for method in tenant_methods:
        parameter = signature(method).parameters["organization_id"]
        assert parameter.default is parameter.empty


def test_repository_api_never_accepts_plaintext_tokens_or_credentials() -> None:
    repository_classes = (
        UserIdentityRepository,
        OrganizationMembershipRepository,
        InvitationRepository,
        OAuthStateRepository,
        SessionRepository,
        AgentAuthorizationRepository,
        ExternalCredentialRepository,
    )
    source = "\n".join(getsource(repository) for repository in repository_classes)

    assert ".commit(" not in source
    assert "access_token" not in source
    assert "refresh_token" not in source
    forbidden_parameters = {"token", "secret", "plaintext", "credential_plaintext"}
    for repository in repository_classes:
        for _, method in getmembers(repository, predicate=iscoroutinefunction):
            assert forbidden_parameters.isdisjoint(signature(method).parameters)


async def _race(call: Callable[[], Awaitable[bool]]) -> list[bool]:
    outcomes: list[bool] = []

    async def invoke() -> None:
        outcomes.append(await call())

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(invoke)
        task_group.start_soon(invoke)
    return outcomes


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORG_A, slug="repository-org-a", name="Repository Org A"),
                Organization(id=ORG_B, slug="repository-org-b", name="Repository Org B"),
                User(id=USER_ID, display_name="Repository User"),
            ]
        )
        await session.flush()
        await OrganizationMembershipRepository(session).add(
            OrganizationMembership(
                id=MEMBERSHIP_ID,
                organization_id=ORG_A,
                user_id=USER_ID,
                roles=["methodologist"],
            )
        )
        await ExternalCredentialRepository(session).add(
            ExternalCredential(
                id=CREDENTIAL_ID,
                organization_id=ORG_A,
                provider="stepik",
                binding_version=1,
                ciphertext="encrypted-fixture",
                key_id="key-fixture",
            )
        )
        await UserIdentityRepository(session).add_external_identity(
            ExternalIdentity(
                id=UUID("00000000-0000-7000-8000-000000000960"),
                user_id=USER_ID,
                provider="stepik",
                issuer="https://stepik.org",
                subject="repository-user",
                verified_email="user@example.com",
            )
        )
        await InvitationRepository(session).add(
            Invitation(
                id=INVITATION_ID,
                organization_id=ORG_A,
                role="reviewer",
                normalized_email="reviewer@example.com",
                token_digest=INVITATION_DIGEST,
                expires_at=NOW + timedelta(hours=1),
                issued_by=USER_ID,
            )
        )
        await SessionRepository(session).add(
            Session(
                id=SESSION_ID,
                organization_id=ORG_A,
                user_id=USER_ID,
                membership_id=MEMBERSHIP_ID,
                membership_revision=0,
                auth_epoch=0,
                token_digest=SESSION_DIGEST,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        await AgentAuthorizationRepository(session).add(
            AgentAuthorization(
                id=AUTHORIZATION_ID,
                organization_id=ORG_A,
                user_id=USER_ID,
                agent_id=UUID("00000000-0000-7000-8000-000000000961"),
                scopes=["courses:read"],
                membership_revision=0,
                auth_epoch=0,
                token_digest=AUTHORIZATION_DIGEST,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        await OAuthStateRepository(session).add(
            OAuthState(
                state_id=OAUTH_STATE_ID,
                organization_id=ORG_A,
                state_digest=STATE_DIGEST,
                provider="stepik",
                credential_binding_id=CREDENTIAL_ID,
                credential_binding_version=1,
                redirect_uri="https://review.example.test/callback",
                pkce_verifier_ciphertext="encrypted-pkce-fixture",
                expires_at=NOW + timedelta(minutes=5),
            )
        )


async def _cleanup(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        for model in (
            OAuthState,
            AgentAuthorization,
            Session,
            Invitation,
            OrganizationMembership,
            ExternalCredential,
        ):
            await session.execute(delete(model).where(model.organization_id.in_((ORG_A, ORG_B))))
        await session.execute(delete(ExternalIdentity).where(ExternalIdentity.user_id == USER_ID))
        await session.execute(delete(User).where(User.id == USER_ID))
        await session.execute(delete(Organization).where(Organization.id.in_((ORG_A, ORG_B))))


@pytest.mark.infrastructure
@pytest.mark.anyio
async def test_mysql_tenant_isolation_cas_and_one_time_races(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    factory = create_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        await _seed(factory)

        async with session_scope(factory) as session:
            assert await OrganizationMembershipRepository(session).get(ORG_B, MEMBERSHIP_ID) is None
            assert await InvitationRepository(session).get(ORG_B, INVITATION_ID) is None
            assert await OAuthStateRepository(session).get(ORG_B, OAUTH_STATE_ID) is None
            assert await SessionRepository(session).get(ORG_B, SESSION_ID) is None
            assert (
                await AgentAuthorizationRepository(session).get(ORG_B, AUTHORIZATION_ID) is None
            )
            assert (
                await ExternalCredentialRepository(session).get_exact(
                    ORG_B,
                    CREDENTIAL_ID,
                    1,
                )
                is None
            )
            credential = await ExternalCredentialRepository(session).get_exact(
                ORG_A,
                CREDENTIAL_ID,
                1,
                status="active",
                for_update=True,
            )
            assert credential is not None
            assert credential.ciphertext == "encrypted-fixture"
            assert await UserIdentityRepository(session).get_external_identity(
                provider="stepik",
                issuer="https://stepik.org",
                subject="repository-user",
                for_update=True,
            ) is not None
            assert await OrganizationMembershipRepository(session).compare_and_set(
                ORG_B,
                MEMBERSHIP_ID,
                expected_revision=0,
                roles=["reviewer"],
                increment_auth_epoch=True,
            ) is False
            assert await OrganizationMembershipRepository(session).compare_and_set(
                ORG_A,
                MEMBERSHIP_ID,
                expected_revision=0,
                roles=["methodologist", "reviewer"],
                increment_auth_epoch=True,
            ) is True

        async def consume_invitation() -> bool:
            async with session_scope(factory) as session:
                return await InvitationRepository(session).consume_once(
                    ORG_A,
                    INVITATION_ID,
                    token_digest=INVITATION_DIGEST,
                    expected_revision=0,
                    consumed_by=USER_ID,
                    consumed_at=NOW,
                )

        async def consume_oauth_state() -> bool:
            async with session_scope(factory) as session:
                return await OAuthStateRepository(session).consume_once(
                    ORG_A,
                    OAUTH_STATE_ID,
                    state_digest=STATE_DIGEST,
                    consumed_at=NOW,
                    resulting_session_id=SESSION_ID,
                )

        async def revoke_session() -> bool:
            async with session_scope(factory) as session:
                return await SessionRepository(session).revoke_once(
                    ORG_A,
                    SESSION_ID,
                    revoked_at=NOW,
                )

        async def revoke_authorization() -> bool:
            async with session_scope(factory) as session:
                return await AgentAuthorizationRepository(session).revoke_once(
                    ORG_A,
                    AUTHORIZATION_ID,
                    expected_revision=0,
                    revoked_at=NOW,
                )

        assert sorted(await _race(consume_invitation)) == [False, True]
        assert sorted(await _race(consume_oauth_state)) == [False, True]
        assert sorted(await _race(revoke_session)) == [False, True]
        assert sorted(await _race(revoke_authorization)) == [False, True]

        async with session_scope(factory) as session:
            membership = await OrganizationMembershipRepository(session).get(
                ORG_A,
                MEMBERSHIP_ID,
            )
            invitation = await InvitationRepository(session).get(ORG_A, INVITATION_ID)
            oauth_state = await OAuthStateRepository(session).get(ORG_A, OAUTH_STATE_ID)
            session_row = await SessionRepository(session).get(ORG_A, SESSION_ID)
            authorization = await AgentAuthorizationRepository(session).get(
                ORG_A,
                AUTHORIZATION_ID,
            )
            assert membership is not None
            assert (membership.revision, membership.auth_epoch) == (1, 1)
            assert invitation is not None
            assert (invitation.status, invitation.revision) == ("consumed", 1)
            assert oauth_state is not None
            assert oauth_state.resulting_session_id == SESSION_ID
            assert session_row is not None and session_row.status == "revoked"
            assert authorization is not None
            assert (authorization.status, authorization.revision) == ("revoked", 1)
    finally:
        await _cleanup(factory)
        await engine.dispose()
