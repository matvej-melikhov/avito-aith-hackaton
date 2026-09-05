"""T048: complete offline organization and course lifecycle on local infrastructure."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi import Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.middleware.base import RequestResponseEndpoint
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    JsonValue,
    ProviderPayload,
)
from review_platform.application.request_context import RequestActor
from review_platform.application.services.invitations import (
    InvitationEmailIntent,
    InvitationEmailIntentPort,
    InvitationSecretProtector,
)
from review_platform.bootstrap import build_bootstrap_command, execute_bootstrap_command
from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AuditEvent,
    Course,
    CourseMembership,
    CourseRun,
    ExternalCourseBinding,
    ExternalCredential,
    ExternalIdentity,
    Operation,
    Organization,
    OrganizationMembership,
    OutboxMessage,
    User,
)
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.infrastructure.providers.mocks import (
    FrozenFixtureStore,
    JsonSchemaPayloadValidator,
)
from review_platform.infrastructure.tasks.broker import RetryableTaskError
from review_platform.infrastructure.tasks.course_import import CourseImportTaskHandler
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000001")
FRESH_ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000099")
USER_ID = UUID("00000000-0000-7000-8000-000000000002")
IDENTITY_ID = UUID("00000000-0000-7000-8000-000000000003")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000004")
STEPIK_CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000011")
EMAIL_CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000042")
SEEDED_COURSE_ID = UUID("00000000-0000-7000-8000-000000000060")
SEEDED_COURSE_RUN_ID = UUID("00000000-0000-7000-8000-000000000061")
SEEDED_BINDING_ID = UUID("00000000-0000-7000-8000-000000000062")


class DynamicIdentityProvider:
    contract_version = CONTRACT_VERSION
    schema_name = "identity-provider.schema.json"

    def __init__(self) -> None:
        self._registry = ContractRegistry()

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload:
        self._registry.validate(
            request,
            self.schema_name,
            definition="verification_request",
        )
        state_id = request["state_id"]
        assert isinstance(state_id, str)
        assertion: dict[str, JsonValue] = {
            "contract_version": CONTRACT_VERSION,
            "provider": "stepik",
            "issuer": "https://stepik.org",
            "subject": "methodologist-fixture",
            "verified_email": "methodologist@example.com",
            "issued_at": NOW.isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "state_id": state_id,
        }
        self._registry.validate(
            assertion,
            self.schema_name,
            definition="identity_assertion",
        )
        return assertion


@dataclass(slots=True)
class DynamicCourseImportProvider:
    outcomes: list[str]
    requests: list[ProviderPayload] = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION
    schema_name: str = "course-import.schema.json"

    async def import_course_page(self, request: ProviderPayload) -> ProviderPayload:
        JsonSchemaPayloadValidator().validate(
            schema_name=self.schema_name,
            definition="request",
            payload=request,
        )
        self.requests.append(deepcopy(dict(request)))
        try:
            outcome = self.outcomes[len(self.requests) - 1]
        except IndexError:
            outcome = self.outcomes[-1]
        fixture = FrozenFixtureStore().load("course-import-v1.1.0.json")
        key = "failure_result" if outcome == "failure" else "success_result"
        result = deepcopy(dict(fixture[key]))
        result["organization_id"] = request["organization_id"]
        result["operation_id"] = request["operation_id"]
        JsonSchemaPayloadValidator().validate(
            schema_name=self.schema_name,
            definition="result",
            payload=cast(ProviderPayload, result),
        )
        return cast(ProviderPayload, result)


class DigestInvitationProtector(InvitationSecretProtector):
    def seal(self, magic_link: str) -> str:
        return f"sealed:sha256:{sha256(magic_link.encode()).hexdigest()}"


class SqlInvitationEmailIntents(InvitationEmailIntentPort):
    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    async def enqueue(self, intent: InvitationEmailIntent, *, transaction: object) -> None:
        if not isinstance(transaction, AsyncSession):
            raise TypeError("invitation transaction must be an AsyncSession")
        await OutboxService(
            OutboxMessageRepository(transaction),
            clock=self._clock,
        ).create(
            OutboxDraft(
                organization_id=intent.organization_id,
                message_id=intent.message_id,
                aggregate_type="invitation",
                aggregate_id=intent.invitation_id,
                event_type="InvitationEmailRequested",
                payload_version=CONTRACT_VERSION,
                payload={
                    "invitation_id": str(intent.invitation_id),
                    "recipient": intent.recipient,
                    "sealed_magic_link": intent.sealed_magic_link,
                    "expires_at": intent.expires_at.isoformat().replace("+00:00", "Z"),
                },
                available_at=self._clock(),
                max_attempts=5,
            )
        )


@dataclass(slots=True)
class US1Harness:
    client: AsyncClient
    runtime: Any
    session_factory: AsyncSessionFactory
    actor: RequestActor


@pytest.fixture
async def us1_harness(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
    uuid7_factory: Callable[[], UUID],
    fixed_clock: Callable[[], datetime],
) -> AsyncIterator[US1Harness]:
    engine = create_database_engine(mysql_container.get_connection_url())
    await _reset_database(engine)
    session_factory = create_session_factory(engine)
    await _seed_server_state(session_factory)
    actor = RequestActor.user(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        roles=["methodologist"],
        membership_revision=0,
        auth_epoch=0,
    )
    settings = Settings(
        environment="test",
        database_url=mysql_container.get_connection_url(),
        redis_url=(
            f"redis://{redis_container.get_container_host_ip()}:"
            f"{redis_container.get_exposed_port(6379)}/0"
        ),
        s3_endpoint_url=(
            f"http://{minio_container.get_container_host_ip()}:"
            f"{minio_container.get_exposed_port(9000)}"
        ),
    )
    from review_platform.application.foundation_runtime import build_foundation_runtime

    runtime = build_foundation_runtime(
        settings,
        session_factory=session_factory,
        s3_client=cast(S3Client, minio_container.get_client()),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )
    app = create_app(settings, runtime=runtime)
    app.state.installation_organization_id = ORGANIZATION_ID
    app.state.stepik_credential_binding_id = STEPIK_CREDENTIAL_ID
    app.state.stepik_credential_binding_version = 1
    app.state.stepik_redirect_uri = "https://review-platform.test/api/v1/auth/stepik/callback"
    app.state.stepik_pkce_verifier_ciphertext = "encrypted-pkce-fixture"
    app.state.stepik_authorization_url = "https://stepik.org/oauth2/authorize"
    app.state.identity_provider = DynamicIdentityProvider()
    app.state.course_import_credential_binding_id = STEPIK_CREDENTIAL_ID
    app.state.course_import_credential_binding_version = 1
    app.state.reviewer_magic_link_base_url = "https://review-platform.test/invite"
    app.state.invitation_secret_protector = DigestInvitationProtector()
    app.state.invitation_email_intents = SqlInvitationEmailIntents(clock=fixed_clock)

    @app.middleware("http")
    async def inject_request_actor(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
        follow_redirects=False,
    ) as client:
        yield US1Harness(
            client=client,
            runtime=runtime,
            session_factory=session_factory,
            actor=actor,
        )
    await runtime.close()
    await _reset_database(engine)
    await engine.dispose()


async def _reset_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


async def _seed_server_state(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORGANIZATION_ID, slug="main-org", name="Main Organization"),
                Organization(
                    id=FRESH_ORGANIZATION_ID,
                    slug="fresh-org",
                    name="Fresh Organization",
                ),
                User(id=USER_ID, display_name="Methodologist"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ExternalIdentity(
                    id=IDENTITY_ID,
                    user_id=USER_ID,
                    provider="stepik",
                    issuer="https://stepik.org",
                    subject="methodologist-fixture",
                    verified_email="methodologist@example.com",
                ),
                OrganizationMembership(
                    id=MEMBERSHIP_ID,
                    organization_id=ORGANIZATION_ID,
                    user_id=USER_ID,
                    roles=["methodologist"],
                    revision=0,
                    auth_epoch=0,
                ),
                ExternalCredential(
                    id=STEPIK_CREDENTIAL_ID,
                    organization_id=ORGANIZATION_ID,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-stepik-credential",
                    key_id="local-key",
                ),
                ExternalCredential(
                    id=EMAIL_CREDENTIAL_ID,
                    organization_id=ORGANIZATION_ID,
                    provider="smtp",
                    binding_version=1,
                    ciphertext="encrypted-email-credential",
                    key_id="local-key",
                ),
                Course(
                    id=SEEDED_COURSE_ID,
                    organization_id=ORGANIZATION_ID,
                    title="Seeded Course",
                    description="Archive/restore fixture",
                    source_kind="external",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=SEEDED_COURSE_RUN_ID,
                    organization_id=ORGANIZATION_ID,
                    course_id=SEEDED_COURSE_ID,
                    external_run_id="seed-run",
                    title="Seeded Run",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
                ExternalCourseBinding(
                    id=SEEDED_BINDING_ID,
                    organization_id=ORGANIZATION_ID,
                    course_id=SEEDED_COURSE_ID,
                    provider="stepik",
                    external_course_id="seed-course",
                    external_url="https://stepik.org/course/seed",
                    provider_version="seed-v1",
                    credential_id=STEPIK_CREDENTIAL_ID,
                    credential_binding_version=1,
                    binding_version=1,
                    status="active",
                    last_synced_at=NOW,
                ),
            ]
        )


def _command(
    *,
    command_name: str,
    revision_target: str,
    target_id: UUID,
    expected_revision: int,
    payload: Mapping[str, Any],
    request_suffix: int,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_suffix:012d}",
        "idempotency_key": idempotency_key,
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


async def test_fresh_installation_bootstrap_uses_local_operator_boundary(
    us1_harness: US1Harness,
) -> None:
    command = build_bootstrap_command(
        organization_id=FRESH_ORGANIZATION_ID,
        operator_id="local-installation-operator",
        operator_reason="activate exact first methodologist",
        provider="stepik",
        issuer="https://stepik.org",
        subject="fresh-methodologist",
        request_id=UUID("00000000-0000-7000-8000-000000000070"),
        trace_id=UUID("00000000-0000-7000-8000-000000000071"),
    )

    result = await execute_bootstrap_command(
        command,
        session_factory=us1_harness.session_factory,
    )

    assert result.organization_id == FRESH_ORGANIZATION_ID
    assert result.roles == ("methodologist",)
    assert result.organization_revision == 1
    async with session_scope(us1_harness.session_factory) as session:
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == FRESH_ORGANIZATION_ID,
                AuditEvent.action == "activate_bootstrap",
            )
        )
        assert audit is not None


async def test_dynamic_identity_assertion_establishes_current_session(
    us1_harness: US1Harness,
) -> None:
    start = await us1_harness.client.get("/api/v1/auth/stepik/start")
    assert start.status_code == 302
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    callback = await us1_harness.client.post(
        "/api/v1/auth/stepik/callback",
        params={"state": state, "code": "fixture-code"},
    )
    assert callback.status_code == 204
    assert "review_session" in us1_harness.client.cookies

    session = await us1_harness.client.get("/api/v1/session")
    assert session.status_code == 200
    assert session.json() == {
        "user_id": str(USER_ID),
        "organization_id": str(ORGANIZATION_ID),
        "membership_id": str(MEMBERSHIP_ID),
        "roles": ["methodologist"],
        "membership_revision": 0,
        "auth_epoch": 0,
        "actor_type": "user",
        "agent_id": None,
    }


async def test_reviewer_invitation_route_persists_sealed_email_intent(
    us1_harness: US1Harness,
) -> None:
    response = await us1_harness.client.post(
        "/api/v1/invitations",
        json=_command(
            command_name="create_invitation",
            revision_target="organization",
            target_id=ORGANIZATION_ID,
            expected_revision=0,
            payload={
                "email": "reviewer@example.com",
                "role": "reviewer",
                "expires_at": "2026-09-05T12:00:00Z",
            },
            request_suffix=20,
            idempotency_key="create-invitation-fixture-0001",
        ),
    )
    assert response.status_code == 201, response.text
    invitation_id = response.json()["id"]

    async with session_scope(us1_harness.session_factory) as session:
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.organization_id == ORGANIZATION_ID,
                OutboxMessage.aggregate_id == UUID(invitation_id),
                OutboxMessage.event_type == "InvitationEmailRequested",
            )
        )
        assert message is not None
        assert message.payload["recipient"] == "reviewer@example.com"
        assert str(message.payload["sealed_magic_link"]).startswith("sealed:sha256:")
        assert "token=" not in str(message.payload)


async def _start_course_import(client: AsyncClient) -> tuple[dict[str, Any], str]:
    command = _command(
        command_name="start_course_import",
        revision_target="organization",
        target_id=ORGANIZATION_ID,
        expected_revision=0,
        payload={"provider": "stepik", "external_url": "https://stepik.org/course/1"},
        request_suffix=30,
        idempotency_key="course-import-idempotent-fixture-0001",
    )
    response = await client.post("/api/v1/courses/imports", json=command)
    assert response.status_code == 202, response.text
    return command, response.json()["id"]


async def _course_import_message(
    factory: AsyncSessionFactory,
    operation_id: str,
) -> OutboxMessage:
    async with session_scope(factory) as session:
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.organization_id == ORGANIZATION_ID,
                OutboxMessage.aggregate_id == UUID(operation_id),
                OutboxMessage.event_type == "CourseImportRequested",
            )
        )
        assert message is not None
        return message


async def test_course_import_retry_runs_explicit_worker_and_exposes_attempt_history(
    us1_harness: US1Harness,
    uuid7_factory: Callable[[], UUID],
    fixed_clock: Callable[[], datetime],
) -> None:
    _, operation_id = await _start_course_import(us1_harness.client)
    message = await _course_import_message(us1_harness.session_factory, operation_id)
    provider = DynamicCourseImportProvider(["failure", "success"])
    handler = CourseImportTaskHandler(
        session_factory=us1_harness.session_factory,
        provider=provider,
        validator=JsonSchemaPayloadValidator(),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )

    with pytest.raises(RetryableTaskError):
        await handler(organization_id=str(ORGANIZATION_ID), message_id=str(message.message_id))
    completed = await handler(
        organization_id=str(ORGANIZATION_ID),
        message_id=str(message.message_id),
    )
    assert completed["state"] == "succeeded"

    operation = await us1_harness.client.get(f"/api/v1/operations/{operation_id}")
    assert operation.status_code == 200, operation.text
    assert [attempt["state"] for attempt in operation.json()["attempts"]] == [
        "retryable_failed",
        "succeeded",
    ]
    assert operation.json()["attempts"][0]["error"]["code"] == "provider_unavailable"


async def test_course_import_replay_has_one_operation_outbox_and_roster(
    us1_harness: US1Harness,
    uuid7_factory: Callable[[], UUID],
    fixed_clock: Callable[[], datetime],
) -> None:
    command, operation_id = await _start_course_import(us1_harness.client)
    replay = await us1_harness.client.post("/api/v1/courses/imports", json=command)
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == operation_id
    message = await _course_import_message(us1_harness.session_factory, operation_id)
    provider = DynamicCourseImportProvider(["success"])
    completed = await CourseImportTaskHandler(
        session_factory=us1_harness.session_factory,
        provider=provider,
        validator=JsonSchemaPayloadValidator(),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )(organization_id=str(ORGANIZATION_ID), message_id=str(message.message_id))
    assert completed["state"] == "succeeded"

    async with session_scope(us1_harness.session_factory) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Operation)
                .where(Operation.id == UUID(operation_id))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(
                    OutboxMessage.aggregate_id == UUID(operation_id),
                    OutboxMessage.event_type == "CourseImportRequested",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CourseMembership)
                .where(
                    CourseMembership.organization_id == ORGANIZATION_ID,
                    CourseMembership.source == "imported",
                )
            )
            == 1
        )
        import_audits = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.organization_id == ORGANIZATION_ID,
                    AuditEvent.action == "start_course_import",
                    AuditEvent.entity_id == UUID(operation_id),
                )
            )
        ).all()
        assert len(import_audits) == 1
        assert (
            import_audits[0].actor_user_id,
            import_audits[0].before_revision,
            import_audits[0].after_revision,
            import_audits[0].outcome,
        ) == (USER_ID, None, 0, "succeeded")
    course_run_id = cast(Sequence[str], completed["course_run_ids"])[0]
    roster = await us1_harness.client.get(f"/api/v1/course-runs/{course_run_id}/memberships")
    assert roster.status_code == 200, roster.text
    assert len(roster.json()["items"]) == 1


async def test_archive_restore_preserves_course_and_run_history(
    us1_harness: US1Harness,
) -> None:
    archive_course = await us1_harness.client.post(
        f"/api/v1/courses/{SEEDED_COURSE_ID}/archive",
        json=_command(
            command_name="archive_course",
            revision_target="course",
            target_id=SEEDED_COURSE_ID,
            expected_revision=0,
            payload={"reason": "course completed"},
            request_suffix=50,
            idempotency_key="archive-course-fixture-0001",
        ),
    )
    assert archive_course.status_code == 204, archive_course.text
    archived = await us1_harness.client.get("/api/v1/courses")
    assert archived.status_code == 200
    assert (
        next(item for item in archived.json()["items"] if item["id"] == str(SEEDED_COURSE_ID))[
            "status"
        ]
        == "archived"
    )

    restore_course = await us1_harness.client.post(
        f"/api/v1/courses/{SEEDED_COURSE_ID}/restore",
        json=_command(
            command_name="restore_course",
            revision_target="course",
            target_id=SEEDED_COURSE_ID,
            expected_revision=1,
            payload={},
            request_suffix=51,
            idempotency_key="restore-course-fixture-0001",
        ),
    )
    assert restore_course.status_code == 204, restore_course.text

    archive_run = await us1_harness.client.post(
        f"/api/v1/course-runs/{SEEDED_COURSE_RUN_ID}/archive",
        json=_command(
            command_name="archive_course_run",
            revision_target="course_run",
            target_id=SEEDED_COURSE_RUN_ID,
            expected_revision=0,
            payload={"reason": "cohort completed"},
            request_suffix=52,
            idempotency_key="archive-course-run-fixture-0001",
        ),
    )
    assert archive_run.status_code == 204, archive_run.text
    restore_run = await us1_harness.client.post(
        f"/api/v1/course-runs/{SEEDED_COURSE_RUN_ID}/restore",
        json=_command(
            command_name="restore_course_run",
            revision_target="course_run",
            target_id=SEEDED_COURSE_RUN_ID,
            expected_revision=1,
            payload={},
            request_suffix=53,
            idempotency_key="restore-course-run-fixture-0001",
        ),
    )
    assert restore_run.status_code == 204, restore_run.text

    history = await us1_harness.client.get("/api/v1/courses")
    runs = await us1_harness.client.get("/api/v1/course-runs")
    assert history.status_code == runs.status_code == 200
    assert (
        next(item for item in history.json()["items"] if item["id"] == str(SEEDED_COURSE_ID))[
            "status"
        ]
        == "active"
    )
    assert (
        next(item for item in runs.json()["items"] if item["id"] == str(SEEDED_COURSE_RUN_ID))[
            "status"
        ]
        == "active"
    )
