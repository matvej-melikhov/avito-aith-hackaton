"""Deterministic MySQL tests for bootstrap and authentication services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select
from testcontainers.mysql import MySqlContainer

from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.services.authentication import (
    AuthenticationService,
    IdentityEmailMismatch,
    ProtocolReplayRejected,
    SessionNotActive,
)
from review_platform.application.services.bootstrap import (
    BootstrapError,
    BootstrapService,
    InvalidBootstrapCommand,
)
from review_platform.contracts.commands import ApplicationCommand
from review_platform.contracts.registry import ContractRegistry
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AuditEvent,
    CommandReceipt,
    ExternalCredential,
    ExternalIdentity,
    Invitation,
    OAuthState,
    Organization,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.providers.mocks import FixtureIdentityProvider

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000000001")
ISSUER_USER = UUID("00000000-0000-7000-8000-000000000002")
ISSUER_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000000003")
STATE_ID = UUID("00000000-0000-7000-8000-000000000050")
CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000051")
STEP_USER = UUID("00000000-0000-7000-8000-000000000052")
STEP_IDENTITY = UUID("00000000-0000-7000-8000-000000000053")
STEP_MEMBERSHIP = UUID("00000000-0000-7000-8000-000000000054")
INVITATION_ID = UUID("00000000-0000-7000-8000-000000000055")
MAGIC_TOKEN = "offline-magic-token-000000000000000000000001"
STATE_SECRET = "offline-oauth-state-000000000000000000000001"
SESSION_SECRET = "offline-session-token-000000000000000000001"


class SequenceIds:
    def __init__(self, *, start: int = 1000, first: tuple[UUID, ...] = ()) -> None:
        self._next = start
        self._first = iter(first)

    def __call__(self) -> UUID:
        try:
            return next(self._first)
        except StopIteration:
            value = UUID(f"00000000-0000-7000-8000-{self._next:012d}")
            self._next += 1
            return value


@pytest.fixture
async def identity_factory(
    mysql_container: MySqlContainer,
) -> AsyncIterator[AsyncSessionFactory]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield create_session_factory(engine)
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _operator_command(
    command_name: str,
    *,
    request: int,
    expected_revision: int,
    subject: str,
) -> ApplicationCommand:
    payload: dict[str, object]
    if command_name == "activate_bootstrap":
        payload = {
            "external_identity": {
                "provider": "stepik",
                "issuer": "https://stepik.org",
                "subject": subject,
            }
        }
    else:
        payload = {
            "organization_id": str(ORG),
            "provider": "stepik",
            "issuer": "https://stepik.org",
            "subject": subject,
            "reason": "recover exact recipient",
        }
    return ApplicationCommand.model_validate(
        {
            "request_id": f"00000000-0000-7000-8000-{request:012d}",
            "idempotency_key": f"operator-{command_name}-{request:08d}",
            "command_name": command_name,
            "revision_target": "organization",
            "target_id": str(ORG),
            "expected_revision": expected_revision,
            "payload": payload,
            "organization_id": str(ORG),
            "actor": {
                "type": "installation_operator",
                "installation_operator_id": "local-operator",
                "reason": "authorized local operation",
            },
            "transport": "operator",
            "trace_id": f"00000000-0000-7000-8000-{request + 100:012d}",
        }
    )


async def _seed_organization(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(Organization(id=ORG, slug="service-org", name="Service Org", revision=0))


async def _seed_issuer(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=ISSUER_USER, display_name="Issuer"))
        await session.flush()
        session.add(
            OrganizationMembership(
                id=ISSUER_MEMBERSHIP,
                organization_id=ORG,
                user_id=ISSUER_USER,
                roles=["methodologist"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )


async def test_bootstrap_has_one_winner_and_rejects_non_operator_context(
    identity_factory: AsyncSessionFactory,
) -> None:
    await _seed_organization(identity_factory)
    ids = SequenceIds(start=1100)
    outcomes: list[str] = []

    async def activate(request: int) -> None:
        command = _operator_command(
            "activate_bootstrap",
            request=request,
            expected_revision=0,
            subject="first-methodologist",
        )
        try:
            async with session_scope(identity_factory) as session:
                await BootstrapService(
                    session,
                    id_factory=ids,
                    clock=lambda: NOW,
                ).activate(command)
        except BootstrapError:
            outcomes.append("rejected")
        else:
            outcomes.append("activated")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(activate, 101)
        tasks.start_soon(activate, 102)
    assert sorted(outcomes) == ["activated", "rejected"]

    valid = _operator_command(
        "activate_bootstrap",
        request=103,
        expected_revision=1,
        subject="another-user",
    )
    forged = valid.model_copy(update={"transport": "rest"})
    async with identity_factory() as session:
        with pytest.raises(InvalidBootstrapCommand, match="installation_operator"):
            await BootstrapService(session, id_factory=ids, clock=lambda: NOW).activate(forged)

    async with identity_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(ExternalIdentity)) == 1
        memberships = (await session.scalars(select(OrganizationMembership))).all()
        assert len(memberships) == 1
        assert memberships[0].roles == ["methodologist"]

    async with session_scope(identity_factory) as session:
        membership = await session.scalar(select(OrganizationMembership))
        assert membership is not None
        membership.status = "archived"
    repeat = _operator_command(
        "activate_bootstrap",
        request=104,
        expected_revision=1,
        subject="replacement-methodologist",
    )
    with pytest.raises(BootstrapError, match="already activated"):
        async with session_scope(identity_factory) as session:
            await BootstrapService(session, id_factory=ids, clock=lambda: NOW).activate(repeat)


async def test_recovery_authority_replays_once_and_records_operator_audit(
    identity_factory: AsyncSessionFactory,
) -> None:
    await _seed_organization(identity_factory)
    ids = SequenceIds(start=1200)
    command = _operator_command(
        "recover_methodologist",
        request=201,
        expected_revision=0,
        subject="recovered-methodologist",
    )
    async with session_scope(identity_factory) as session:
        first = await BootstrapService(
            session,
            id_factory=ids,
            clock=lambda: NOW,
        ).recover(command)
    async with session_scope(identity_factory) as session:
        replay = await BootstrapService(
            session,
            id_factory=ids,
            clock=lambda: NOW,
        ).recover(command)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.membership_id == first.membership_id
    async with identity_factory() as session:
        audits = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "recover_methodologist")
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].installation_operator_id == "local-operator"
        assert audits[0].sanitized_details["reason"] == "recover exact recipient"
        receipt = await session.scalar(select(CommandReceipt))
        assert receipt is not None
        assert receipt.status == "succeeded"
        assert receipt.actor_snapshot["type"] == "installation_operator"


@dataclass(slots=True)
class CountingStepikProvider:
    delegate: FixtureIdentityProvider = field(default_factory=FixtureIdentityProvider)
    calls: int = 0

    @property
    def contract_version(self) -> str:
        return self.delegate.contract_version

    @property
    def schema_name(self) -> str:
        return self.delegate.schema_name

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload:
        self.calls += 1
        return await self.delegate.verify_identity(request)


async def _seed_stepik_login(factory: AsyncSessionFactory) -> None:
    await _seed_organization(factory)
    async with session_scope(factory) as session:
        session.add(User(id=STEP_USER, display_name="Stepik User"))
        await session.flush()
        session.add_all(
            [
                ExternalIdentity(
                    id=STEP_IDENTITY,
                    user_id=STEP_USER,
                    provider="stepik",
                    issuer="https://stepik.org",
                    subject="fixture-user",
                    verified_email="user@example.com",
                ),
                OrganizationMembership(
                    id=STEP_MEMBERSHIP,
                    organization_id=ORG,
                    user_id=STEP_USER,
                    roles=["methodologist"],
                    status="active",
                    revision=3,
                    auth_epoch=2,
                ),
                ExternalCredential(
                    id=CREDENTIAL_ID,
                    organization_id=ORG,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-stepik-fixture",
                    key_id="fixture-key",
                ),
            ]
        )


async def test_oauth_replay_creates_one_session_and_logout_is_idempotent(
    identity_factory: AsyncSessionFactory,
) -> None:
    await _seed_stepik_login(identity_factory)
    ids = SequenceIds(first=(STATE_ID,), start=1300)
    provider = CountingStepikProvider()
    kwargs = {
        "identity_provider": provider,
        "id_factory": ids,
        "clock": lambda: NOW,
        "state_secret_factory": lambda: STATE_SECRET,
        "session_secret_factory": lambda: SESSION_SECRET,
    }
    async with session_scope(identity_factory) as session:
        start = await AuthenticationService(session, **kwargs).start_stepik_oauth(
            organization_id=ORG,
            credential_binding_id=CREDENTIAL_ID,
            credential_binding_version=1,
            redirect_uri="https://review.example.test/api/v1/auth/stepik/callback",
            pkce_verifier_ciphertext="encrypted-pkce-fixture",
        )
    assert start.state_id == STATE_ID
    assert start.state_secret == STATE_SECRET

    async with session_scope(identity_factory) as session:
        first = await AuthenticationService(session, **kwargs).complete_stepik_oauth(
            organization_id=ORG,
            state_secret=STATE_SECRET,
            authorization_response="fixture-code",
        )
    async with session_scope(identity_factory) as session:
        replay = await AuthenticationService(session, **kwargs).complete_stepik_oauth(
            organization_id=ORG,
            state_secret=STATE_SECRET,
            authorization_response="fixture-code",
        )
    assert first.replayed is False and first.session_secret == SESSION_SECRET
    assert replay.replayed is True and replay.session_secret is None
    assert replay.session_id == first.session_id
    assert provider.calls == 1
    async with identity_factory() as session:
        state = await session.get(OAuthState, STATE_ID)
        assert state is not None
        assert state.state_digest == sha256_digest(STATE_SECRET)
        assert STATE_SECRET not in repr(state.__dict__)

    async with session_scope(identity_factory) as session:
        view = await AuthenticationService(session, **kwargs).current_session(
            organization_id=ORG,
            session_secret=SESSION_SECRET,
        )
    assert (view.membership_revision, view.auth_epoch, view.roles) == (
        3,
        2,
        ("methodologist",),
    )

    logout_results = []
    for _ in range(2):
        async with session_scope(identity_factory) as session:
            logout_results.append(
                await AuthenticationService(session, **kwargs).logout_current_session(
                    organization_id=ORG,
                    session_secret=SESSION_SECRET,
                )
            )
    assert [result.revoked for result in logout_results] == [True, False]
    async with identity_factory() as session:
        stored = (await session.scalars(select(Session))).all()
        assert len(stored) == 1
        assert stored[0].token_digest == sha256_digest(SESSION_SECRET)
        assert SESSION_SECRET not in repr(stored[0].__dict__)


@dataclass(slots=True)
class EmailAssertionProvider:
    verified_email: str
    registry: ContractRegistry = field(default_factory=ContractRegistry)

    @property
    def contract_version(self) -> str:
        return "1.1.0"

    @property
    def schema_name(self) -> str:
        return "identity-provider.schema.json"

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload:
        self.registry.validate(request, self.schema_name, definition="verification_request")
        assertion: ProviderPayload = {
            "contract_version": "1.1.0",
            "provider": "email_magic_link",
            "issuer": "https://review.example.test",
            "subject": "invited-reviewer",
            "verified_email": self.verified_email,
            "issued_at": "2026-09-04T12:00:00Z",
            "expires_at": "2026-09-04T12:05:00Z",
            "state_id": str(STATE_ID),
        }
        self.registry.validate(assertion, self.schema_name, definition="identity_assertion")
        return assertion


async def _seed_magic_link(factory: AsyncSessionFactory, *, email: str) -> None:
    await _seed_organization(factory)
    await _seed_issuer(factory)
    async with session_scope(factory) as session:
        session.add(
            ExternalCredential(
                id=CREDENTIAL_ID,
                organization_id=ORG,
                provider="email_magic_link",
                binding_version=1,
                ciphertext="encrypted-email-fixture",
                key_id="fixture-key",
            )
        )
        await session.flush()
        session.add(
            Invitation(
                id=INVITATION_ID,
                organization_id=ORG,
                role="reviewer",
                normalized_email=email,
                token_digest=sha256_digest(MAGIC_TOKEN),
                expires_at=NOW + timedelta(hours=1),
                issued_by=ISSUER_USER,
                status="active",
                revision=0,
            )
        )
        state = await AuthenticationService(
            session,
            identity_provider=EmailAssertionProvider(email),
            id_factory=SequenceIds(first=(STATE_ID,)),
            clock=lambda: NOW,
        ).issue_reviewer_magic_link_state(
            organization_id=ORG,
            credential_binding_id=CREDENTIAL_ID,
            credential_binding_version=1,
            redirect_uri="https://review.example.test/auth/callback",
            state_authority_ciphertext="encrypted-state-fixture",
        )
        assert state.state_id == STATE_ID


async def test_magic_link_rejects_mismatched_verified_email_atomically(
    identity_factory: AsyncSessionFactory,
) -> None:
    await _seed_magic_link(identity_factory, email="invited@example.com")
    with pytest.raises(IdentityEmailMismatch):
        async with session_scope(identity_factory) as session:
            await AuthenticationService(
                session,
                identity_provider=EmailAssertionProvider("different@example.com"),
                id_factory=SequenceIds(start=1400),
                clock=lambda: NOW,
                session_secret_factory=lambda: SESSION_SECRET,
            ).consume_reviewer_magic_link(
                organization_id=ORG,
                invitation_token=MAGIC_TOKEN,
                state_id=STATE_ID,
            )

    async with identity_factory() as session:
        invitation = await session.get(Invitation, INVITATION_ID)
        assert invitation is not None and invitation.status == "active"
        assert await session.scalar(select(func.count()).select_from(Session)) == 0
        assert await session.scalar(select(func.count()).select_from(ExternalIdentity)) == 0


async def test_magic_link_double_consume_has_one_session_and_membership(
    identity_factory: AsyncSessionFactory,
) -> None:
    await _seed_magic_link(identity_factory, email="reviewer@example.com")
    provider = EmailAssertionProvider("Reviewer@Example.com")
    ids = SequenceIds(start=1500)
    outcomes: list[str] = []

    async def consume() -> None:
        try:
            async with session_scope(identity_factory) as session:
                await AuthenticationService(
                    session,
                    identity_provider=provider,
                    id_factory=ids,
                    clock=lambda: NOW,
                    session_secret_factory=lambda: SESSION_SECRET,
                ).consume_reviewer_magic_link(
                    organization_id=ORG,
                    invitation_token=MAGIC_TOKEN,
                    state_id=STATE_ID,
                )
        except ProtocolReplayRejected:
            outcomes.append("rejected")
        else:
            outcomes.append("consumed")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        tasks.start_soon(consume)
    assert sorted(outcomes) == ["consumed", "rejected"]

    async with identity_factory() as session:
        invitation = await session.get(Invitation, INVITATION_ID)
        assert invitation is not None and invitation.status == "consumed"
        assert await session.scalar(select(func.count()).select_from(Session)) == 1
        reviewer_memberships = (
            await session.scalars(
                select(OrganizationMembership).where(
                    OrganizationMembership.user_id != ISSUER_USER
                )
            )
        ).all()
        assert len(reviewer_memberships) == 1
        assert reviewer_memberships[0].roles == ["reviewer"]


async def test_stale_auth_epoch_invalidates_current_session(
    identity_factory: AsyncSessionFactory,
) -> None:
    await _seed_stepik_login(identity_factory)
    async with session_scope(identity_factory) as session:
        session.add(
            Session(
                id=UUID("00000000-0000-7000-8000-000000001601"),
                organization_id=ORG,
                user_id=STEP_USER,
                membership_id=STEP_MEMBERSHIP,
                membership_revision=3,
                auth_epoch=1,
                token_digest=sha256_digest(SESSION_SECRET),
                expires_at=NOW + timedelta(hours=1),
            )
        )
    with pytest.raises(SessionNotActive, match="epoch"):
        async with session_scope(identity_factory) as session:
            await AuthenticationService(
                session,
                identity_provider=CountingStepikProvider(),
                clock=lambda: NOW,
            ).current_session(
                organization_id=ORG,
                session_secret=SESSION_SECRET,
            )
