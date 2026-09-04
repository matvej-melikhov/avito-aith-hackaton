"""T048 RED acceptance wave for the organization and course lifecycle.

The suite uses only the local Foundation runtime, isolated T009 containers,
and schema-validated frozen provider fixtures.  Until US1 routes and services
are registered, HTTP scenarios fail with real 404 responses and bootstrap
fails with ``CommandNotRegistered`` rather than import/setup errors.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient, Response
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.command_bus import CommandBus
from review_platform.application.foundation_runtime import (
    FoundationRuntime,
    build_foundation_runtime,
)
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import Organization, OutboxMessage
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.infrastructure.providers.mocks import (
    FixtureCourseImportProvider,
    FixtureEmailProvider,
    FixtureIdentityProvider,
    FrozenFixtureStore,
)
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000001")
USER_ID = UUID("00000000-0000-7000-8000-000000000002")
COURSE_ID = UUID("00000000-0000-7000-8000-000000000003")
COURSE_RUN_ID = UUID("00000000-0000-7000-8000-000000000004")
INVITATION_ID = UUID("00000000-0000-7000-8000-000000000041")


@dataclass(slots=True)
class US1Harness:
    client: AsyncClient
    runtime: FoundationRuntime
    session_factory: AsyncSessionFactory
    redis_url: str


@pytest.fixture
async def us1_harness(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
) -> AsyncIterator[US1Harness]:
    database_url = mysql_container.get_connection_url()
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}/0"
    )
    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)
    await _reset_database(engine)
    async with session_scope(session_factory) as session:
        session.add(
            Organization(
                id=ORGANIZATION_ID,
                slug="fresh-installation",
                name="Fresh Installation",
            )
        )

    settings = Settings(
        environment="test",
        database_url=database_url,
        redis_url=redis_url,
        s3_endpoint_url=(
            f"http://{minio_container.get_container_host_ip()}:"
            f"{minio_container.get_exposed_port(9000)}"
        ),
    )
    runtime = build_foundation_runtime(
        settings,
        session_factory=session_factory,
        # US1 does not read artifacts.  Supplying the real isolated MinIO
        # client keeps composition local without creating a network stub.
        s3_client=cast(S3Client, minio_container.get_client()),
    )
    app = create_app(settings, runtime=runtime)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://review-platform.test",
    ) as client:
        yield US1Harness(
            client=client,
            runtime=runtime,
            session_factory=session_factory,
            redis_url=redis_url,
        )

    await runtime.close()
    await _reset_database(engine)
    await engine.dispose()


async def _reset_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


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


def _assert_status(response: Response, expected: int, capability: str) -> None:
    assert response.status_code == expected, (
        f"US1 capability is not implemented: {capability}; "
        f"received HTTP {response.status_code}: {response.text}"
    )


async def test_fresh_installation_activates_first_methodologist_via_operator_boundary(
    us1_harness: US1Harness,
) -> None:
    reason = "activate the exact bootstrap identity"
    command = us1_harness.runtime.bind_operator_command(
        command=_command(
            command_name="activate_bootstrap",
            revision_target="organization",
            target_id=ORGANIZATION_ID,
            expected_revision=0,
            payload={
                "external_identity": {
                    "provider": "stepik",
                    "issuer": "https://stepik.org",
                    "subject": "fixture-user",
                }
            },
            request_suffix=10,
            idempotency_key="activate-bootstrap-fixture-0001",
        ),
        organization_id=ORGANIZATION_ID,
        operator_id="local-installation-operator",
        reason=reason,
        trace_id=UUID("00000000-0000-7000-8000-000000000011"),
    )
    actor = RequestActor.installation_operator(
        organization_id=ORGANIZATION_ID,
        installation_operator_id="local-installation-operator",
        reason=reason,
    )
    command_bus = us1_harness.runtime.components()["command_bus"]
    assert isinstance(command_bus, CommandBus)

    result = await command_bus.dispatch(command, actor=actor)

    assert result is not None


async def test_frozen_identity_assertion_establishes_a_typed_session(
    us1_harness: US1Harness,
) -> None:
    fixtures = FrozenFixtureStore().load("identity-provider-v1.1.0.json")
    assertion = await FixtureIdentityProvider().verify_identity(fixtures["request"])
    assert assertion == fixtures["success_assertion"]

    start = await us1_harness.client.get("/api/v1/auth/stepik/start")
    _assert_status(start, 302, "Stepik OAuth state issuance")
    callback = await us1_harness.client.post(
        "/api/v1/auth/stepik/callback",
        params={
            "state_id": assertion["state_id"],
            "authorization_response": "fixture-code",
        },
    )
    _assert_status(callback, 204, "typed identity callback and session creation")

    session = await us1_harness.client.get("/api/v1/session")
    _assert_status(session, 200, "current typed session projection")
    body = session.json()
    assert body == {
        "user_id": str(USER_ID),
        "organization_id": str(ORGANIZATION_ID),
        "membership_id": body["membership_id"],
        "roles": ["methodologist"],
        "membership_revision": 0,
        "auth_epoch": 0,
        "actor_type": "user",
        "agent_id": None,
    }


async def test_reviewer_invitation_creates_a_durable_email_intent(
    us1_harness: US1Harness,
) -> None:
    email_fixture = FrozenFixtureStore().load("email-v1.1.0.json")
    accepted = await FixtureEmailProvider().send(email_fixture["request"])
    assert accepted == email_fixture["success_result"]

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
    _assert_status(response, 201, "reviewer invitation command")
    assert response.json()["id"]

    async with session_scope(us1_harness.session_factory) as session:
        messages = (
            await session.execute(
                select(OutboxMessage).where(
                    OutboxMessage.organization_id == ORGANIZATION_ID,
                )
            )
        ).scalars()
        assert any(
            message.payload.get("template") == "reviewer_magic_link"
            and message.payload.get("recipient") == "reviewer@example.com"
            for message in messages
        )


async def test_course_import_retry_exposes_ordered_attempts_and_sanitized_error(
    us1_harness: US1Harness,
) -> None:
    fixture = FrozenFixtureStore().load("course-import-v1.1.0.json")
    failed = await FixtureCourseImportProvider(outcome="failure_result").import_course_page(
        fixture["request"]
    )
    succeeded = await FixtureCourseImportProvider().import_course_page(fixture["request"])
    assert failed["error"] == fixture["failure_result"]["error"]
    assert succeeded == fixture["success_result"]

    response = await us1_harness.client.post(
        "/api/v1/courses/imports",
        json=_command(
            command_name="start_course_import",
            revision_target="organization",
            target_id=ORGANIZATION_ID,
            expected_revision=0,
            payload={"provider": "stepik", "external_url": "https://stepik.org/course/1"},
            request_suffix=30,
            idempotency_key="course-import-retry-fixture-0001",
        ),
    )
    _assert_status(response, 202, "course import orchestration")
    operation_id = response.json()["id"]

    operation = await us1_harness.client.get(f"/api/v1/operations/{operation_id}")
    _assert_status(operation, 200, "course import operation history")
    body = operation.json()
    assert body["kind"] == "course_import"
    assert body["state"] == "succeeded"
    assert [attempt["state"] for attempt in body["attempts"]] == [
        "retryable_failed",
        "succeeded",
    ]
    assert body["attempts"][0]["error"] == {
        "code": "provider_unavailable",
        "message": "Provider unavailable",
        "retryable": True,
        "action": None,
    }


async def test_replayed_course_import_upserts_roster_without_duplicates(
    us1_harness: US1Harness,
) -> None:
    command = _command(
        command_name="start_course_import",
        revision_target="organization",
        target_id=ORGANIZATION_ID,
        expected_revision=0,
        payload={"provider": "stepik", "external_url": "https://stepik.org/course/1"},
        request_suffix=40,
        idempotency_key="course-import-idempotent-fixture-0001",
    )
    first = await us1_harness.client.post("/api/v1/courses/imports", json=command)
    replay = await us1_harness.client.post("/api/v1/courses/imports", json=command)
    _assert_status(first, 202, "first course import")
    _assert_status(replay, 202, "idempotent course import replay")
    assert replay.json()["id"] == first.json()["id"]

    roster = await us1_harness.client.get(
        f"/api/v1/course-runs/{COURSE_RUN_ID}/memberships"
    )
    _assert_status(roster, 200, "imported roster projection")
    imported_students = [
        item
        for item in roster.json()["items"]
        if item["kind"] == "student" and item["source"] == "imported"
    ]
    assert imported_students == [
        {
            "user_id": imported_students[0]["user_id"],
            "kind": "student",
            "status": "active",
            "source": "imported",
        }
    ]


async def test_archive_restore_preserves_course_and_run_history(
    us1_harness: US1Harness,
) -> None:
    archive = await us1_harness.client.post(
        f"/api/v1/courses/{COURSE_ID}/archive",
        json=_command(
            command_name="archive_course",
            revision_target="course",
            target_id=COURSE_ID,
            expected_revision=0,
            payload={"reason": "course completed"},
            request_suffix=50,
            idempotency_key="archive-course-fixture-0001",
        ),
    )
    _assert_status(archive, 204, "course archive")

    archived = await us1_harness.client.get("/api/v1/courses")
    _assert_status(archived, 200, "archived course history")
    archived_course = next(
        item for item in archived.json()["items"] if item["id"] == str(COURSE_ID)
    )
    assert archived_course["status"] == "archived"
    assert any(
        run["id"] == str(COURSE_RUN_ID)
        for run in archived.json()["course_runs"]
    )

    restore = await us1_harness.client.post(
        f"/api/v1/courses/{COURSE_ID}/restore",
        json=_command(
            command_name="restore_course",
            revision_target="course",
            target_id=COURSE_ID,
            expected_revision=1,
            payload={},
            request_suffix=51,
            idempotency_key="restore-course-fixture-0001",
        ),
    )
    _assert_status(restore, 204, "course restore")

    restored = await us1_harness.client.get("/api/v1/courses")
    _assert_status(restored, 200, "restored course history")
    restored_course = next(
        item for item in restored.json()["items"] if item["id"] == str(COURSE_ID)
    )
    assert restored_course["status"] == "active"

    archive_run = await us1_harness.client.post(
        f"/api/v1/course-runs/{COURSE_RUN_ID}/archive",
        json=_command(
            command_name="archive_course_run",
            revision_target="course_run",
            target_id=COURSE_RUN_ID,
            expected_revision=0,
            payload={"reason": "cohort completed"},
            request_suffix=52,
            idempotency_key="archive-course-run-fixture-0001",
        ),
    )
    _assert_status(archive_run, 204, "course-run archive")
    restore_run = await us1_harness.client.post(
        f"/api/v1/course-runs/{COURSE_RUN_ID}/restore",
        json=_command(
            command_name="restore_course_run",
            revision_target="course_run",
            target_id=COURSE_RUN_ID,
            expected_revision=1,
            payload={},
            request_suffix=53,
            idempotency_key="restore-course-run-fixture-0001",
        ),
    )
    _assert_status(restore_run, 204, "course-run restore")

    run_history = await us1_harness.client.get("/api/v1/course-runs")
    _assert_status(run_history, 200, "restored course-run history")
    assert next(
        run for run in run_history.json()["items"] if run["id"] == str(COURSE_RUN_ID)
    )["status"] == "active"

    redis_client = Redis.from_url(us1_harness.redis_url, decode_responses=True)
    try:
        # No live/provider shortcut is allowed to leave an unscoped import key.
        assert not any(
            key.startswith("review-platform:course-import:")
            for key in redis_client.scan_iter(match="review-platform:*")
        )
    finally:
        redis_client.close()
