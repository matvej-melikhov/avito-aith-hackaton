"""Scoped workspace read models with server pagination and published-result continuity."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from typing import Literal, cast

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import WorkspaceFailure, course_scope, row
from review_platform.application.workspace.self_review import SelfReviewService, draft_view
from review_platform.contracts.workspace import (
    PrivateHomeworkView,
    PublicCriterion,
    PublicationPolicyInput,
    QuotaView,
    ReviewContext,
    ReviewCriterionView,
    StatisticView,
    StudentContext,
    WorkItem,
    WorkList,
)
from review_platform.infrastructure.db.models import (
    CourseMembership,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
    ReviewCase,
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewPublication,
    ReviewRevision,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.models.workspace import (
    HomeworkPrivateDetails,
    PublicationPolicy,
    ReviewOutcome,
    SelfReviewRun,
    StudentReviewerAssignment,
    WorkDraft,
)


class WorkspaceQueries:
    def __init__(self, runtime: FoundationRuntime, session: AsyncSession):
        self.runtime, self.session = runtime, session

    async def student_context(self, actor: RequestActor, publication_id: UUID) -> StudentContext:
        publication = await row(
            self.session, CourseRunHomework, actor.organization_id, publication_id
        )
        await course_scope(self.session, actor, publication.course_run_id)
        if publication.current_publication_id is None:
            raise WorkspaceFailure("not_published", "Задание не опубликовано.", 404)
        published = await row(
            self.session,
            CourseRunHomeworkPublication,
            actor.organization_id,
            publication.current_publication_id,
        )
        homework = await row(
            self.session, HomeworkVersion, actor.organization_id, published.homework_version_id
        )
        title = await row(self.session, Homework, actor.organization_id, publication.homework_id)
        criteria = (
            await self.session.scalars(
                select(Criterion)
                .join(
                    CriterionSet,
                    and_(
                        CriterionSet.id == Criterion.criterion_set_id,
                        CriterionSet.organization_id == Criterion.organization_id,
                    ),
                )
                .where(
                    Criterion.organization_id == actor.organization_id,
                    CriterionSet.homework_version_id == homework.id,
                )
                .order_by(Criterion.position)
            )
        ).all()
        draft = await self.session.scalar(
            select(WorkDraft).where(
                WorkDraft.organization_id == actor.organization_id,
                WorkDraft.publication_id == publication_id,
                WorkDraft.student_id == actor.user_id,
            )
        )
        policy = await self.session.scalar(
            select(PublicationPolicy).where(
                PublicationPolicy.organization_id == actor.organization_id,
                PublicationPolicy.id == publication_id,
            )
        )
        service = SelfReviewService(self.runtime, self.session)
        quota = None
        views = []
        if policy:
            if draft:
                _, quota = await service.get_quota(draft, policy)
                runs = (
                    await self.session.scalars(
                        select(SelfReviewRun)
                        .where(
                            SelfReviewRun.organization_id == actor.organization_id,
                            SelfReviewRun.draft_id == draft.id,
                        )
                        .order_by(SelfReviewRun.created_at.desc(), SelfReviewRun.id.desc())
                    )
                ).all()
                views = [await service.view(run, draft=draft) for run in runs]
            else:
                quota = QuotaView(
                    limit=policy.self_review_limit,
                    used=0,
                    reserved=0,
                    remaining=policy.self_review_limit,
                    policy_revision=policy.revision,
                )
        return StudentContext(
            publication_id=publication.id,
            homework_id=homework.homework_id,
            homework_version_id=homework.id,
            course_run_id=publication.course_run_id,
            title=title.title,
            student_text=homework.student_text,
            criteria=[PublicCriterion(id=c.id, key=c.stable_key, title=c.title) for c in criteria],
            submission_deadline=published.submission_deadline,
            draft=draft_view(draft) if draft else None,
            quota=quota,
            self_reviews=views,
            policy=PublicationPolicyInput.model_validate(policy.policy) if policy else None,
        )

    async def works(
        self,
        actor: RequestActor,
        *,
        run_id: UUID | None = None,
        homework_id: UUID | None = None,
        search: str = "",
        state: str = "",
        view: str = "all",
        priority: str = "assigned",
        offset: int = 0,
        limit: int = 30,
    ) -> WorkList:
        org = actor.organization_id
        # Immutable latest submitted version and latest published iteration are
        # ranked independently. An unpublished correction cannot hide a grade.
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
            .where(SubmissionVersion.organization_id == org)
            .subquery()
        )
        published = (
            select(
                ReviewIteration.review_case_id,
                ReviewPublication.review_revision_id,
                ReviewPublication.published_by,
                ReviewPublication.review_iteration_id,
                func.row_number()
                .over(
                    partition_by=ReviewIteration.review_case_id,
                    order_by=ReviewIteration.iteration_number.desc(),
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
            .where(ReviewIteration.organization_id == org)
            .subquery()
        )
        version = aliased(SubmissionVersion)
        current = aliased(ReviewIteration)
        revision = aliased(ReviewRevision)
        publication_history = aliased(CourseRunHomeworkPublication)
        status = case(
            (
                and_(current.status == "published", ReviewOutcome.decision.is_not(None)),
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
                Submission,
                User.display_name,
                Homework.title,
                CourseRun.title,
                version,
                ReviewCase,
                current,
                revision,
                StudentReviewerAssignment.reviewer_id,
                published.c.published_by,
                publication_history.review_deadline,
                status.label("workspace_status"),
            )
            .join(User, User.id == Submission.student_id)
            .join(
                Homework,
                and_(
                    Homework.id == Submission.homework_id,
                    Homework.organization_id == Submission.organization_id,
                ),
            )
            .join(
                CourseRun,
                and_(
                    CourseRun.id == Submission.course_run_id,
                    CourseRun.organization_id == Submission.organization_id,
                ),
            )
            .outerjoin(
                latest_version,
                and_(latest_version.c.submission_id == Submission.id, latest_version.c.rank == 1),
            )
            .outerjoin(version, version.id == latest_version.c.version_id)
            .outerjoin(
                ReviewCase,
                and_(
                    ReviewCase.organization_id == Submission.organization_id,
                    ReviewCase.course_run_id == Submission.course_run_id,
                    ReviewCase.homework_id == Submission.homework_id,
                    ReviewCase.student_id == Submission.student_id,
                ),
            )
            .outerjoin(
                current,
                and_(current.id == ReviewCase.current_iteration_id, current.organization_id == org),
            )
            .outerjoin(
                published, and_(published.c.review_case_id == ReviewCase.id, published.c.rank == 1)
            )
            .outerjoin(
                revision,
                and_(
                    revision.id == published.c.review_revision_id, revision.organization_id == org
                ),
            )
            .outerjoin(
                ReviewOutcome,
                and_(
                    ReviewOutcome.id == published.c.review_iteration_id,
                    ReviewOutcome.organization_id == org,
                ),
            )
            .outerjoin(
                StudentReviewerAssignment,
                and_(
                    StudentReviewerAssignment.organization_id == org,
                    StudentReviewerAssignment.course_run_id == Submission.course_run_id,
                    StudentReviewerAssignment.student_id == Submission.student_id,
                ),
            )
            .join(
                CourseRunHomework,
                and_(
                    CourseRunHomework.id == Submission.course_run_homework_id,
                    CourseRunHomework.organization_id == org,
                ),
            )
            .outerjoin(
                publication_history,
                and_(
                    publication_history.id == CourseRunHomework.current_publication_id,
                    publication_history.organization_id == org,
                ),
            )
            .where(Submission.organization_id == org)
        )
        if not actor.roles.intersection({"reviewer", "methodologist"}):
            query = query.where(Submission.student_id == actor.user_id)
        elif "methodologist" not in actor.roles:
            allowed = select(CourseMembership.course_run_id).where(
                CourseMembership.organization_id == org,
                CourseMembership.user_id == actor.user_id,
                CourseMembership.kind == "reviewer",
                CourseMembership.status == "active",
            )
            query = query.where(Submission.course_run_id.in_(allowed))
        if run_id:
            query = query.where(Submission.course_run_id == run_id)
        if homework_id:
            query = query.where(Submission.homework_id == homework_id)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(
                (Homework.title.ilike(f"%{escaped}%", escape="\\"))
                | (User.display_name.ilike(f"%{escaped}%", escape="\\"))
            )
        if state:
            query = query.where(status == state)
        if view == "assigned":
            query = query.where(StudentReviewerAssignment.reviewer_id == actor.user_id)
        if view == "active":
            query = query.where(
                current.responsible_reviewer_id == actor.user_id,
                current.status.in_(["in_review", "ready_to_publish"]),
            )
        total = await self.session.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        if priority == "assigned" and actor.roles.intersection({"reviewer", "methodologist"}):
            query = query.order_by(
                case((StudentReviewerAssignment.reviewer_id == actor.user_id, 0), else_=1)
            )
        query = (
            query.order_by(
                publication_history.review_deadline, Submission.created_at, Submission.id
            )
            .offset(offset)
            .limit(limit)
        )
        entries = (await self.session.execute(query)).all()
        items = []
        for (
            sub,
            student_name,
            title,
            run_title,
            ver,
            review_case,
            iteration,
            pub_revision,
            primary,
            published_by,
            deadline,
            item_status,
        ) in entries:
            items.append(
                WorkItem(
                    submission_id=sub.id,
                    submission_revision=sub.revision,
                    review_submission_version_id=iteration.submission_version_id
                    if iteration
                    else None,
                    publication_id=sub.course_run_homework_id,
                    course_run_id=sub.course_run_id,
                    homework_id=sub.homework_id,
                    title=title,
                    course_run_title=run_title,
                    student_id=sub.student_id,
                    student_name=student_name,
                    submitted_at=ver.submitted_at if ver else sub.created_at,
                    submission_version_id=ver.id if ver else None,
                    attempt=ver.sequence if ver else 0,
                    review_case_id=review_case.id if review_case else None,
                    review_case_revision=review_case.revision if review_case else 0,
                    review_iteration_id=iteration.id if iteration else None,
                    review_revision=iteration.revision if iteration else 0,
                    responsible_reviewer_id=iteration.responsible_reviewer_id
                    if iteration
                    else None,
                    primary_reviewer_id=primary,
                    status=item_status,
                    score=float(pub_revision.total_score) if pub_revision else None,
                    feedback=pub_revision.feedback if pub_revision else None,
                    published_by=published_by,
                    review_deadline=deadline,
                )
            )
        return WorkList(items=items, total=total or 0, offset=offset, limit=limit)

    async def review_context(self, actor: RequestActor, identity: UUID) -> ReviewContext:
        if not actor.roles.intersection({"reviewer", "methodologist"}):
            raise WorkspaceFailure("forbidden", "Ревью доступно проверяющим.", 403)
        iteration = await row(self.session, ReviewIteration, actor.organization_id, identity)
        await course_scope(self.session, actor, iteration.course_run_id)
        homework = await row(
            self.session, HomeworkVersion, actor.organization_id, iteration.homework_version_id
        )
        criteria = (
            await self.session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == actor.organization_id,
                    Criterion.criterion_set_id == iteration.criterion_set_id,
                )
                .order_by(Criterion.position)
            )
        ).all()
        private = await self.session.scalar(
            select(HomeworkPrivateDetails).where(
                HomeworkPrivateDetails.organization_id == actor.organization_id,
                HomeworkPrivateDetails.id == homework.id,
            )
        )
        outcome = await self.session.scalar(
            select(ReviewOutcome).where(
                ReviewOutcome.organization_id == actor.organization_id,
                ReviewOutcome.id == iteration.id,
            )
        )
        publication = await self.session.scalar(
            select(CourseRunHomework).where(
                CourseRunHomework.organization_id == actor.organization_id,
                CourseRunHomework.course_run_id == iteration.course_run_id,
                CourseRunHomework.homework_id == iteration.homework_id,
            )
        )
        draft = (
            await self.session.scalar(
                select(WorkDraft).where(
                    WorkDraft.organization_id == actor.organization_id,
                    WorkDraft.publication_id == publication.id,
                    WorkDraft.student_id == iteration.student_id,
                )
            )
            if publication
            else None
        )
        runs = (
            (
                await self.session.scalars(
                    select(SelfReviewRun)
                    .where(
                        SelfReviewRun.organization_id == actor.organization_id,
                        SelfReviewRun.draft_id == draft.id,
                    )
                    .order_by(SelfReviewRun.created_at)
                )
            ).all()
            if draft
            else []
        )
        from review_platform.contracts.workspace import OutcomeInput

        service = SelfReviewService(self.runtime, self.session)
        return ReviewContext(
            homework_id=homework.homework_id,
            criterion_set_id=iteration.criterion_set_id,
            homework_version_id=homework.id,
            student_text=homework.student_text,
            max_score=float(homework.max_score),
            criteria=[
                ReviewCriterionView(
                    id=c.id,
                    key=c.stable_key,
                    title=c.title,
                    description=c.description,
                    max_points=float(c.max_points),
                    position=c.position,
                )
                for c in criteria
            ],
            private_details=PrivateHomeworkView(
                revision=private.revision,
                reviewer_guidance=private.reviewer_guidance,
                reference_upload_id=private.reference_upload_id,
                criterion_classes=cast(
                    dict[str, Literal["formal", "content", "judgement"]], private.criterion_classes
                ),
            )
            if private
            else None,
            self_reviews=[await service.view(run, draft=draft) for run in runs],
            outcome=OutcomeInput(
                decision=cast(Literal["needs_changes", "passed", "failed"], outcome.decision),
                revision_deadline=outcome.revision_deadline,
                reason=outcome.reason,
            )
            if outcome
            else None,
            outcome_revision=outcome.revision if outcome else 0,
        )

    async def statistics(
        self, actor: RequestActor, start: datetime, end: datetime
    ) -> StatisticView:
        if not actor.roles.intersection({"reviewer", "methodologist"}):
            raise WorkspaceFailure("forbidden", "Статистика недоступна.", 403)
        query = (
            select(ReviewPublication, ReviewIteration)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.id == ReviewPublication.review_iteration_id,
                    ReviewIteration.organization_id == ReviewPublication.organization_id,
                ),
            )
            .where(
                ReviewPublication.organization_id == actor.organization_id,
                ReviewPublication.published_at >= start,
                ReviewPublication.published_at < end,
            )
        )
        if "methodologist" not in actor.roles:
            query = query.where(ReviewPublication.published_by == actor.user_id)
        entries = (await self.session.execute(query)).all()
        durations = [(p.published_at - i.created_at).total_seconds() / 60 for p, i in entries]
        revision_ids = [p.review_revision_id for p, _ in entries]
        decisions = (
            await self.session.scalars(
                select(ReviewCriterionDecision).where(
                    ReviewCriterionDecision.organization_id == actor.organization_id,
                    ReviewCriterionDecision.review_revision_id.in_(revision_ids),
                    ReviewCriterionDecision.ai_suggestion_id.is_not(None),
                )
            )
        ).all()
        return StatisticView(
            publications=len(entries),
            average_elapsed_minutes=sum(durations) / len(durations) if durations else None,
            changed_decisions=sum(d.decision == "changed" for d in decisions),
            compared_decisions=len(decisions),
            from_date=start,
            until_date=end,
        )
