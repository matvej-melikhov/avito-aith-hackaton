"""Scoped workspace read models with server pagination and published-result continuity."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import String, and_, case, false, func, select, true
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import WorkspaceFailure, course_scope, row
from review_platform.application.workspace.grading import published_grades
from review_platform.application.workspace.self_review import SelfReviewService, draft_view
from review_platform.application.workspace.student_alias import student_identifier, student_labels
from review_platform.contracts.workspace import (
    CriterionSettings,
    PrivateHomeworkView,
    PublicationPolicyInput,
    PublicCriterion,
    QuotaView,
    ReviewContext,
    ReviewCriterionView,
    StatisticView,
    StudentContext,
    WorkItem,
    WorkList,
)
from review_platform.infrastructure.db.models import (
    Course,
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
    ReviewResponsibility,
    ReviewRevision,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.models.workspace import (
    HomeworkPrivateDetails,
    PublicationPolicy,
    ReviewAIChoice,
    ReviewAssistRun,
    ReviewOutcome,
    SelfReviewRun,
    StudentReviewerAssignment,
    WorkDraft,
    WorkspacePreferences,
    WorkspaceRunSettings,
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
        run = await row(self.session, CourseRun, actor.organization_id, publication.course_run_id)
        course = await row(self.session, Course, actor.organization_id, run.course_id)
        submission_id = await self.session.scalar(
            select(Submission.id).where(
                Submission.organization_id == actor.organization_id,
                Submission.course_run_homework_id == publication.id,
                Submission.student_id == actor.user_id,
            )
        )
        private = await self.session.scalar(
            select(HomeworkPrivateDetails).where(
                HomeworkPrivateDetails.id == homework.id,
                HomeworkPrivateDetails.organization_id == actor.organization_id,
            )
        )
        return StudentContext(
            material_upload_ids=[UUID(value) for value in (private.material_upload_ids or [])]
            if private
            else [],
            course_title=course.title,
            run_title=run.title,
            max_score=float(homework.max_score),
            submission_id=submission_id,
            publication_id=publication.id,
            homework_id=homework.homework_id,
            homework_version_id=homework.id,
            course_run_id=publication.course_run_id,
            title=title.title,
            student_text=homework.student_text,
            criteria=[
                PublicCriterion(
                    id=c.id, key=c.stable_key, title=c.title, max_points=float(c.max_points)
                )
                for c in criteria
            ],
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
        priority: str | None = None,
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
                ReviewPublication.id.label("publication_id"),
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
        participation = (
            select(
                ReviewResponsibility.review_iteration_id,
                ReviewResponsibility.action,
                func.row_number()
                .over(
                    partition_by=ReviewResponsibility.review_iteration_id,
                    order_by=(
                        ReviewResponsibility.occurred_at.desc(),
                        ReviewResponsibility.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                ReviewResponsibility.organization_id == org,
                ReviewResponsibility.reviewer_id == actor.user_id,
            )
            .subquery()
        )
        version = aliased(SubmissionVersion)
        current = aliased(ReviewIteration)
        revision = aliased(ReviewRevision)
        publication_history = aliased(CourseRunHomeworkPublication)
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
                Submission,
                func.concat("Студент ", student_identifier(Submission.student_id)),
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
                Course.title,
                publication_history.submission_deadline,
                published.c.publication_id,
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
            .join(Course, and_(Course.id == CourseRun.course_id, Course.organization_id == org))
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
                participation,
                and_(participation.c.review_iteration_id == current.id, participation.c.rank == 1),
            )
            .outerjoin(
                WorkspaceRunSettings,
                and_(
                    WorkspaceRunSettings.id == Submission.course_run_id,
                    WorkspaceRunSettings.organization_id == org,
                ),
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
            uuid_query = escaped.replace("-", "")
            query = query.where(
                (Homework.title.ilike(f"%{escaped}%", escape="\\"))
                | (student_identifier(Submission.student_id).ilike(f"%{escaped}%", escape="\\"))
                | (sql_cast(Submission.student_id, String).ilike(f"%{uuid_query}%", escape="\\"))
            )
        if state == "in_progress":
            query = query.where(status.not_in(["passed", "failed", "published"]))
        elif state == "completed":
            query = query.where(status.in_(["passed", "failed", "published"]))
        elif state:
            query = query.where(status == state)
        if view == "assigned":
            query = query.where(StudentReviewerAssignment.reviewer_id == actor.user_id)
        if view == "pool":
            query = query.where(status.in_(["pending_review", "in_review", "ready_to_publish"]))
        if view == "active":
            query = query.where(
                participation.c.action.in_(["started", "joined"])
                | and_(
                    participation.c.action.is_(None),
                    current.responsible_reviewer_id == actor.user_id,
                ),
            )
        if actor.roles.intersection({"reviewer", "methodologist"}):
            preferences = await self.session.scalar(
                select(WorkspacePreferences).where(
                    WorkspacePreferences.organization_id == org,
                    WorkspacePreferences.user_id == actor.user_id,
                )
            )
            from review_platform.contracts.workspace import PreferencesInput

            saved = PreferencesInput.model_validate(preferences.settings) if preferences else None
            absent = bool(
                saved
                and saved.absent_from
                and saved.absent_until
                and saved.absent_from <= self.runtime.clock() < saved.absent_until
            )
            if view == "pool" and saved:
                query = query.where(Submission.course_run_id.in_(saved.course_run_ids))
                if not saved.show_pool or absent:
                    query = query.where(false())
            # Absence and priorities affect recommendations only, never access.
            if not absent and priority != "deadline":
                assigned_policy = (
                    (func.coalesce(WorkspaceRunSettings.priority, "assigned") == "assigned")
                    if priority is None
                    else true()
                )
                query = query.order_by(
                    case(
                        (
                            and_(
                                assigned_policy,
                                StudentReviewerAssignment.reviewer_id == actor.user_id,
                            ),
                            0,
                        ),
                        else_=1,
                    )
                )
        total = await self.session.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        query = (
            query.order_by(
                publication_history.review_deadline, Submission.created_at, Submission.id
            )
            .offset(offset)
            .limit(limit)
        )
        entries = (await self.session.execute(query)).all()
        grades = await published_grades(
            self.session, org, [entry[-1] for entry in entries if entry[-1] is not None]
        )
        iteration_ids = [entry[6].id for entry in entries if entry[6] is not None]
        events = (
            await self.session.scalars(
                select(ReviewResponsibility)
                .where(
                    ReviewResponsibility.organization_id == org,
                    ReviewResponsibility.review_iteration_id.in_(iteration_ids),
                )
                .order_by(ReviewResponsibility.occurred_at, ReviewResponsibility.id)
            )
        ).all()
        taken: dict[UUID, datetime] = {}
        participants: dict[UUID, dict[UUID, str]] = {}
        for event in events:
            if event.review_iteration_id is None:
                continue
            if event.action in {"started", "joined"}:
                taken.setdefault(event.review_iteration_id, event.occurred_at)
            participants.setdefault(event.review_iteration_id, {})[event.reviewer_id] = event.action
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
            course_title,
            submission_deadline,
            publication_id,
        ) in entries:
            items.append(
                WorkItem(
                    course_title=course_title,
                    submission_deadline=submission_deadline,
                    taken_at=taken.get(iteration.id) if iteration else None,
                    participant_ids=[
                        identity
                        for identity, action in participants.get(iteration.id, {}).items()
                        if action in {"started", "joined"}
                    ]
                    if iteration
                    else [],
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
                    score=grades[publication_id].final_score
                    if publication_id in grades
                    else float(pub_revision.total_score)
                    if pub_revision
                    else None,
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
        review_homework = await row(
            self.session, Homework, actor.organization_id, homework.homework_id
        )
        submitted = await row(
            self.session, SubmissionVersion, actor.organization_id, iteration.submission_version_id
        )
        choice = (
            await self.session.scalar(
                select(ReviewAIChoice).where(
                    ReviewAIChoice.organization_id == actor.organization_id,
                    ReviewAIChoice.id == iteration.current_revision_id,
                )
            )
            if iteration.current_revision_id
            else None
        )
        from review_platform.infrastructure.db.models import ArtifactReference, ArtifactVersion
        from review_platform.infrastructure.db.models.workspace import WorkspaceArtifact

        case = await row(self.session, ReviewCase, actor.organization_id, iteration.review_case_id)
        snapshot = await self.session.scalar(
            select(WorkspaceArtifact).where(
                WorkspaceArtifact.organization_id == actor.organization_id,
                WorkspaceArtifact.id == iteration.artifact_version_id,
            )
        )
        source = await self.session.scalar(
            select(ArtifactReference)
            .join(
                ArtifactVersion,
                (ArtifactVersion.artifact_reference_id == ArtifactReference.id)
                & (ArtifactVersion.organization_id == ArtifactReference.organization_id),
            )
            .where(
                ArtifactVersion.organization_id == actor.organization_id,
                ArtifactVersion.id == iteration.artifact_version_id,
            )
        )
        label = (
            source.original_url
            if source and source.original_url.startswith("https:")
            else snapshot.filename
            if snapshot
            else "Снимок работы"
        )
        return ReviewContext(
            submission_id=submitted.submission_id,
            latest_review_iteration_id=case.current_iteration_id,
            artifact_label=label,
            ai_run_id=choice.run_id if choice else None,
            signal_decisions=cast(
                dict[str, Literal["confirm", "reject"]], choice.signal_decisions or {}
            )
            if choice
            else {},
            title=review_homework.title,
            student_name=(
                await student_labels(self.session, actor.organization_id, [iteration.student_id])
            )[iteration.student_id],
            attempt=submitted.sequence,
            submitted_at=submitted.submitted_at,
            homework_id=homework.homework_id,
            criterion_set_id=iteration.criterion_set_id,
            homework_version_id=homework.id,
            student_text=homework.student_text,
            max_score=float(homework.max_score),
            criteria=[
                ReviewCriterionView(
                    **CriterionSettings.model_validate(
                        (private.criterion_settings or {}).get(c.stable_key, {}) if private else {}
                    ).model_dump(),
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
                material_upload_ids=[UUID(value) for value in (private.material_upload_ids or [])],
                criterion_settings={
                    key: CriterionSettings.model_validate(value)
                    for key, value in (private.criterion_settings or {}).items()
                },
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
        self,
        actor: RequestActor,
        start: datetime,
        end: datetime,
        course_run_id: UUID | None = None,
    ) -> StatisticView:
        from review_platform.application.workspace.statistics import (
            PublishedMeasurement,
            summarize_statistics,
        )
        from review_platform.contracts.workspace import ReviewAssistResult

        if not actor.roles.intersection({"reviewer", "methodologist"}):
            raise WorkspaceFailure("forbidden", "Статистика недоступна.", 403)
        if course_run_id is not None:
            await course_scope(self.session, actor, course_run_id)
        # The deadline is from the publication effective when the work was submitted,
        # not a coordinator's later publication of changed dates.
        deadline = (
            select(CourseRunHomeworkPublication.review_deadline)
            .where(
                CourseRunHomeworkPublication.organization_id == actor.organization_id,
                CourseRunHomeworkPublication.course_run_homework_id
                == Submission.course_run_homework_id,
                CourseRunHomeworkPublication.published_at <= SubmissionVersion.submitted_at,
            )
            .order_by(CourseRunHomeworkPublication.publication_sequence.desc())
            .limit(1)
            .scalar_subquery()
        )
        query = (
            select(ReviewPublication, ReviewIteration, SubmissionVersion, deadline)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.id == ReviewPublication.review_iteration_id,
                    ReviewIteration.organization_id == ReviewPublication.organization_id,
                ),
            )
            .join(
                SubmissionVersion,
                and_(
                    SubmissionVersion.id == ReviewIteration.submission_version_id,
                    SubmissionVersion.organization_id == ReviewIteration.organization_id,
                ),
            )
            .join(
                Submission,
                and_(
                    Submission.id == SubmissionVersion.submission_id,
                    Submission.organization_id == SubmissionVersion.organization_id,
                ),
            )
            .where(
                ReviewPublication.organization_id == actor.organization_id,
                ReviewPublication.published_at >= start,
                ReviewPublication.published_at < end,
            )
        )
        if course_run_id is not None:
            query = query.where(ReviewIteration.course_run_id == course_run_id)
        if "methodologist" not in actor.roles:
            allowed = select(CourseMembership.course_run_id).where(
                CourseMembership.organization_id == actor.organization_id,
                CourseMembership.user_id == actor.user_id,
                CourseMembership.kind == "reviewer",
                CourseMembership.status == "active",
            )
            query = query.where(ReviewIteration.course_run_id.in_(allowed))
        entries = (await self.session.execute(query)).all()
        revision_ids = [publication.review_revision_id for publication, _, _, _ in entries]
        decisions = (
            await self.session.execute(
                select(ReviewCriterionDecision, Criterion)
                .join(
                    Criterion,
                    and_(
                        Criterion.id == ReviewCriterionDecision.criterion_id,
                        Criterion.organization_id == ReviewCriterionDecision.organization_id,
                    ),
                )
                .where(
                    ReviewCriterionDecision.organization_id == actor.organization_id,
                    ReviewCriterionDecision.review_revision_id.in_(revision_ids),
                )
            )
        ).all()
        choices = (
            await self.session.execute(
                select(ReviewAIChoice.id, ReviewAssistRun.result)
                .join(
                    ReviewAssistRun,
                    and_(
                        ReviewAssistRun.id == ReviewAIChoice.run_id,
                        ReviewAssistRun.organization_id == ReviewAIChoice.organization_id,
                    ),
                )
                .where(
                    ReviewAIChoice.organization_id == actor.organization_id,
                    ReviewAIChoice.id.in_(revision_ids),
                    ReviewAssistRun.status == "succeeded",
                )
            )
        ).all()
        proposals = {
            (revision_id, suggestion.criterion_id): float(suggestion.proposed_points)
            for revision_id, result in choices
            if result is not None
            for suggestion in ReviewAssistResult.model_validate(result).suggestions
            if suggestion.status == "suggested" and suggestion.proposed_points is not None
        }
        by_revision: dict[UUID, dict[UUID, tuple[str, float, float, float | None]]] = {}
        for decision, criterion in decisions:
            by_revision.setdefault(decision.review_revision_id, {})[criterion.id] = (
                criterion.title,
                float(criterion.max_points),
                float(decision.points),
                proposals.get((decision.review_revision_id, criterion.id)),
            )
        records = [
            PublishedMeasurement(
                publication_id=publication.id,
                reviewer_id=publication.published_by,
                artifact_id=iteration.artifact_version_id,
                rubric_id=iteration.criterion_set_id,
                published_at=publication.published_at,
                opened_at=iteration.created_at,
                submitted_at=version.submitted_at,
                review_deadline=review_deadline,
                attempt=version.sequence,
                decisions=by_revision.get(publication.review_revision_id, {}),
            )
            for publication, iteration, version, review_deadline in entries
        ]
        return summarize_statistics(
            records, actor.user_id if "methodologist" not in actor.roles else None, start, end
        )
