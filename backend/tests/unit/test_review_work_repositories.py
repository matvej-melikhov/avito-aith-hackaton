"""MySQL-backed tests for tenant-scoped review-work repositories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

from review_platform.application.services.review_responsibility import (
    ReviewResponsibilityEvent,
)
from review_platform.infrastructure.db.models import (
    ArtifactReference,
    ArtifactVersion,
    AvailabilityPlan,
    Course,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    CriterionSet,
    ExternalCredential,
    Homework,
    HomeworkVersion,
    Operation,
    OrganizationMembership,
    ReviewCase,
    ReviewerCourseSelection,
    ReviewIteration,
    ReviewResponsibility,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.repositories.review_work import (
    InvalidReviewWorkTransaction,
    SqlReviewWorkRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
REVIEWER_A = UUID("00000000-0000-7000-8000-000000128001")
REVIEWER_TWO = UUID("00000000-0000-7000-8000-000000128002")
REVIEWER_B = UUID("00000000-0000-7000-8000-000000128003")
STUDENT = UUID("00000000-0000-7000-8000-000000128004")
MEMBERSHIP_A = UUID("00000000-0000-7000-8000-000000128011")
MEMBERSHIP_TWO = UUID("00000000-0000-7000-8000-000000128012")
MEMBERSHIP_B = UUID("00000000-0000-7000-8000-000000128013")
COURSE_A = UUID("00000000-0000-7000-8000-000000128021")
COURSE_B = UUID("00000000-0000-7000-8000-000000128022")
RUN_ACTIVE = UUID("00000000-0000-7000-8000-000000128031")
RUN_ARCHIVED = UUID("00000000-0000-7000-8000-000000128032")
RUN_FOREIGN = UUID("00000000-0000-7000-8000-000000128033")
HOMEWORK = UUID("00000000-0000-7000-8000-000000128041")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000128042")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000128043")
RUN_HOMEWORK = UUID("00000000-0000-7000-8000-000000128044")
HOMEWORK_PUBLICATION = UUID("00000000-0000-7000-8000-000000128045")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000128051")
REFERENCE = UUID("00000000-0000-7000-8000-000000128052")
ARTIFACT = UUID("00000000-0000-7000-8000-000000128053")
CAPTURE_OPERATION = UUID("00000000-0000-7000-8000-000000128054")
SUBMISSION = UUID("00000000-0000-7000-8000-000000128061")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000128062")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000128063")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _seed_scope(factory: AsyncSessionFactory, *, full_candidate: bool = False) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=REVIEWER_A, display_name="Reviewer A"),
                User(id=REVIEWER_TWO, display_name="Reviewer Two"),
                User(id=REVIEWER_B, display_name="Reviewer B"),
                User(id=STUDENT, display_name="Student"),
                Course(
                    id=COURSE_A,
                    organization_id=ORG_A,
                    title="Course A",
                    description="",
                    source_kind="standalone",
                    status="active",
                ),
                Course(
                    id=COURSE_B,
                    organization_id=ORG_B,
                    title="Course B",
                    description="",
                    source_kind="standalone",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_A,
                    organization_id=ORG_A,
                    user_id=REVIEWER_A,
                    roles=["reviewer"],
                    status="active",
                    revision=3,
                    auth_epoch=0,
                ),
                OrganizationMembership(
                    id=MEMBERSHIP_TWO,
                    organization_id=ORG_A,
                    user_id=REVIEWER_TWO,
                    roles=["methodologist", "reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                OrganizationMembership(
                    id=MEMBERSHIP_B,
                    organization_id=ORG_B,
                    user_id=REVIEWER_B,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                CourseRun(
                    id=RUN_ACTIVE,
                    organization_id=ORG_A,
                    course_id=COURSE_A,
                    title="Active run",
                    timezone="UTC",
                    status="active",
                ),
                CourseRun(
                    id=RUN_ARCHIVED,
                    organization_id=ORG_A,
                    course_id=COURSE_A,
                    title="Archived run",
                    timezone="UTC",
                    status="archived",
                ),
                CourseRun(
                    id=RUN_FOREIGN,
                    organization_id=ORG_B,
                    course_id=COURSE_B,
                    title="Foreign run",
                    timezone="UTC",
                    status="active",
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG_A,
                    course_id=COURSE_A,
                    title="Homework",
                ),
            ]
        )
        await session.flush()
        session.add(
            ReviewCase(
                id=REVIEW_CASE,
                organization_id=ORG_A,
                course_run_id=RUN_ACTIVE,
                homework_id=HOMEWORK,
                student_id=STUDENT,
                current_iteration_id=None,
                revision=2,
            )
        )
        if not full_candidate:
            return
        session.add_all(
            [
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG_A,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-fixture",
                    key_id="fixture-key",
                ),
                Operation(
                    id=CAPTURE_OPERATION,
                    organization_id=ORG_A,
                    kind="artifact_capture",
                    input_version="review-work-fixture",
                    state="succeeded",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                HomeworkVersion(
                    id=HOMEWORK_VERSION,
                    organization_id=ORG_A,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Submit work",
                    max_score=Decimal("10"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=45,
                    revision=0,
                ),
                CourseRunHomework(
                    id=RUN_HOMEWORK,
                    organization_id=ORG_A,
                    course_run_id=RUN_ACTIVE,
                    homework_id=HOMEWORK,
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG_A,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/repository",
                    locator={"external_id": "example/repository"},
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
                CriterionSet(
                    id=CRITERION_SET,
                    organization_id=ORG_A,
                    homework_version_id=HOMEWORK_VERSION,
                ),
                CourseRunHomeworkPublication(
                    id=HOMEWORK_PUBLICATION,
                    organization_id=ORG_A,
                    course_run_homework_id=RUN_HOMEWORK,
                    homework_id=HOMEWORK,
                    homework_version_id=HOMEWORK_VERSION,
                    publication_sequence=1,
                    submission_deadline=NOW - timedelta(hours=1),
                    review_deadline=NOW + timedelta(days=1),
                    published_at=NOW - timedelta(days=2),
                ),
                ArtifactVersion(
                    id=ARTIFACT,
                    organization_id=ORG_A,
                    artifact_reference_id=REFERENCE,
                    provider_version="commit:fixture",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG_A}/{ARTIFACT}/artifact.zip",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW - timedelta(hours=2),
                    artifact_metadata={},
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG_A,
                    course_run_homework_id=RUN_HOMEWORK,
                    course_run_id=RUN_ACTIVE,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_predeadline_version_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add(
            SubmissionVersion(
                id=SUBMISSION_VERSION,
                organization_id=ORG_A,
                submission_id=SUBMISSION,
                course_run_id=RUN_ACTIVE,
                homework_id=HOMEWORK,
                sequence=1,
                homework_version_id=HOMEWORK_VERSION,
                artifact_reference_id=REFERENCE,
                artifact_version_id=ARTIFACT,
                submitted_at=NOW - timedelta(hours=2),
                effective_deadline=NOW - timedelta(hours=1),
                phase="before_deadline",
                status="ready",
                capture_operation_id=CAPTURE_OPERATION,
                revision=0,
            )
        )
        await session.flush()
        relation = await session.get(CourseRunHomework, RUN_HOMEWORK)
        submission = await session.get(Submission, SUBMISSION)
        assert relation is not None and submission is not None
        relation.current_publication_id = HOMEWORK_PUBLICATION
        submission.current_predeadline_version_id = SUBMISSION_VERSION


async def test_selection_and_availability_are_tenant_safe_and_do_not_commit(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_scope(foundation_session_factory)
    repository = SqlReviewWorkRepository(
        id_factory=iter(
            (
                UUID("00000000-0000-7000-8000-000000128101"),
                UUID("00000000-0000-7000-8000-000000128102"),
                UUID("00000000-0000-7000-8000-000000128103"),
            )
        ).__next__
    )
    async with foundation_session_factory() as session:
        assert await repository.lock_active_reviewer_membership(
            ORG_A,
            MEMBERSHIP_A,
            REVIEWER_A,
            expected_revision=3,
            transaction=session,
        )
        assert not await repository.lock_active_reviewer_membership(
            ORG_A,
            MEMBERSHIP_A,
            REVIEWER_A,
            expected_revision=2,
            transaction=session,
        )
        assert not await repository.lock_active_reviewer_membership(
            ORG_B,
            MEMBERSHIP_A,
            REVIEWER_A,
            expected_revision=3,
            transaction=session,
        )
        assert await repository.replace_exact_active_runs(
            ORG_A,
            REVIEWER_A,
            (RUN_ACTIVE,),
            transaction=session,
        ) == (RUN_ACTIVE,)
        assert (
            await repository.replace_exact_active_runs(
                ORG_A,
                REVIEWER_A,
                (RUN_ARCHIVED,),
                transaction=session,
            )
            == ()
        )
        assert (
            await repository.replace_exact_active_runs(
                ORG_A,
                REVIEWER_A,
                (RUN_FOREIGN,),
                transaction=session,
            )
            == ()
        )
        selected = (
            await session.scalars(
                select(ReviewerCourseSelection).where(
                    ReviewerCourseSelection.organization_id == ORG_A,
                    ReviewerCourseSelection.reviewer_id == REVIEWER_A,
                    ReviewerCourseSelection.active.is_(True),
                )
            )
        ).all()
        assert [row.course_run_id for row in selected] == [RUN_ACTIVE]

        plan_id, revision = await repository.upsert_plan(
            ORG_A,
            REVIEWER_A,
            planned_minutes=100_000,
            until_at=NOW + timedelta(days=30),
            transaction=session,
        )
        same_id, next_revision = await repository.upsert_plan(
            ORG_A,
            REVIEWER_A,
            planned_minutes=60,
            until_at=NOW + timedelta(days=1),
            transaction=session,
        )
        assert (same_id, revision, next_revision) == (plan_id, 0, 1)
        await session.rollback()

    async with foundation_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AvailabilityPlan)) == 0
        assert await session.scalar(select(func.count()).select_from(ReviewerCourseSelection)) == 0


async def test_responsibility_append_is_nonexclusive_and_tenant_affine(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_scope(foundation_session_factory)
    repository = SqlReviewWorkRepository()
    async with foundation_session_factory() as session:
        assert (
            await repository.resolve_context(
                ORG_A,
                REVIEW_CASE,
                None,
                expected_review_iteration_revision=None,
                transaction=session,
            )
            is not None
        )
        assert (
            await repository.resolve_context(
                ORG_B,
                REVIEW_CASE,
                None,
                expected_review_iteration_revision=None,
                transaction=session,
            )
            is None
        )

    async def append_event(event: ReviewResponsibilityEvent) -> None:
        async with session_scope(foundation_session_factory) as session:
            await repository.append(
                event,
                expected_review_iteration_revision=None,
                transaction=session,
            )

    first = ReviewResponsibilityEvent(
        event_id=UUID("00000000-0000-7000-8000-000000128111"),
        organization_id=ORG_A,
        review_case_id=REVIEW_CASE,
        review_iteration_id=None,
        reviewer_id=REVIEWER_A,
        actor_id=REVIEWER_A,
        action="started",
        occurred_at=NOW,
    )
    second = ReviewResponsibilityEvent(
        event_id=UUID("00000000-0000-7000-8000-000000128112"),
        organization_id=ORG_A,
        review_case_id=REVIEW_CASE,
        review_iteration_id=None,
        reviewer_id=REVIEWER_TWO,
        actor_id=REVIEWER_TWO,
        action="joined",
        occurred_at=NOW,
    )
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(append_event, first)
        tasks.start_soon(append_event, second)

    async with foundation_session_factory() as session:
        rows = (
            await session.scalars(
                select(ReviewResponsibility)
                .where(
                    ReviewResponsibility.organization_id == ORG_A,
                    ReviewResponsibility.review_case_id == REVIEW_CASE,
                )
                .order_by(ReviewResponsibility.id)
            )
        ).all()
        assert [(row.reviewer_id, row.action) for row in rows] == [
            (REVIEWER_A, "started"),
            (REVIEWER_TWO, "joined"),
        ]

        wrong_tenant = ReviewResponsibilityEvent(
            event_id=UUID("00000000-0000-7000-8000-000000128113"),
            organization_id=ORG_B,
            review_case_id=REVIEW_CASE,
            review_iteration_id=None,
            reviewer_id=REVIEWER_B,
            actor_id=REVIEWER_B,
            action="started",
            occurred_at=NOW,
        )
        assert not await repository.append(
            wrong_tenant,
            expected_review_iteration_revision=None,
            transaction=session,
        )


async def test_recommendation_snapshot_is_selected_active_exact_and_read_only(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_scope(foundation_session_factory, full_candidate=True)
    ids = iter(
        (
            UUID("00000000-0000-7000-8000-000000128121"),
            UUID("00000000-0000-7000-8000-000000128122"),
            UUID("00000000-0000-7000-8000-000000128123"),
        )
    )
    repository = SqlReviewWorkRepository(id_factory=ids.__next__)
    async with session_scope(foundation_session_factory) as session:
        assert await repository.lock_active_reviewer_membership(
            ORG_A,
            MEMBERSHIP_A,
            REVIEWER_A,
            expected_revision=3,
            transaction=session,
        )
        await repository.replace_exact_active_runs(
            ORG_A,
            REVIEWER_A,
            (RUN_ACTIVE,),
            transaction=session,
        )
        await repository.upsert_plan(
            ORG_A,
            REVIEWER_A,
            planned_minutes=120,
            until_at=NOW + timedelta(days=1),
            transaction=session,
        )
        await repository.append(
            ReviewResponsibilityEvent(
                event_id=UUID("00000000-0000-7000-8000-000000128124"),
                organization_id=ORG_A,
                review_case_id=REVIEW_CASE,
                review_iteration_id=None,
                reviewer_id=REVIEWER_A,
                actor_id=REVIEWER_A,
                action="started",
                occurred_at=NOW,
            ),
            expected_review_iteration_revision=None,
            transaction=session,
        )

    async with foundation_session_factory() as session:
        snapshot = await repository.load_queue(
            ORG_A,
            REVIEWER_A,
            RUN_ACTIVE,
            now=NOW,
            transaction=session,
        )
        assert snapshot is not None
        assert snapshot.selected
        assert snapshot.course_status == "active"
        assert snapshot.course_run_status == "active"
        assert snapshot.planned_minutes == 120
        assert snapshot.assigned_minutes == 0
        assert len(snapshot.candidates) == 1
        candidate = snapshot.candidates[0]
        assert candidate.review_case_id == REVIEW_CASE
        assert candidate.candidate_id == REVIEW_CASE
        assert candidate.review_case_revision == 2
        assert candidate.submission_version_id == SUBMISSION_VERSION
        assert candidate.review_deadline == NOW + timedelta(days=1)
        assert candidate.submitted_at == NOW - timedelta(hours=2)
        assert candidate.estimated_review_minutes == 45
        assert candidate.continuing_reviewer_id == REVIEWER_A
        assert candidate.active_reviewer_count == 1

        iteration_id = UUID("00000000-0000-7000-8000-000000128125")
        session.add(
            ReviewIteration(
                id=iteration_id,
                organization_id=ORG_A,
                review_case_id=REVIEW_CASE,
                course_run_id=RUN_ACTIVE,
                homework_id=HOMEWORK,
                student_id=STUDENT,
                iteration_number=1,
                submission_version_id=SUBMISSION_VERSION,
                artifact_version_id=ARTIFACT,
                homework_version_id=HOMEWORK_VERSION,
                criterion_set_id=CRITERION_SET,
                effective_deadline=NOW - timedelta(hours=1),
                responsible_reviewer_id=None,
                status="in_review",
                current_revision_id=None,
                predecessor_iteration_id=None,
                origin="initial",
                revision=0,
            )
        )
        await session.flush()
        review_case = await session.get(ReviewCase, REVIEW_CASE)
        assert review_case is not None
        review_case.current_iteration_id = iteration_id
        await session.flush()
        assigned = await repository.load_queue(
            ORG_A,
            REVIEWER_A,
            RUN_ACTIVE,
            now=NOW,
            transaction=session,
        )
        assert assigned is not None
        assert assigned.assigned_minutes == 45
        assert assigned.candidates[0].continuing_reviewer_id == REVIEWER_A

        await repository.append(
            ReviewResponsibilityEvent(
                event_id=UUID("00000000-0000-7000-8000-000000128126"),
                organization_id=ORG_A,
                review_case_id=REVIEW_CASE,
                review_iteration_id=iteration_id,
                reviewer_id=REVIEWER_A,
                actor_id=REVIEWER_A,
                action="released",
                occurred_at=NOW + timedelta(minutes=1),
            ),
            expected_review_iteration_revision=0,
            transaction=session,
        )
        released = await repository.load_queue(
            ORG_A,
            REVIEWER_A,
            RUN_ACTIVE,
            now=NOW,
            transaction=session,
        )
        assert released is not None
        assert released.assigned_minutes == 0
        assert released.candidates[0].continuing_reviewer_id is None
        assert released.candidates[0].active_reviewer_count == 0

        foreign = await repository.load_queue(
            ORG_B,
            REVIEWER_A,
            RUN_ACTIVE,
            now=NOW,
            transaction=session,
        )
        assert foreign is None

        run = await session.get(CourseRun, RUN_ACTIVE)
        assert run is not None
        run.status = "archived"
        await session.flush()
        archived = await repository.load_queue(
            ORG_A,
            REVIEWER_A,
            RUN_ACTIVE,
            now=NOW,
            transaction=session,
        )
        assert archived is not None
        assert archived.course_run_status == "archived"
        assert archived.candidates == ()


async def test_repository_rejects_non_session_transactions() -> None:
    repository = SqlReviewWorkRepository()
    with pytest.raises(InvalidReviewWorkTransaction, match="caller-owned AsyncSession"):
        await repository.load_queue(
            ORG_A,
            REVIEWER_A,
            RUN_ACTIVE,
            now=NOW,
            transaction=object(),
        )
