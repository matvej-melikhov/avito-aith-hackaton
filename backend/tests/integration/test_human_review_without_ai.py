"""T115 RED acceptance: a human publishes while AI is entirely unavailable."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import Request, Response
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import RequestResponseEndpoint
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.foundation_runtime import build_foundation_runtime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    ArtifactReference,
    ArtifactVersion,
    Course,
    CourseRun,
    CourseRunHomework,
    Criterion,
    CriterionSet,
    ExternalCredential,
    Homework,
    HomeworkVersion,
    Organization,
    OrganizationMembership,
    ReviewCase,
    ReviewIteration,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000001201")
USER = UUID("00000000-0000-7000-8000-000000001202")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000001203")
ITERATION = UUID("00000000-0000-7000-8000-000000001204")
CRITERION = UUID("00000000-0000-7000-8000-000000001205")
COURSE = UUID("00000000-0000-7000-8000-000000001206")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000001207")
HOMEWORK = UUID("00000000-0000-7000-8000-000000001208")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000001209")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000001210")
RUN_HOMEWORK = UUID("00000000-0000-7000-8000-000000001211")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000001212")
ARTIFACT_REFERENCE = UUID("00000000-0000-7000-8000-000000001213")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000001214")
SUBMISSION = UUID("00000000-0000-7000-8000-000000001215")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000001216")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000001217")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
async def human_review_client(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
    uuid7_factory: Callable[[], UUID],
    fixed_clock: Callable[[], datetime],
) -> AsyncIterator[AsyncClient]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORG, slug="human-without-ai", name="Human Without AI"),
                User(id=USER, display_name="Human Reviewer"),
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
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Human review course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-fixture",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Human review run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Human review homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=ARTIFACT_REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/human-review",
                    locator={"external_id": "example/human-review"},
                    read_capability="available",
                    feedback_capability="available",
                    last_checked_at=NOW,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                HomeworkVersion(
                    id=HOMEWORK_VERSION,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Submit the work",
                    max_score=Decimal("5"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                CourseRunHomework(
                    id=RUN_HOMEWORK,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
                ArtifactVersion(
                    id=ARTIFACT_VERSION,
                    organization_id=ORG,
                    artifact_reference_id=ARTIFACT_REFERENCE,
                    provider_version="commit:human-only",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG}/{ARTIFACT_VERSION}/artifact.zip",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW - timedelta(hours=1),
                    artifact_metadata={},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CriterionSet(
                    id=CRITERION_SET,
                    organization_id=ORG,
                    homework_version_id=HOMEWORK_VERSION,
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG,
                    course_run_homework_id=RUN_HOMEWORK,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=USER,
                    current_predeadline_version_id=None,
                    revision=0,
                ),
                ReviewCase(
                    id=REVIEW_CASE,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=USER,
                    current_iteration_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Criterion(
                    id=CRITERION,
                    organization_id=ORG,
                    criterion_set_id=CRITERION_SET,
                    stable_key="human-review",
                    position=0,
                    title="Human review",
                    description="Reviewed without AI",
                    max_points=Decimal("5"),
                    active=True,
                ),
                SubmissionVersion(
                    id=SUBMISSION_VERSION,
                    organization_id=ORG,
                    submission_id=SUBMISSION,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    sequence=1,
                    homework_version_id=HOMEWORK_VERSION,
                    artifact_reference_id=ARTIFACT_REFERENCE,
                    artifact_version_id=ARTIFACT_VERSION,
                    submitted_at=NOW - timedelta(hours=1),
                    effective_deadline=NOW + timedelta(hours=1),
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add(
            ReviewIteration(
                id=ITERATION,
                organization_id=ORG,
                review_case_id=REVIEW_CASE,
                course_run_id=COURSE_RUN,
                homework_id=HOMEWORK,
                student_id=USER,
                iteration_number=1,
                submission_version_id=SUBMISSION_VERSION,
                artifact_version_id=ARTIFACT_VERSION,
                homework_version_id=HOMEWORK_VERSION,
                criterion_set_id=CRITERION_SET,
                effective_deadline=NOW + timedelta(hours=1),
                responsible_reviewer_id=USER,
                status="in_review",
                current_revision_id=None,
                predecessor_iteration_id=None,
                origin="initial",
                revision=0,
            )
        )
        await session.flush()
        review_case = await session.get(ReviewCase, REVIEW_CASE)
        submission = await session.get(Submission, SUBMISSION)
        assert review_case is not None and submission is not None
        review_case.current_iteration_id = ITERATION
        submission.current_predeadline_version_id = SUBMISSION_VERSION
    settings = Settings(
        environment="test",
        database_url=mysql_container.get_connection_url(),
        redis_url=(
            f"redis://{redis_container.get_container_host_ip()}:"
            f"{redis_container.get_exposed_port(6379)}/0"
        ),
    )
    runtime = build_foundation_runtime(
        settings,
        session_factory=factory,
        s3_client=cast(S3Client, minio_container.get_client()),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )
    app = create_app(settings, runtime=runtime)
    # Deliberately do not configure an AI provider, component credential, run,
    # or fallback. Human editing/publication must remain independent.
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles=["reviewer"],
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_human(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield client
    await runtime.close()
    await engine.dispose()


def _command(
    *,
    name: str,
    expected_revision: int,
    payload: Mapping[str, Any],
    suffix: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{suffix:012d}",
        "idempotency_key": f"human-without-ai-{suffix:04d}",
        "command_name": name,
        "revision_target": "review_iteration",
        "target_id": str(ITERATION),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


async def test_human_can_save_and_publish_when_ai_component_is_unavailable(
    human_review_client: AsyncClient,
) -> None:
    saved = await human_review_client.post(
        f"/api/v1/review-iterations/{ITERATION}/revisions",
        json=_command(
            name="save_review_revision",
            expected_revision=0,
            payload={
                "feedback": "Human-only review",
                "criterion_decisions": [
                    {
                        "criterion_id": str(CRITERION),
                        "points": 5,
                        "decision": "manual",
                        "reason": "Reviewed directly by a human",
                        "evidence_ids": [],
                    }
                ],
                "review_notes": [],
            },
            suffix=1210,
        ),
    )
    assert saved.status_code == 201, (
        "US5 human save route is not implemented independently of AI; "
        f"received HTTP {saved.status_code}: {saved.text}"
    )
    revision_id = cast(str, saved.json()["review_revision_id"])

    published = await human_review_client.post(
        f"/api/v1/review-iterations/{ITERATION}/publish",
        json=_command(
            name="publish_review",
            expected_revision=saved.json()["review_iteration_revision"],
            payload={"review_revision_id": revision_id, "publication_request_id": None},
            suffix=1211,
        ),
    )
    assert published.status_code == 202, (
        "US5 human publication must not depend on AI availability; "
        f"received HTTP {published.status_code}: {published.text}"
    )
    assert published.json()["review_revision_id"] == revision_id
