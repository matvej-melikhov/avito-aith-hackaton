"""SQL-paginated coordinator registry union of submissions and draft-only work."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import String, and_, case, cast, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import require_roles
from review_platform.application.workspace.insights import has_active_participant
from review_platform.application.workspace.student_alias import student_identifier
from review_platform.contracts.workspace import WorkItem, WorkList
from review_platform.infrastructure.db.models import (
    Course,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Homework,
    ReviewCase,
    ReviewIteration,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import ReviewOutcome, WorkDraft


async def registry_works(
    runtime: FoundationRuntime,
    session: AsyncSession,
    actor: RequestActor,
    *,
    run_id: UUID | None = None,
    homework_id: UUID | None = None,
    search: str = "",
    search_student_only: bool = False,
    state: str = "",
    stuck: bool = False,
    offset: int = 0,
    limit: int = 30,
) -> WorkList:
    from review_platform.application.workspace.projections import WorkspaceQueries

    require_roles(actor, "methodologist")
    org = actor.organization_id
    ver = aliased(SubmissionVersion)
    iteration = aliased(ReviewIteration)
    newest = (
        select(func.max(SubmissionVersion.sequence))
        .where(
            SubmissionVersion.organization_id == org,
            SubmissionVersion.submission_id == Submission.id,
        )
        .correlate(Submission)
        .scalar_subquery()
    )
    status = case(
        (
            and_(
                iteration.status == "published",
                iteration.submission_version_id == ver.id,
                ReviewOutcome.decision.is_not(None),
            ),
            ReviewOutcome.decision,
        ),
        (iteration.submission_version_id == ver.id, iteration.status),
        (ver.id.is_not(None), "pending_review"),
        else_="draft",
    )
    submitted = (
        select(
            Submission.id.label("identity"),
            literal("submission").label("kind"),
            status.label("status"),
            has_active_participant(org, iteration.id).label("active"),
            func.coalesce(ver.sequence, 0).label("attempt"),
            CourseRunHomeworkPublication.review_deadline.label("deadline"),
            func.coalesce(ver.submitted_at, Submission.created_at).label("sort_at"),
        )
        .join(
            CourseRunHomework,
            and_(
                CourseRunHomework.id == Submission.course_run_homework_id,
                CourseRunHomework.organization_id == org,
            ),
        )
        .join(
            Homework, and_(Homework.id == Submission.homework_id, Homework.organization_id == org)
        )
        .outerjoin(
            CourseRunHomeworkPublication,
            and_(
                CourseRunHomeworkPublication.id == CourseRunHomework.current_publication_id,
                CourseRunHomeworkPublication.organization_id == org,
            ),
        )
        .outerjoin(
            ver,
            and_(
                ver.submission_id == Submission.id,
                ver.organization_id == org,
                ver.sequence == newest,
            ),
        )
        .outerjoin(
            ReviewCase,
            and_(
                ReviewCase.organization_id == org,
                ReviewCase.course_run_id == Submission.course_run_id,
                ReviewCase.homework_id == Submission.homework_id,
                ReviewCase.student_id == Submission.student_id,
            ),
        )
        .outerjoin(
            iteration,
            and_(iteration.id == ReviewCase.current_iteration_id, iteration.organization_id == org),
        )
        .outerjoin(
            ReviewOutcome,
            and_(ReviewOutcome.id == iteration.id, ReviewOutcome.organization_id == org),
        )
        .where(Submission.organization_id == org)
    )
    has_submission = (
        select(Submission.id)
        .where(
            Submission.organization_id == org,
            Submission.course_run_homework_id == WorkDraft.publication_id,
            Submission.student_id == WorkDraft.student_id,
        )
        .exists()
    )
    drafts = (
        select(
            WorkDraft.id.label("identity"),
            literal("draft").label("kind"),
            literal("draft").label("status"),
            literal(False).label("active"),
            literal(0).label("attempt"),
            CourseRunHomeworkPublication.review_deadline.label("deadline"),
            WorkDraft.updated_at.label("sort_at"),
        )
        .join(
            CourseRunHomework,
            and_(
                CourseRunHomework.id == WorkDraft.publication_id,
                CourseRunHomework.organization_id == org,
            ),
        )
        .join(
            Homework,
            and_(Homework.id == CourseRunHomework.homework_id, Homework.organization_id == org),
        )
        .outerjoin(
            CourseRunHomeworkPublication,
            and_(
                CourseRunHomeworkPublication.id == CourseRunHomework.current_publication_id,
                CourseRunHomeworkPublication.organization_id == org,
            ),
        )
        .where(WorkDraft.organization_id == org, ~has_submission)
    )
    if run_id:
        submitted = submitted.where(Submission.course_run_id == run_id)
        drafts = drafts.where(CourseRunHomework.course_run_id == run_id)
    if homework_id:
        submitted = submitted.where(Submission.homework_id == homework_id)
        drafts = drafts.where(Homework.id == homework_id)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        submission_match = student_identifier(Submission.student_id).ilike(
            f"%{escaped}%", escape="\\"
        ) | cast(Submission.student_id, String).ilike(f"%{escaped.replace('-', '')}%", escape="\\")
        draft_match = student_identifier(WorkDraft.student_id).ilike(
            f"%{escaped}%", escape="\\"
        ) | cast(WorkDraft.student_id, String).ilike(f"%{escaped.replace('-', '')}%", escape="\\")
        title_match = Homework.title.ilike(f"%{escaped}%", escape="\\")
        submitted = submitted.where(
            submission_match if search_student_only else title_match | submission_match
        )
        drafts = drafts.where(draft_match if search_student_only else title_match | draft_match)
    combined = union_all(submitted, drafts).subquery()
    query = select(combined)
    if stuck:
        query = query.where(
            combined.c.kind == "submission",
            combined.c.active.is_(False),
            combined.c.sort_at < runtime.clock() - timedelta(days=3),
        )
    if state == "reviewing":
        query = query.where(combined.c.status.in_(["in_review", "ready_to_publish"]))
    elif state == "repeat_review":
        query = query.where(
            combined.c.attempt > 1, combined.c.status.in_(["in_review", "ready_to_publish"])
        )
    elif state == "in_review":
        query = query.where(
            combined.c.attempt <= 1, combined.c.status.in_(["in_review", "ready_to_publish"])
        )
    elif state == "in_progress":
        query = query.where(combined.c.status.not_in(["passed", "failed", "published"]))
    elif state == "completed":
        query = query.where(combined.c.status.in_(["passed", "failed", "published"]))
    elif state:
        query = query.where(combined.c.status == state)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    page = (
        await session.execute(
            query.order_by(combined.c.deadline, combined.c.sort_at, combined.c.identity)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    submission_ids = [entry.identity for entry in page if entry.kind == "submission"]
    draft_ids = [entry.identity for entry in page if entry.kind == "draft"]
    items: dict[UUID, WorkItem] = {}
    if submission_ids:
        loaded = await WorkspaceQueries(runtime, session).works(
            actor, view="submitted_registry", submission_ids=submission_ids, limit=limit
        )
        items.update({item.submission_id: item for item in loaded.items if item.submission_id})
    if draft_ids:
        rows = (
            await session.execute(
                select(
                    WorkDraft,
                    CourseRunHomework,
                    Homework,
                    CourseRun,
                    Course,
                    CourseRunHomeworkPublication.submission_deadline,
                    student_identifier(WorkDraft.student_id),
                )
                .join(
                    CourseRunHomework,
                    and_(
                        CourseRunHomework.id == WorkDraft.publication_id,
                        CourseRunHomework.organization_id == org,
                    ),
                )
                .join(
                    Homework,
                    and_(
                        Homework.id == CourseRunHomework.homework_id,
                        Homework.organization_id == org,
                    ),
                )
                .join(
                    CourseRun,
                    and_(
                        CourseRun.id == CourseRunHomework.course_run_id,
                        CourseRun.organization_id == org,
                    ),
                )
                .join(Course, and_(Course.id == CourseRun.course_id, Course.organization_id == org))
                .outerjoin(
                    CourseRunHomeworkPublication,
                    and_(
                        CourseRunHomeworkPublication.id == CourseRunHomework.current_publication_id,
                        CourseRunHomeworkPublication.organization_id == org,
                    ),
                )
                .where(WorkDraft.organization_id == org, WorkDraft.id.in_(draft_ids))
            )
        ).all()
        for draft, relation, homework, run, course, deadline, alias in rows:
            items[draft.id] = WorkItem(
                draft_id=draft.id,
                submission_id=None,
                submitted_at=None,
                updated_at=draft.updated_at,
                submission_revision=0,
                review_submission_version_id=None,
                publication_id=relation.id,
                course_run_id=run.id,
                homework_id=homework.id,
                title=homework.title,
                course_run_title=run.title,
                course_title=course.title,
                student_id=draft.student_id,
                student_name=f"Студент {alias}",
                submission_deadline=deadline,
                submission_version_id=None,
                attempt=0,
                review_case_id=None,
                review_case_revision=0,
                review_iteration_id=None,
                review_revision=0,
                responsible_reviewer_id=None,
                primary_reviewer_id=None,
                status="draft",
                score=None,
                feedback=None,
                published_by=None,
                review_deadline=None,
            )
    return WorkList(
        items=[items[entry.identity] for entry in page],
        total=total or 0,
        offset=offset,
        limit=limit,
    )
