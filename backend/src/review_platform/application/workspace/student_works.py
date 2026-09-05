"""One paginated student cabinet for already-started drafts and submitted work."""

from __future__ import annotations

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import require_roles
from review_platform.application.workspace.grading import published_grades
from review_platform.contracts.workspace import StudentHomeworkItem, StudentHomeworkList
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Homework,
    ReviewCase,
    ReviewIteration,
    ReviewPublication,
    ReviewRevision,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import ReviewOutcome, WorkDraft


async def student_homeworks(
    session: AsyncSession,
    actor: RequestActor,
    *,
    state: str = "",
    offset: int = 0,
    limit: int = 30,
) -> StudentHomeworkList:
    user = require_roles(actor, "student")
    org = actor.organization_id
    latest_version = (
        select(
            SubmissionVersion.id.label("version_id"),
            SubmissionVersion.submission_id,
            func.row_number()
            .over(
                partition_by=SubmissionVersion.submission_id,
                order_by=SubmissionVersion.sequence.desc(),
            )
            .label("rank"),
        )
        .join(
            Submission,
            and_(
                Submission.id == SubmissionVersion.submission_id,
                Submission.organization_id == SubmissionVersion.organization_id,
            ),
        )
        .where(Submission.organization_id == org, Submission.student_id == user)
        .subquery()
    )
    published = (
        select(
            ReviewIteration.review_case_id,
            ReviewIteration.id.label("review_iteration_id"),
            ReviewPublication.id.label("publication_id"),
            ReviewPublication.review_revision_id,
            func.row_number()
            .over(
                partition_by=ReviewIteration.review_case_id,
                order_by=(
                    ReviewIteration.iteration_number.desc(),
                    ReviewPublication.publication_version.desc(),
                ),
            )
            .label("rank"),
        )
        .join(
            ReviewPublication,
            and_(
                ReviewPublication.review_iteration_id == ReviewIteration.id,
                ReviewPublication.organization_id == ReviewIteration.organization_id,
            ),
        )
        .where(
            ReviewIteration.organization_id == org,
            ReviewIteration.student_id == user,
            ReviewPublication.status == "published",
        )
        .subquery()
    )
    version = aliased(SubmissionVersion)
    current = aliased(ReviewIteration)
    status = case(
        (
            and_(
                current.status == "published",
                current.submission_version_id == version.id,
                ReviewOutcome.decision.is_not(None),
            ),
            ReviewOutcome.decision,
        ),
        (
            and_(current.id.is_not(None), current.submission_version_id == version.id),
            current.status,
        ),
        (version.id.is_not(None), "pending_review"),
        else_="draft",
    )
    query = (
        select(
            CourseRunHomework.id,
            Homework.title,
            Course.title,
            CourseRun.title,
            CourseRunHomeworkPublication.submission_deadline,
            status,
            version.sequence,
            Submission.id,
            WorkDraft.id,
            published.c.publication_id,
            ReviewRevision.total_score,
            case(
                (
                    and_(
                        current.status == "published",
                        current.submission_version_id == version.id,
                        published.c.review_iteration_id == current.id,
                        ReviewOutcome.decision == "needs_changes",
                    ),
                    ReviewOutcome.revision_deadline,
                ),
                else_=None,
            ).label("revision_deadline"),
        )
        .join(
            CourseMembership,
            and_(
                CourseMembership.course_run_id == CourseRunHomework.course_run_id,
                CourseMembership.organization_id == CourseRunHomework.organization_id,
                CourseMembership.user_id == user,
                CourseMembership.kind == "student",
                CourseMembership.status == "active",
            ),
        )
        .join(
            CourseRunHomeworkPublication,
            and_(
                CourseRunHomeworkPublication.id == CourseRunHomework.current_publication_id,
                CourseRunHomeworkPublication.organization_id == CourseRunHomework.organization_id,
            ),
        )
        .join(
            Homework,
            and_(
                Homework.id == CourseRunHomework.homework_id,
                Homework.organization_id == CourseRunHomework.organization_id,
            ),
        )
        .join(
            CourseRun,
            and_(
                CourseRun.id == CourseRunHomework.course_run_id,
                CourseRun.organization_id == CourseRunHomework.organization_id,
            ),
        )
        .join(
            Course,
            and_(
                Course.id == CourseRun.course_id,
                Course.organization_id == CourseRun.organization_id,
            ),
        )
        .outerjoin(
            Submission,
            and_(
                Submission.course_run_homework_id == CourseRunHomework.id,
                Submission.organization_id == org,
                Submission.student_id == user,
            ),
        )
        .outerjoin(
            WorkDraft,
            and_(
                WorkDraft.publication_id == CourseRunHomework.id,
                WorkDraft.organization_id == org,
                WorkDraft.student_id == user,
            ),
        )
        .outerjoin(
            latest_version,
            and_(latest_version.c.submission_id == Submission.id, latest_version.c.rank == 1),
        )
        .outerjoin(
            version, and_(version.id == latest_version.c.version_id, version.organization_id == org)
        )
        .outerjoin(
            ReviewCase,
            and_(
                ReviewCase.course_run_id == CourseRunHomework.course_run_id,
                ReviewCase.homework_id == CourseRunHomework.homework_id,
                ReviewCase.organization_id == org,
                ReviewCase.student_id == user,
            ),
        )
        .outerjoin(
            current,
            and_(current.id == ReviewCase.current_iteration_id, current.organization_id == org),
        )
        .outerjoin(
            ReviewOutcome,
            and_(ReviewOutcome.id == current.id, ReviewOutcome.organization_id == org),
        )
        .outerjoin(
            published, and_(published.c.review_case_id == ReviewCase.id, published.c.rank == 1)
        )
        .outerjoin(
            ReviewRevision,
            and_(
                ReviewRevision.id == published.c.review_revision_id,
                ReviewRevision.organization_id == org,
            ),
        )
        .where(
            CourseRunHomework.organization_id == org,
            or_(WorkDraft.id.is_not(None), Submission.id.is_not(None)),
        )
    )
    if state == "in_progress":
        query = query.where(status.not_in(["passed", "failed", "published"]))
    elif state == "completed":
        query = query.where(status.in_(["passed", "failed", "published"]))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.execute(
            query.order_by(CourseRunHomeworkPublication.submission_deadline, CourseRunHomework.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    grades = await published_grades(session, org, [row[9] for row in rows if row[9]])
    return StudentHomeworkList(
        items=[
            StudentHomeworkItem(
                publication_id=publication_id,
                title=title,
                course_title=course_title,
                course_run_title=run_title,
                submission_deadline=deadline,
                revision_deadline=revision_deadline,
                status=item_status,
                attempt=attempt or 0,
                submission_id=submission_id,
                draft_id=draft_id,
                score=grades[grade_id].final_score
                if grade_id in grades
                else float(raw_score)
                if raw_score is not None
                else None,
            )
            for (
                publication_id,
                title,
                course_title,
                run_title,
                deadline,
                item_status,
                attempt,
                submission_id,
                draft_id,
                grade_id,
                raw_score,
                revision_deadline,
            ) in rows
        ],
        total=total or 0,
        offset=offset,
        limit=limit,
    )
