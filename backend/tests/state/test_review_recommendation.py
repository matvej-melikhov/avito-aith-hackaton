"""Executable RED contract for reviewer preferences and deterministic recommendation."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import ModuleType
from typing import Any, Protocol, cast
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from fastapi.routing import APIRoute
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
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
from review_platform.infrastructure.db.models.review_case import ReviewCase
from review_platform.infrastructure.db.models.submission import (
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000000001")
REVIEWER = UUID("00000000-0000-7000-8000-000000020001")
STUDENT = UUID("00000000-0000-7000-8000-000000020002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000020003")
COURSE = UUID("00000000-0000-7000-8000-000000020004")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000020005")
HOMEWORK = UUID("00000000-0000-7000-8000-000000020006")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000020007")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000020008")
CRITERION = UUID("00000000-0000-7000-8000-000000020009")
RELATION = UUID("00000000-0000-7000-8000-000000020010")
PUBLICATION = UUID("00000000-0000-7000-8000-000000020011")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000020012")
REFERENCE = UUID("00000000-0000-7000-8000-000000020013")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000020014")
CAPTURE_OPERATION = UUID("00000000-0000-7000-8000-000000020015")
SUBMISSION = UUID("00000000-0000-7000-8000-000000020016")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000020017")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000020018")


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    candidate_id: UUID
    review_deadline: datetime
    continuing_reviewer_id: UUID | None
    submitted_at: datetime
    estimated_review_minutes: int
    active_reviewer_count: int


@dataclass(frozen=True, slots=True)
class ReviewerRecommendationContext:
    reviewer_id: UUID
    planned_minutes: int
    assigned_minutes: int


class RankRecommendations(Protocol):
    def __call__(
        self,
        candidates: Sequence[RecommendationCandidate],
        *,
        context: ReviewerRecommendationContext,
    ) -> Sequence[RecommendationCandidate]: ...


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
                    ciphertext="encrypted-fixture",
                    key_id="fixture-key",
                    status="active",
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Recommendation course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                Operation(
                    id=CAPTURE_OPERATION,
                    organization_id=ORG,
                    kind="artifact_capture",
                    input_version="recommendation-fixture",
                    state="succeeded",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    finished_at=NOW,
                    error_code=None,
                    sanitized_error=None,
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
                    roles=["methodologist", "reviewer"],
                    status="active",
                    revision=3,
                    auth_epoch=2,
                ),
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id="recommendation-run",
                    title="Recommendation run",
                    starts_at=None,
                    ends_at=None,
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Recommendation homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/recommendation",
                    locator={"external_id": "example/recommendation"},
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
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
                ArtifactVersion(
                    id=ARTIFACT_VERSION,
                    organization_id=ORG,
                    artifact_reference_id=REFERENCE,
                    provider_version="commit:fixture",
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
                CourseRunHomeworkPublication(
                    id=PUBLICATION,
                    organization_id=ORG,
                    course_run_homework_id=RELATION,
                    homework_id=HOMEWORK,
                    homework_version_id=HOMEWORK_VERSION,
                    publication_sequence=1,
                    submission_deadline=NOW - timedelta(hours=1),
                    review_deadline=NOW + timedelta(days=1),
                    published_at=NOW - timedelta(days=1),
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG,
                    course_run_homework_id=RELATION,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_predeadline_version_id=None,
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
                    effective_deadline=NOW - timedelta(hours=1),
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
                    current_iteration_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        relation = await session.get(CourseRunHomework, RELATION)
        submission = await session.get(Submission, SUBMISSION)
        assert relation is not None and submission is not None
        relation.current_publication_id = PUBLICATION
        submission.current_predeadline_version_id = SUBMISSION_VERSION


@pytest.fixture
async def reviewer_client(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[httpx.AsyncClient]:
    await _seed(foundation_session_factory)
    app = create_app(Settings(), runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"methodologist", "reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )

    @app.middleware("http")
    async def inject_authenticated_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


def _command(
    name: str,
    *,
    target: UUID,
    revision_target: str,
    expected_revision: int,
    payload: Mapping[str, Any],
    request_number: int,
) -> dict[str, Any]:
    return {
        "request_id": f"00000000-0000-7000-8000-{request_number:012d}",
        "idempotency_key": f"review-recommend-{name}-{request_number:06d}",
        "command_name": name,
        "revision_target": revision_target,
        "target_id": str(target),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


def _validate_component(name: str, value: object) -> None:
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": openapi["components"],
        }
    ).validate(value)


@pytest.mark.parametrize(
    ("path", "method", "operation_id"),
    [
        (
            "/api/v1/reviewer/course-selections",
            "POST",
            "setReviewerCourseSelection",
        ),
        ("/api/v1/reviewer/availability", "PUT", "setReviewerAvailability"),
        ("/api/v1/review-queue/next", "GET", "recommendNextReview"),
    ],
)
def test_frozen_routes_bind_exact_preference_commands(
    path: str,
    method: str,
    operation_id: str,
) -> None:
    app = create_app(Settings())
    operations = {
        (route.path, method): route.operation_id
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    route_key = (path, method)
    assert route_key in operations, f"T130 is missing frozen operation {method} {path}"
    assert operations[route_key] == operation_id


async def test_exact_selection_and_unbounded_advisory_plan_commands(
    reviewer_client: httpx.AsyncClient,
) -> None:
    selection = await reviewer_client.post(
        "/api/v1/reviewer/course-selections",
        json=_command(
            "set_reviewer_course_selection",
            target=MEMBERSHIP,
            revision_target="membership",
            expected_revision=3,
            payload={"course_run_ids": [str(COURSE_RUN)]},
            request_number=20100,
        ),
    )
    assert selection.status_code == 204, selection.text

    availability = await reviewer_client.put(
        "/api/v1/reviewer/availability",
        json=_command(
            "set_reviewer_availability",
            target=MEMBERSHIP,
            revision_target="membership",
            expected_revision=3,
            payload={
                "planned_minutes": 100_000,
                "until_at": (NOW + timedelta(days=30)).isoformat(),
            },
            request_number=20101,
        ),
    )
    assert availability.status_code == 200, availability.text
    _validate_component("UpdatedResource", availability.json())

    wrong_variant = await reviewer_client.post(
        "/api/v1/reviewer/course-selections",
        json=_command(
            "set_reviewer_availability",
            target=MEMBERSHIP,
            revision_target="membership",
            expected_revision=3,
            payload={"planned_minutes": 60, "until_at": NOW.isoformat()},
            request_number=20102,
        ),
    )
    assert wrong_variant.status_code == 409, wrong_variant.text
    _validate_component("ErrorObject", wrong_variant.json())


def test_deterministic_order_is_deadline_continuity_age_plan_load_then_uuid() -> None:
    module = _recommendation_module()
    rank = cast(RankRecommendations, module.__dict__["rank_recommendations"])
    context = ReviewerRecommendationContext(
        reviewer_id=REVIEWER,
        planned_minutes=30,
        assigned_minutes=0,
    )
    earliest = _candidate(1, deadline_hours=1, submitted_hours=1, estimate=120)
    continuation = _candidate(
        2,
        deadline_hours=2,
        submitted_hours=1,
        estimate=60,
        continuing=REVIEWER,
    )
    older = _candidate(3, deadline_hours=2, submitted_hours=4, estimate=60)
    fits_plan = _candidate(4, deadline_hours=2, submitted_hours=1, estimate=20)
    lower_load = _candidate(
        5,
        deadline_hours=2,
        submitted_hours=1,
        estimate=60,
        active_reviewers=0,
    )
    higher_load = _candidate(
        6,
        deadline_hours=2,
        submitted_hours=1,
        estimate=60,
        active_reviewers=2,
    )

    ordered = rank(
        [higher_load, lower_load, fits_plan, older, continuation, earliest],
        context=context,
    )

    assert [item.candidate_id for item in ordered] == [
        earliest.candidate_id,
        continuation.candidate_id,
        older.candidate_id,
        fits_plan.candidate_id,
        lower_load.candidate_id,
        higher_load.candidate_id,
    ]
    zero_plan = rank([higher_load, fits_plan], context=replace_plan(context, 0))
    assert {item.candidate_id for item in zero_plan} == {
        higher_load.candidate_id,
        fits_plan.candidate_id,
    }


async def test_archive_race_has_only_pre_archive_open_or_fail_closed_outcomes(
    reviewer_client: httpx.AsyncClient,
) -> None:
    archive_command = _command(
        "archive_course_run",
        target=COURSE_RUN,
        revision_target="course_run",
        expected_revision=0,
        payload={"reason": "race fixture"},
        request_number=20110,
    )
    open_command = _command(
        "open_review_iteration",
        target=REVIEW_CASE,
        revision_target="review_case",
        expected_revision=0,
        payload={"submission_version_id": str(SUBMISSION_VERSION)},
        request_number=20111,
    )
    archive_response, open_response = await asyncio.gather(
        reviewer_client.post(
            f"/api/v1/course-runs/{COURSE_RUN}/archive",
            json=archive_command,
        ),
        reviewer_client.post(
            f"/api/v1/review-cases/{REVIEW_CASE}/iterations",
            json=open_command,
        ),
    )
    assert archive_response.status_code == 204, archive_response.text
    assert open_response.status_code in {201, 409}, open_response.text
    if open_response.status_code == 201:
        _validate_component("ReviewIterationCreated", open_response.json())
    else:
        _validate_component("ErrorObject", open_response.json())

    after_archive = await reviewer_client.get(
        "/api/v1/review-queue/next",
        params={"course_run_id": str(COURSE_RUN)},
    )
    assert after_archive.status_code == 200, after_archive.text
    assert after_archive.json() is None

    retry_open = await reviewer_client.post(
        f"/api/v1/review-cases/{REVIEW_CASE}/iterations",
        json={**open_command, "idempotency_key": "review-open-after-archive-0001"},
    )
    assert retry_open.status_code == 409, retry_open.text
    _validate_component("ErrorObject", retry_open.json())


def _candidate(
    suffix: int,
    *,
    deadline_hours: int,
    submitted_hours: int,
    estimate: int,
    continuing: UUID | None = None,
    active_reviewers: int = 1,
) -> RecommendationCandidate:
    return RecommendationCandidate(
        candidate_id=UUID(f"00000000-0000-7000-8000-{suffix:012d}"),
        review_deadline=NOW + timedelta(hours=deadline_hours),
        continuing_reviewer_id=continuing,
        submitted_at=NOW - timedelta(hours=submitted_hours),
        estimated_review_minutes=estimate,
        active_reviewer_count=active_reviewers,
    )


def replace_plan(
    context: ReviewerRecommendationContext,
    planned_minutes: int,
) -> ReviewerRecommendationContext:
    return ReviewerRecommendationContext(
        reviewer_id=context.reviewer_id,
        planned_minutes=planned_minutes,
        assigned_minutes=context.assigned_minutes,
    )


def _recommendation_module() -> ModuleType:
    module_name = "review_platform.domain.recommendation"
    specification = importlib.util.find_spec(module_name)
    assert specification is not None, (
        "T119 deterministic recommendation behavior is not implemented"
    )
    module = importlib.import_module(module_name)
    assert callable(module.__dict__.get("rank_recommendations")), (
        "T119 must expose rank_recommendations"
    )
    return module
