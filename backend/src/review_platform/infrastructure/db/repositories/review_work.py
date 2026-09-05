"""Tenant-scoped SQL adapters for reviewer preferences and review work."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.services.recommendations import (
    CourseRunState,
    CourseState,
    RecommendationQueueSnapshot,
    RecommendationRepository,
    ReviewQueueCandidate,
)
from review_platform.application.services.review_responsibility import (
    ResponsibilityContext,
    ReviewResponsibilityEvent,
    ReviewResponsibilityRepository,
)
from review_platform.application.services.reviewer_availability import (
    ReviewerAvailabilityRepository,
)
from review_platform.application.services.reviewer_course_selections import (
    ReviewerCourseSelectionRepository,
)
from review_platform.domain.primitives import require_utc, uuid7
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomeworkPublication,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import OrganizationMembership
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_work import (
    AvailabilityPlan,
    ReviewerCourseSelection,
    ReviewResponsibility,
)
from review_platform.infrastructure.db.models.submission import Submission, SubmissionVersion

_REVIEWER_ROLE = "reviewer"
_OPEN_REVIEW_STATUSES = ("queued", "in_review", "ready_to_publish")
_ACTIVE_RESPONSIBILITY_ACTIONS = frozenset({"started", "joined"})


class ReviewWorkRepositoryError(RuntimeError):
    """A tenant, membership, or immutable review-work invariant failed."""


class InvalidReviewWorkTransaction(ReviewWorkRepositoryError):
    pass


class ReviewWorkScopeConflict(ReviewWorkRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class _CandidateRow:
    review_case_id: UUID
    review_case_revision: int
    review_iteration_id: UUID | None
    submission_version_id: UUID
    course_run_homework_id: UUID
    homework_version_id: UUID
    submitted_at: datetime
    effective_deadline: datetime
    estimated_review_minutes: int
    responsible_reviewer_id: UUID | None


class SqlReviewWorkRepository:
    """Implement T120/T121/T122 ports using a caller-owned AsyncSession."""

    def __init__(self, *, id_factory: Callable[[], UUID] = uuid7) -> None:
        self._id_factory = id_factory

    async def lock_active_reviewer_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        reviewer_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        if expected_revision < 0:
            return False
        membership = await session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
                OrganizationMembership.user_id == reviewer_id,
                OrganizationMembership.status == "active",
                OrganizationMembership.revision == expected_revision,
            )
            .with_for_update()
        )
        return membership is not None and _REVIEWER_ROLE in membership.roles

    async def replace_exact_active_runs(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_ids: tuple[UUID, ...],
        *,
        transaction: object,
    ) -> tuple[UUID, ...]:
        session = _session(transaction)
        requested = tuple(sorted(course_run_ids, key=str))
        if len(requested) > 1000 or len(set(requested)) != len(requested):
            return ()
        if not await _active_reviewer_exists(session, organization_id, reviewer_id):
            return ()
        if requested:
            valid = (
                await session.scalars(
                    select(CourseRun.id)
                    .join(
                        Course,
                        and_(
                            Course.organization_id == CourseRun.organization_id,
                            Course.id == CourseRun.course_id,
                        ),
                    )
                    .where(
                        CourseRun.organization_id == organization_id,
                        CourseRun.id.in_(requested),
                        CourseRun.status == "active",
                        Course.status == "active",
                    )
                    .order_by(CourseRun.id)
                    .with_for_update()
                )
            ).all()
            if set(valid) != set(requested) or len(valid) != len(requested):
                return ()

        existing = (
            await session.scalars(
                select(ReviewerCourseSelection)
                .where(
                    ReviewerCourseSelection.organization_id == organization_id,
                    ReviewerCourseSelection.reviewer_id == reviewer_id,
                )
                .order_by(ReviewerCourseSelection.course_run_id, ReviewerCourseSelection.id)
                .with_for_update()
            )
        ).all()
        by_run = {row.course_run_id: row for row in existing}
        requested_set = set(requested)
        for row in existing:
            row.active = row.course_run_id in requested_set
        for course_run_id in requested:
            if course_run_id not in by_run:
                session.add(
                    ReviewerCourseSelection(
                        id=self._id_factory(),
                        organization_id=organization_id,
                        course_run_id=course_run_id,
                        reviewer_id=reviewer_id,
                        active=True,
                    )
                )
        await session.flush()
        return requested

    async def upsert_plan(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        *,
        planned_minutes: int,
        until_at: datetime,
        transaction: object,
    ) -> tuple[UUID, int]:
        session = _session(transaction)
        if (
            not isinstance(planned_minutes, int)
            or isinstance(planned_minutes, bool)
            or planned_minutes < 0
        ):
            raise ReviewWorkScopeConflict("planned_minutes must be a nonnegative integer")
        until = require_utc(until_at)
        if not await _active_reviewer_exists(session, organization_id, reviewer_id):
            raise ReviewWorkScopeConflict("active tenant reviewer membership was not found")
        candidate_id = self._id_factory()
        insert = mysql_insert(AvailabilityPlan).values(
            id=candidate_id,
            organization_id=organization_id,
            reviewer_id=reviewer_id,
            planned_minutes=planned_minutes,
            until_at=until,
            revision=0,
        )
        await session.execute(
            insert.on_duplicate_key_update(
                planned_minutes=insert.inserted.planned_minutes,
                until_at=insert.inserted.until_at,
                revision=AvailabilityPlan.revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        plan = await session.scalar(
            select(AvailabilityPlan)
            .where(
                AvailabilityPlan.organization_id == organization_id,
                AvailabilityPlan.reviewer_id == reviewer_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if plan is None:
            raise ReviewWorkScopeConflict("availability upsert produced no tenant plan")
        return plan.id, plan.revision

    async def resolve_context(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        review_iteration_id: UUID | None,
        *,
        expected_review_iteration_revision: int | None,
        transaction: object,
    ) -> ResponsibilityContext | None:
        session = _session(transaction)
        revision: int | None = None
        if review_iteration_id is None:
            if expected_review_iteration_revision is not None:
                return None
            found_id = await session.scalar(
                select(ReviewCase.id).where(
                    ReviewCase.organization_id == organization_id,
                    ReviewCase.id == review_case_id,
                )
            )
        else:
            found = (
                await session.execute(
                    select(ReviewIteration.id, ReviewIteration.revision)
                    .where(
                        ReviewIteration.organization_id == organization_id,
                        ReviewIteration.review_case_id == review_case_id,
                        ReviewIteration.id == review_iteration_id,
                        ReviewIteration.revision == expected_review_iteration_revision,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if found is None:
                return None
            found_id, revision = found
        if found_id is None:
            return None
        return ResponsibilityContext(organization_id, review_case_id, review_iteration_id, revision)

    async def append(
        self,
        event: ReviewResponsibilityEvent,
        *,
        expected_review_iteration_revision: int | None,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        if event.action not in {"started", "joined", "released", "completed"}:
            raise ReviewWorkScopeConflict("unknown responsibility action")
        occurred_at = require_utc(event.occurred_at)
        context = await self.resolve_context(
            event.organization_id,
            event.review_case_id,
            event.review_iteration_id,
            expected_review_iteration_revision=expected_review_iteration_revision,
            transaction=session,
        )
        if context is None:
            return False
        participant_ids = {event.reviewer_id, event.actor_id}
        active_members = set(
            (
                await session.scalars(
                    select(OrganizationMembership.user_id).where(
                        OrganizationMembership.organization_id == event.organization_id,
                        OrganizationMembership.user_id.in_(participant_ids),
                        OrganizationMembership.status == "active",
                    )
                )
            ).all()
        )
        if active_members != participant_ids:
            raise ReviewWorkScopeConflict(
                "responsibility participants must be active tenant members"
            )
        row = ReviewResponsibility(
            id=event.event_id,
            organization_id=event.organization_id,
            review_case_id=event.review_case_id,
            review_iteration_id=event.review_iteration_id,
            reviewer_id=event.reviewer_id,
            actor_id=event.actor_id,
            action=event.action,
            occurred_at=occurred_at,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError as error:
            raise ReviewWorkScopeConflict(
                "responsibility event identity or affinity conflicts"
            ) from error
        return True

    async def load_queue(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        course_run_id: UUID,
        *,
        now: datetime,
        transaction: object,
    ) -> RecommendationQueueSnapshot | None:
        """Load one stable snapshot without SELECT FOR UPDATE or ownership writes."""

        session = _session(transaction)
        selected_at = require_utc(now)
        state = (
            await session.execute(
                select(Course.status, CourseRun.status)
                .select_from(CourseRun)
                .join(
                    Course,
                    and_(
                        Course.organization_id == CourseRun.organization_id,
                        Course.id == CourseRun.course_id,
                    ),
                )
                .where(
                    CourseRun.organization_id == organization_id,
                    CourseRun.id == course_run_id,
                )
            )
        ).one_or_none()
        if state is None:
            return None
        course_status = cast(CourseState, state[0])
        course_run_status = cast(CourseRunState, state[1])

        selected = await session.scalar(
            select(ReviewerCourseSelection.active).where(
                ReviewerCourseSelection.organization_id == organization_id,
                ReviewerCourseSelection.course_run_id == course_run_id,
                ReviewerCourseSelection.reviewer_id == reviewer_id,
            )
        )
        planned_minutes = await session.scalar(
            select(AvailabilityPlan.planned_minutes).where(
                AvailabilityPlan.organization_id == organization_id,
                AvailabilityPlan.reviewer_id == reviewer_id,
                AvailabilityPlan.until_at >= selected_at,
            )
        )
        assigned_minutes = await self._assigned_minutes(
            session,
            organization_id=organization_id,
            reviewer_id=reviewer_id,
        )
        candidates: tuple[ReviewQueueCandidate, ...] = ()
        if selected is True and course_status == "active" and course_run_status == "active":
            candidate_rows = await self._candidate_rows(
                session,
                organization_id=organization_id,
                course_run_id=course_run_id,
            )
            candidates = await self._materialize_candidates(
                session,
                organization_id=organization_id,
                course_run_id=course_run_id,
                reviewer_id=reviewer_id,
                rows=candidate_rows,
            )
        return RecommendationQueueSnapshot(
            organization_id=organization_id,
            reviewer_id=reviewer_id,
            course_run_id=course_run_id,
            course_status=course_status,
            course_run_status=course_run_status,
            selected=selected is True,
            planned_minutes=int(planned_minutes or 0),
            assigned_minutes=assigned_minutes,
            candidates=candidates,
        )

    @staticmethod
    async def _assigned_minutes(
        session: AsyncSession,
        *,
        organization_id: UUID,
        reviewer_id: UUID,
    ) -> int:
        latest_responsibility = (
            select(
                ReviewResponsibility.organization_id.label("organization_id"),
                ReviewResponsibility.review_case_id.label("review_case_id"),
                ReviewResponsibility.action.label("action"),
                func.row_number()
                .over(
                    partition_by=(
                        ReviewResponsibility.organization_id,
                        ReviewResponsibility.review_case_id,
                        ReviewResponsibility.reviewer_id,
                    ),
                    order_by=(
                        ReviewResponsibility.occurred_at.desc(),
                        ReviewResponsibility.id.desc(),
                    ),
                )
                .label("event_rank"),
            )
            .where(
                ReviewResponsibility.organization_id == organization_id,
                ReviewResponsibility.reviewer_id == reviewer_id,
            )
            .subquery()
        )
        value = await session.scalar(
            select(func.coalesce(func.sum(HomeworkVersion.estimated_review_minutes), 0))
            .select_from(latest_responsibility)
            .join(
                ReviewCase,
                and_(
                    ReviewCase.organization_id == latest_responsibility.c.organization_id,
                    ReviewCase.id == latest_responsibility.c.review_case_id,
                ),
            )
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.organization_id == ReviewCase.organization_id,
                    ReviewIteration.review_case_id == ReviewCase.id,
                    ReviewIteration.id == ReviewCase.current_iteration_id,
                ),
            )
            .join(
                HomeworkVersion,
                and_(
                    HomeworkVersion.organization_id == ReviewIteration.organization_id,
                    HomeworkVersion.id == ReviewIteration.homework_version_id,
                ),
            )
            .where(
                latest_responsibility.c.event_rank == 1,
                latest_responsibility.c.action.in_(_ACTIVE_RESPONSIBILITY_ACTIONS),
                ReviewIteration.status.in_(_OPEN_REVIEW_STATUSES),
            )
        )
        return int(value or 0)

    @staticmethod
    async def _candidate_rows(
        session: AsyncSession,
        *,
        organization_id: UUID,
        course_run_id: UUID,
    ) -> tuple[_CandidateRow, ...]:
        open_rows = (
            await session.execute(
                select(
                    ReviewCase.id,
                    ReviewCase.revision,
                    ReviewIteration.id,
                    SubmissionVersion.id,
                    Submission.course_run_homework_id,
                    SubmissionVersion.homework_version_id,
                    SubmissionVersion.submitted_at,
                    SubmissionVersion.effective_deadline,
                    HomeworkVersion.estimated_review_minutes,
                    ReviewIteration.responsible_reviewer_id,
                )
                .select_from(ReviewCase)
                .join(
                    ReviewIteration,
                    and_(
                        ReviewIteration.organization_id == ReviewCase.organization_id,
                        ReviewIteration.review_case_id == ReviewCase.id,
                        ReviewIteration.id == ReviewCase.current_iteration_id,
                    ),
                )
                .join(
                    SubmissionVersion,
                    and_(
                        SubmissionVersion.organization_id == ReviewIteration.organization_id,
                        SubmissionVersion.id == ReviewIteration.submission_version_id,
                    ),
                )
                .join(
                    Submission,
                    and_(
                        Submission.organization_id == ReviewCase.organization_id,
                        Submission.id == SubmissionVersion.submission_id,
                        Submission.course_run_id == ReviewCase.course_run_id,
                        Submission.homework_id == ReviewCase.homework_id,
                        Submission.student_id == ReviewCase.student_id,
                    ),
                )
                .join(
                    HomeworkVersion,
                    and_(
                        HomeworkVersion.organization_id == SubmissionVersion.organization_id,
                        HomeworkVersion.id == SubmissionVersion.homework_version_id,
                    ),
                )
                .where(
                    ReviewCase.organization_id == organization_id,
                    ReviewCase.course_run_id == course_run_id,
                    ReviewIteration.status.in_(_OPEN_REVIEW_STATUSES),
                )
            )
        ).all()
        unopened_rows = (
            await session.execute(
                select(
                    ReviewCase.id,
                    ReviewCase.revision,
                    SubmissionVersion.id,
                    Submission.course_run_homework_id,
                    SubmissionVersion.homework_version_id,
                    SubmissionVersion.submitted_at,
                    SubmissionVersion.effective_deadline,
                    HomeworkVersion.estimated_review_minutes,
                )
                .select_from(ReviewCase)
                .join(
                    Submission,
                    and_(
                        Submission.organization_id == ReviewCase.organization_id,
                        Submission.course_run_id == ReviewCase.course_run_id,
                        Submission.homework_id == ReviewCase.homework_id,
                        Submission.student_id == ReviewCase.student_id,
                    ),
                )
                .join(
                    SubmissionVersion,
                    and_(
                        SubmissionVersion.organization_id == Submission.organization_id,
                        SubmissionVersion.submission_id == Submission.id,
                        SubmissionVersion.id == Submission.current_predeadline_version_id,
                    ),
                )
                .join(
                    HomeworkVersion,
                    and_(
                        HomeworkVersion.organization_id == SubmissionVersion.organization_id,
                        HomeworkVersion.id == SubmissionVersion.homework_version_id,
                    ),
                )
                .where(
                    ReviewCase.organization_id == organization_id,
                    ReviewCase.course_run_id == course_run_id,
                    ReviewCase.current_iteration_id.is_(None),
                    SubmissionVersion.status == "ready",
                )
            )
        ).all()
        rows = [
            _CandidateRow(
                review_case_id=row[0],
                review_case_revision=row[1],
                review_iteration_id=row[2],
                submission_version_id=row[3],
                course_run_homework_id=row[4],
                homework_version_id=row[5],
                submitted_at=row[6],
                effective_deadline=row[7],
                estimated_review_minutes=row[8],
                responsible_reviewer_id=row[9],
            )
            for row in open_rows
        ]
        rows.extend(
            _CandidateRow(
                review_case_id=row[0],
                review_case_revision=row[1],
                review_iteration_id=None,
                submission_version_id=row[2],
                course_run_homework_id=row[3],
                homework_version_id=row[4],
                submitted_at=row[5],
                effective_deadline=row[6],
                estimated_review_minutes=row[7],
                responsible_reviewer_id=None,
            )
            for row in unopened_rows
        )
        return tuple(sorted(rows, key=lambda row: row.review_case_id.int))

    @staticmethod
    async def _materialize_candidates(
        session: AsyncSession,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        reviewer_id: UUID,
        rows: Sequence[_CandidateRow],
    ) -> tuple[ReviewQueueCandidate, ...]:
        if not rows:
            return ()
        relation_ids = {row.course_run_homework_id for row in rows}
        publications = (
            await session.scalars(
                select(CourseRunHomeworkPublication)
                .where(
                    CourseRunHomeworkPublication.organization_id == organization_id,
                    CourseRunHomeworkPublication.course_run_homework_id.in_(relation_ids),
                )
                .order_by(
                    CourseRunHomeworkPublication.course_run_homework_id,
                    CourseRunHomeworkPublication.publication_sequence,
                )
            )
        ).all()
        deadline_by_input = {
            (
                publication.course_run_homework_id,
                publication.homework_version_id,
                publication.submission_deadline,
            ): publication.review_deadline
            for publication in publications
        }
        case_ids = {row.review_case_id for row in rows}
        iteration_ids = {
            row.review_iteration_id for row in rows if row.review_iteration_id is not None
        }
        responsibility_statement = select(ReviewResponsibility).where(
            ReviewResponsibility.organization_id == organization_id,
            ReviewResponsibility.review_case_id.in_(case_ids),
        )
        if iteration_ids:
            responsibility_statement = responsibility_statement.where(
                or_(
                    ReviewResponsibility.review_iteration_id.is_(None),
                    ReviewResponsibility.review_iteration_id.in_(iteration_ids),
                )
            )
        else:
            responsibility_statement = responsibility_statement.where(
                ReviewResponsibility.review_iteration_id.is_(None)
            )
        responsibilities = (
            await session.scalars(
                responsibility_statement.order_by(
                    ReviewResponsibility.review_case_id,
                    ReviewResponsibility.occurred_at,
                    ReviewResponsibility.id,
                )
            )
        ).all()
        candidates: list[ReviewQueueCandidate] = []
        for row in rows:
            review_deadline = deadline_by_input.get(
                (
                    row.course_run_homework_id,
                    row.homework_version_id,
                    row.effective_deadline,
                )
            )
            if review_deadline is None:
                continue
            latest_actions: dict[UUID, str] = {}
            for event in responsibilities:
                if event.review_case_id != row.review_case_id:
                    continue
                if event.review_iteration_id not in {None, row.review_iteration_id}:
                    continue
                latest_actions[event.reviewer_id] = event.action
            active_reviewers = {
                participant
                for participant, action in latest_actions.items()
                if action in _ACTIVE_RESPONSIBILITY_ACTIONS
            }
            if row.responsible_reviewer_id is not None:
                active_reviewers.add(row.responsible_reviewer_id)
            continuing = (
                reviewer_id if reviewer_id in active_reviewers else row.responsible_reviewer_id
            )
            candidates.append(
                ReviewQueueCandidate(
                    organization_id=organization_id,
                    course_run_id=course_run_id,
                    candidate_id=row.review_case_id,
                    review_case_id=row.review_case_id,
                    review_case_revision=row.review_case_revision,
                    submission_version_id=row.submission_version_id,
                    review_deadline=review_deadline,
                    continuing_reviewer_id=continuing,
                    submitted_at=row.submitted_at,
                    estimated_review_minutes=row.estimated_review_minutes,
                    active_reviewer_count=len(active_reviewers),
                )
            )
        return tuple(candidates)


async def _active_reviewer_exists(
    session: AsyncSession,
    organization_id: UUID,
    reviewer_id: UUID,
) -> bool:
    membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == reviewer_id,
            OrganizationMembership.status == "active",
        )
    )
    return membership is not None and _REVIEWER_ROLE in membership.roles


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise InvalidReviewWorkTransaction(
            "review-work repository requires caller-owned AsyncSession"
        )
    return transaction


_selection_port: ReviewerCourseSelectionRepository = SqlReviewWorkRepository()
_availability_port: ReviewerAvailabilityRepository = SqlReviewWorkRepository()
_recommendation_port: RecommendationRepository = SqlReviewWorkRepository()
_responsibility_port: ReviewResponsibilityRepository = SqlReviewWorkRepository()


__all__ = [
    "InvalidReviewWorkTransaction",
    "ReviewWorkRepositoryError",
    "ReviewWorkScopeConflict",
    "SqlReviewWorkRepository",
]
