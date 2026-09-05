from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import select
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.projections.review_detail import (
    ArtifactDownloadGrant,
    read_review_detail,
)
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.models.publication import ReviewPublication
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
REVIEWER = UUID("00000000-0000-7000-8000-000000050001")
STUDENT = UUID("00000000-0000-7000-8000-000000050002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000050003")
COURSE = UUID("00000000-0000-7000-8000-000000050004")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000050005")
HOMEWORK = UUID("00000000-0000-7000-8000-000000050006")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000050007")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000050008")
CRITERION = UUID("00000000-0000-7000-8000-000000050009")
RELATION = UUID("00000000-0000-7000-8000-000000050010")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000050011")
REFERENCE = UUID("00000000-0000-7000-8000-000000050012")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000050013")
CAPTURE_OPERATION = UUID("00000000-0000-7000-8000-000000050014")
SUBMISSION = UUID("00000000-0000-7000-8000-000000050015")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000050016")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000050017")
PUBLISHED_ITERATION = UUID("00000000-0000-7000-8000-000000050018")
SUCCESSOR_ITERATION = UUID("00000000-0000-7000-8000-000000050019")
PUBLISHED_REVISION = UUID("00000000-0000-7000-8000-000000050020")
DRAFT_REVISION = UUID("00000000-0000-7000-8000-000000050021")
PUBLICATION = UUID("00000000-0000-7000-8000-000000050022")


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=REVIEWER, display_name="Reviewer", status="active"),
                User(id=STUDENT, display_name="Student", status="active"),
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted",
                    key_id="fixture-key",
                    status="active",
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Projection course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                Operation(
                    id=CAPTURE_OPERATION,
                    organization_id=ORG,
                    kind="artifact_capture",
                    input_version="projection-fixture",
                    state="succeeded",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    finished_at=NOW,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=REVIEWER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Projection run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Projection homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/projection",
                    locator={"external_id": "example/projection"},
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
                    student_text="Submit work",
                    max_score=Decimal("5"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                CourseRunHomework(
                    id=RELATION,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    status="active",
                    revision=0,
                ),
                ArtifactVersion(
                    id=ARTIFACT_VERSION,
                    organization_id=ORG,
                    artifact_reference_id=REFERENCE,
                    provider_version="commit:projection",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG}/{ARTIFACT_VERSION}/artifact.zip",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW,
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
                    course_run_homework_id=RELATION,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    revision=1,
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
                    stable_key="correctness",
                    position=0,
                    title="Correctness",
                    description="Correct result",
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
                    artifact_reference_id=REFERENCE,
                    artifact_version_id=ARTIFACT_VERSION,
                    submitted_at=NOW - timedelta(hours=2),
                    effective_deadline=NOW + timedelta(days=1),
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=CAPTURE_OPERATION,
                    revision=0,
                ),
                ReviewCase(
                    id=REVIEW_CASE,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    revision=2,
                ),
            ]
        )
        await session.flush()
        session.add(
            _iteration(
                PUBLISHED_ITERATION,
                number=1,
                status="published",
                predecessor=None,
                revision=1,
            )
        )
        await session.flush()
        session.add(
            ReviewRevision(
                id=PUBLISHED_REVISION,
                organization_id=ORG,
                review_iteration_id=PUBLISHED_ITERATION,
                revision_number=1,
                author_user_id=REVIEWER,
                base_revision_id=None,
                feedback="Published feedback",
                total_score=Decimal("4"),
                created_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            [
                ReviewCriterionDecision(
                    id=UUID("00000000-0000-7000-8000-000000050023"),
                    organization_id=ORG,
                    review_revision_id=PUBLISHED_REVISION,
                    criterion_id=CRITERION,
                    ai_suggestion_id=None,
                    points=Decimal("4"),
                    decision="manual",
                    reason="Published human decision",
                    evidence_ids=[],
                ),
                ReviewNote(
                    id=UUID("00000000-0000-7000-8000-000000050024"),
                    organization_id=ORG,
                    review_revision_id=PUBLISHED_REVISION,
                    criterion_id=None,
                    text="Published note",
                    author_user_id=REVIEWER,
                    position=0,
                ),
            ]
        )
        published_iteration = await session.get(ReviewIteration, PUBLISHED_ITERATION)
        assert published_iteration is not None
        published_iteration.current_revision_id = PUBLISHED_REVISION
        await session.flush()
        session.add(
            _iteration(
                SUCCESSOR_ITERATION,
                number=2,
                status="in_review",
                predecessor=PUBLISHED_ITERATION,
                revision=1,
            )
        )
        await session.flush()
        session.add(
            ReviewRevision(
                id=DRAFT_REVISION,
                organization_id=ORG,
                review_iteration_id=SUCCESSOR_ITERATION,
                revision_number=1,
                author_user_id=REVIEWER,
                base_revision_id=None,
                feedback="Unpublished successor draft",
                total_score=Decimal("1"),
                created_at=NOW + timedelta(minutes=1),
            )
        )
        await session.flush()
        successor = await session.get(ReviewIteration, SUCCESSOR_ITERATION)
        review_case = await session.get(ReviewCase, REVIEW_CASE)
        assert successor is not None and review_case is not None
        successor.current_revision_id = DRAFT_REVISION
        review_case.current_iteration_id = SUCCESSOR_ITERATION
        session.add(
            ReviewPublication(
                id=PUBLICATION,
                organization_id=ORG,
                review_iteration_id=PUBLISHED_ITERATION,
                review_revision_id=PUBLISHED_REVISION,
                publication_request_id=None,
                publication_version=1,
                published_by=REVIEWER,
                published_at=NOW,
                status="published",
                revision=0,
            )
        )


def _iteration(
    identity: UUID,
    *,
    number: int,
    status: str,
    predecessor: UUID | None,
    revision: int,
) -> ReviewIteration:
    return ReviewIteration(
        id=identity,
        organization_id=ORG,
        review_case_id=REVIEW_CASE,
        course_run_id=COURSE_RUN,
        homework_id=HOMEWORK,
        student_id=STUDENT,
        iteration_number=number,
        submission_version_id=SUBMISSION_VERSION,
        artifact_version_id=ARTIFACT_VERSION,
        homework_version_id=HOMEWORK_VERSION,
        criterion_set_id=CRITERION_SET,
        effective_deadline=NOW + timedelta(days=2),
        responsible_reviewer_id=REVIEWER,
        status=status,
        current_revision_id=None,
        predecessor_iteration_id=predecessor,
        origin="initial" if predecessor is None else "correction",
        revision=revision,
    )


@pytest.fixture
async def detail_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, AsyncSessionFactory]]:
    await _seed(foundation_session_factory)
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles=["reviewer"],
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, foundation_session_factory


