"""T078 RED integration contract for submission and artifact lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import boto3
import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.base import RequestResponseEndpoint
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.foundation_runtime import build_foundation_runtime
from review_platform.application.ports.providers import CONTRACT_VERSION, ProviderPayload
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    ExternalCredential,
    Homework,
    HomeworkVersion,
    Organization,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.infrastructure.providers.mocks import (
    FixtureArtifactProvider,
    FrozenFixtureStore,
    JsonSchemaPayloadValidator,
)
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
ORGANIZATION_ID = UUID("00000000-0000-7000-8000-000000000001")
USER_ID = UUID("00000000-0000-7000-8000-000000000302")
MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000303")
COURSE_ID = UUID("00000000-0000-7000-8000-000000000304")
COURSE_RUN_ID = UUID("00000000-0000-7000-8000-000000000305")
COURSE_MEMBERSHIP_ID = UUID("00000000-0000-7000-8000-000000000306")
HOMEWORK_ID = UUID("00000000-0000-7000-8000-000000000307")
HOMEWORK_VERSION_ID = UUID("00000000-0000-7000-8000-000000000308")
CRITERION_SET_ID = UUID("00000000-0000-7000-8000-000000000309")
CRITERION_ID = UUID("00000000-0000-7000-8000-000000000310")
COURSE_RUN_HOMEWORK_ID = UUID("00000000-0000-7000-8000-000000000311")
PUBLICATION_ID = UUID("00000000-0000-7000-8000-000000000312")
ARTIFACT_CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000021")
BUCKET = "submission-lifecycle"


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class DynamicArtifactProvider:
    contract_version = CONTRACT_VERSION
    schema_name = "artifact-provider.schema.json"

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        validator = JsonSchemaPayloadValidator()
        validator.validate(
            schema_name=self.schema_name,
            definition="preflight_request",
            payload=request,
        )
        fixture = FrozenFixtureStore().load("artifact-provider-v1.1.0.json")
        if str(request["url"]).endswith("/unavailable"):
            connected = {
                "contract_version": CONTRACT_VERSION,
                "organization_id": request["organization_id"],
                "provider": request["provider"],
                "read_capability": "unavailable",
                "feedback_capability": "requires_action",
                "locator": None,
                "error": {
                    "code": "access_denied",
                    "message": "Artifact is not accessible",
                    "retryable": False,
                    "action": "grant_read_access",
                },
            }
        else:
            connected = deepcopy(dict(fixture["available_result"]))
            connected["organization_id"] = request["organization_id"]
        validator.validate(
            schema_name=self.schema_name,
            definition="preflight_result",
            payload=cast(ProviderPayload, connected),
        )
        return cast(ProviderPayload, connected)

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        validator = JsonSchemaPayloadValidator()
        validator.validate(
            schema_name=self.schema_name,
            definition="capture_request",
            payload=request,
        )
        fixture = FrozenFixtureStore().load("artifact-provider-v1.1.0.json")
        result = deepcopy(dict(fixture["success_result"]))
        result["organization_id"] = request["organization_id"]
        result["artifact_reference_id"] = request["artifact_reference_id"]
        validator.validate(
            schema_name=self.schema_name,
            definition="capture_result",
            payload=cast(ProviderPayload, result),
        )
        return cast(ProviderPayload, result)


@dataclass(slots=True)
class SubmissionHarness:
    app: FastAPI
    client: AsyncClient
    session_factory: AsyncSessionFactory
    clock: MutableClock
    s3_client: Any


@pytest.fixture
async def submission_harness(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
    uuid7_factory: Callable[[], UUID],
) -> AsyncIterator[SubmissionHarness]:
    engine = create_database_engine(mysql_container.get_connection_url())
    await _reset_database(engine)
    session_factory = create_session_factory(engine)
    await _seed_published_homework(session_factory)
    endpoint = (
        f"http://{minio_container.get_container_host_ip()}:"
        f"{minio_container.get_exposed_port(9000)}"
    )
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="localminio",
        aws_secret_access_key="local-minio-only",
    )
    s3_client.create_bucket(Bucket=BUCKET)
    clock = MutableClock(NOW)
    settings = Settings(
        environment="test",
        database_url=mysql_container.get_connection_url(),
        redis_url=(
            f"redis://{redis_container.get_container_host_ip()}:"
            f"{redis_container.get_exposed_port(6379)}/0"
        ),
        s3_endpoint_url=endpoint,
        s3_bucket=BUCKET,
    )
    runtime = build_foundation_runtime(
        settings,
        session_factory=session_factory,
        s3_client=cast(S3Client, s3_client),
        id_factory=uuid7_factory,
        clock=clock,
    )
    app = create_app(settings, runtime=runtime)
    actor = RequestActor.user(
        organization_id=ORGANIZATION_ID,
        user_id=USER_ID,
        roles=["student"],
        membership_revision=0,
        auth_epoch=0,
    )
    app.state.artifact_provider = DynamicArtifactProvider()
    app.state.artifact_credential_binding_id = ARTIFACT_CREDENTIAL_ID
    app.state.artifact_credential_binding_version = 1

    @app.middleware("http")
    async def inject_student(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield SubmissionHarness(
            app=app,
            client=client,
            session_factory=session_factory,
            clock=clock,
            s3_client=s3_client,
        )
    await runtime.close()
    objects = s3_client.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    if objects:
        s3_client.delete_objects(
            Bucket=BUCKET,
            Delete={"Objects": [{"Key": item["Key"]} for item in objects]},
        )
    s3_client.delete_bucket(Bucket=BUCKET)
    await _reset_database(engine)
    await engine.dispose()


async def _reset_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)


async def _seed_published_homework(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORGANIZATION_ID, slug="submission-org", name="Submission Org"),
                User(id=USER_ID, display_name="Student"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_ID,
                    organization_id=ORGANIZATION_ID,
                    user_id=USER_ID,
                    roles=["student"],
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE_ID,
                    organization_id=ORGANIZATION_ID,
                    title="Submission Course",
                    description="",
                    source_kind="standalone",
                ),
                ExternalCredential(
                    id=ARTIFACT_CREDENTIAL_ID,
                    organization_id=ORGANIZATION_ID,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-github-credential",
                    key_id="local-key",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=COURSE_RUN_ID,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    title="Submission Run",
                    timezone="Europe/Moscow",
                    status="active",
                ),
                Homework(
                    id=HOMEWORK_ID,
                    organization_id=ORGANIZATION_ID,
                    course_id=COURSE_ID,
                    title="Submission Homework",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseMembership(
                    id=COURSE_MEMBERSHIP_ID,
                    organization_id=ORGANIZATION_ID,
                    course_run_id=COURSE_RUN_ID,
                    user_id=USER_ID,
                    kind="student",
                    source="imported",
                    status="active",
                    joined_at=NOW,
                ),
                HomeworkVersion(
                    id=HOMEWORK_VERSION_ID,
                    organization_id=ORGANIZATION_ID,
                    homework_id=HOMEWORK_ID,
                    version_number=1,
                    student_text="Submit a repository",
                    max_score=Decimal("10.00"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                ),
                CourseRunHomework(
                    id=COURSE_RUN_HOMEWORK_ID,
                    organization_id=ORGANIZATION_ID,
                    course_run_id=COURSE_RUN_ID,
                    homework_id=HOMEWORK_ID,
                    current_publication_id=None,
                    status="draft",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add(
            CriterionSet(
                id=CRITERION_SET_ID,
                organization_id=ORGANIZATION_ID,
                homework_version_id=HOMEWORK_VERSION_ID,
            )
        )
        await session.flush()
        session.add_all(
            [
                Criterion(
                    id=CRITERION_ID,
                    organization_id=ORGANIZATION_ID,
                    criterion_set_id=CRITERION_SET_ID,
                    stable_key="correctness",
                    position=0,
                    title="Correctness",
                    description="",
                    max_points=Decimal("10.00"),
                    active=True,
                ),
                CourseRunHomeworkPublication(
                    id=PUBLICATION_ID,
                    organization_id=ORGANIZATION_ID,
                    course_run_homework_id=COURSE_RUN_HOMEWORK_ID,
                    homework_id=HOMEWORK_ID,
                    homework_version_id=HOMEWORK_VERSION_ID,
                    publication_sequence=1,
                    submission_deadline=NOW + timedelta(days=1),
                    review_deadline=NOW + timedelta(days=2),
                    published_at=NOW,
                ),
            ]
        )
        await session.flush()
        relation = await session.get(CourseRunHomework, COURSE_RUN_HOMEWORK_ID)
        assert relation is not None
        relation.current_publication_id = PUBLICATION_ID
        relation.status = "active"
        relation.revision = 1


def _command(
    *,
    command_name: str,
    revision_target: str,
    target_id: UUID,
    expected_revision: int,
    payload: Mapping[str, Any],
    request_suffix: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_suffix:012d}",
        "idempotency_key": f"submission-lifecycle-{request_suffix:04d}",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


async def _preflight(
    harness: SubmissionHarness,
    artifact_url: str,
    request_suffix: int,
) -> Response:
    return await harness.client.post(
        f"/api/v1/course-run-homeworks/{COURSE_RUN_HOMEWORK_ID}/submissions/preflight",
        json=_command(
            command_name="preflight_submission",
            revision_target="course_run_homework",
            target_id=COURSE_RUN_HOMEWORK_ID,
            expected_revision=1,
            payload={"artifact_url": artifact_url},
            request_suffix=request_suffix,
        ),
    )


async def test_unavailable_preflight_returns_no_usable_artifact_reference(
    submission_harness: SubmissionHarness,
) -> None:
    request: ProviderPayload = {
        "contract_version": CONTRACT_VERSION,
        "organization_id": str(ORGANIZATION_ID),
        "provider": "github",
        "url": "https://github.com/example/unavailable",
        "credential_binding_id": str(ARTIFACT_CREDENTIAL_ID),
        "credential_binding_version": 1,
    }
    provider_result = await DynamicArtifactProvider().preflight(request)
    assert provider_result["read_capability"] == "unavailable"
    assert provider_result["locator"] is None

    response = await _preflight(
        submission_harness,
        "https://github.com/example/unavailable",
        320,
    )
    assert response.status_code == 200, (
        "US3 preflight route is not implemented; "
        f"received HTTP {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["read_capability"] == "unavailable"
    assert body["artifact_reference_id"] is None
    assert body["error"]["action"] == "grant_read_access"


async def test_submission_capture_replacement_late_open_and_promotion_recovery(
    submission_harness: SubmissionHarness,
) -> None:
    fixture = FrozenFixtureStore().load("artifact-provider-v1.1.0.json")
    assert await FixtureArtifactProvider().preflight(
        fixture["preflight_request"]
    ) == fixture["available_result"]
    assert await FixtureArtifactProvider().capture(
        fixture["capture_request"]
    ) == fixture["success_result"]

    preflight = await _preflight(
        submission_harness,
        "https://github.com/example/repository",
        321,
    )
    assert preflight.status_code == 200, (
        "US3 available preflight route is not implemented; "
        f"received HTTP {preflight.status_code}: {preflight.text}"
    )
    capability = preflight.json()
    submission_id = UUID(capability["submission_id"])
    artifact_reference_id = UUID(capability["artifact_reference_id"])

    async def submit(expected_revision: int, suffix: int) -> dict[str, Any]:
        response = await submission_harness.client.post(
            f"/api/v1/submissions/{submission_id}/versions",
            json=_command(
                command_name="submit_work",
                revision_target="submission",
                target_id=submission_id,
                expected_revision=expected_revision,
                payload={"artifact_reference_id": str(artifact_reference_id)},
                request_suffix=suffix,
            ),
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    first = await submit(capability["submission_revision"], 322)
    second = await submit(first["submission_revision"], 323)
    assert first["capture_operation_id"] != second["capture_operation_id"]

    tables = Base.metadata.tables
    assert {"artifact_promotion", "artifact_version", "submission_version"} <= set(tables)
    async with session_scope(submission_harness.session_factory) as session:
        promotion = (
            await session.execute(
                select(tables["artifact_promotion"]).where(
                    tables["artifact_promotion"].c.operation_id
                    == UUID(second["capture_operation_id"])
                )
            )
        ).mappings().one()
        assert promotion["state"] in {"staged", "db_committed", "promoting"}
        assert promotion["staged_key"] and promotion["final_key"]

    recovery = getattr(submission_harness.app.state, "artifact_promotion_recovery", None)
    assert callable(recovery), "US3 durable artifact promotion recovery is not composed"
    await cast(Callable[[], Awaitable[Any]], recovery)()

    submission_harness.clock.now = NOW + timedelta(days=1, seconds=1)
    late = await submit(second["submission_revision"], 324)
    history = await submission_harness.client.get(f"/api/v1/submissions/{submission_id}")
    assert history.status_code == 200, history.text
    versions = history.json()["versions"]
    assert [item["status"] for item in versions[:2]] == ["superseded", "pending_review"]
    assert versions[-1]["id"] == late["submission_version_id"]
    assert versions[-1]["phase"] == "revision"
    assert versions[-1]["status"] == "pending_review"
    assert history.json()["review_iterations"] == []

    async with session_scope(submission_harness.session_factory) as session:
        review_case = (
            await session.execute(
                select(tables["review_case"].c.id).where(
                    tables["review_case"].c.organization_id == ORGANIZATION_ID,
                    tables["review_case"].c.course_run_id == COURSE_RUN_ID,
                    tables["review_case"].c.homework_id == HOMEWORK_ID,
                    tables["review_case"].c.student_id == USER_ID,
                )
            )
        ).scalar_one()
    opened = await submission_harness.client.post(
        f"/api/v1/review-cases/{review_case}/iterations",
        json=_command(
            command_name="open_review_iteration",
            revision_target="review_case",
            target_id=review_case,
            expected_revision=0,
            payload={"submission_version_id": late["submission_version_id"]},
            request_suffix=325,
        ),
    )
    assert opened.status_code == 201, opened.text
    assert opened.json()["submission_version_id"] == late["submission_version_id"]

    cleanup = getattr(submission_harness.app.state, "staged_artifact_cleanup", None)
    assert callable(cleanup), "US3 intent-aware staged cleanup is not composed"
    await cast(Callable[[], Awaitable[Any]], cleanup)()
    assert submission_harness.s3_client.head_object(
        Bucket=BUCKET,
        Key=promotion["final_key"],
    )["ContentLength"] <= 128