async def test_default_get_keeps_highest_published_result_visible_over_successor(
    detail_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    client, factory = detail_client
    response = await client.get(
        f"/api/v1/review-iterations/{SUCCESSOR_ITERATION}"
    )

    assert response.status_code == 200, response.text
    detail = response.json()
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/components/schemas/ReviewDetail",
            "components": openapi["components"],
        }
    ).validate(detail)
    assert detail["review_iteration_id"] == str(SUCCESSOR_ITERATION)
    assert detail["status"] == "in_review"
    assert detail["revision"] == 1
    assert detail["immutable_inputs"]["artifact_version_id"] == str(ARTIFACT_VERSION)
    assert detail["current_review_revision_id"] == str(PUBLISHED_REVISION)
    assert detail["current_review_revision"]["review_iteration_id"] == str(
        PUBLISHED_ITERATION
    )
    assert detail["current_review_revision"]["feedback"] == "Published feedback"
    assert detail["criterion_decisions"][0]["points"] == 4
    assert detail["review_notes"][0]["text"] == "Published note"
    assert detail["ai_review"] is None
    assert detail["deliveries"] == []
    assert str(ORG) in detail["immutable_inputs"]["artifact_download_url"]
    assert str(ARTIFACT_VERSION) in detail["immutable_inputs"]["artifact_download_url"]

    async with factory() as session:
        successor = await session.scalar(
            select(ReviewIteration).where(
                ReviewIteration.organization_id == ORG,
                ReviewIteration.id == SUCCESSOR_ITERATION,
            )
        )
        review_case = await session.scalar(
            select(ReviewCase).where(
                ReviewCase.organization_id == ORG,
                ReviewCase.id == REVIEW_CASE,
            )
        )
    assert successor is not None and successor.current_revision_id == DRAFT_REVISION
    assert review_case is not None and review_case.current_iteration_id == SUCCESSOR_ITERATION


async def test_same_iteration_uuid_in_other_tenant_has_no_global_fallback(
    detail_client: tuple[httpx.AsyncClient, AsyncSessionFactory],
) -> None:
    _client, factory = detail_client
    signer_called = False

    def forbidden_signer(
        organization_id: UUID,
        artifact_version_id: UUID,
    ) -> ArtifactDownloadGrant:
        nonlocal signer_called
        signer_called = True
        raise AssertionError(
            f"cross-tenant signer called for {organization_id}/{artifact_version_id}"
        )

    async with factory() as session:
        detail = await read_review_detail(
            session,
            organization_id=OTHER_ORG,
            review_iteration_id=SUCCESSOR_ITERATION,
            sign_artifact_download=forbidden_signer,
        )

    assert detail is None
    assert signer_called is False
