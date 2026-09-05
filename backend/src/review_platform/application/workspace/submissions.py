"""Submit prepared upload snapshots and project only published student results."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.workspace.common import (
    WorkspaceFailure,
    course_scope,
    require_roles,
    revision,
    row,
)
from review_platform.application.workspace.grading import (
    published_grades,
    snapshot_submission_policy,
    validate_submission_limit,
)
from review_platform.application.workspace.preparation import prepared_artifact
from review_platform.contracts.workspace import (
    PublishedCriterionView,
    ResourceResult,
    StudentReviewView,
    StudentSubmissionView,
    SubmissionAttemptView,
)
from review_platform.infrastructure.db.models import (
    ArtifactReference,
    ArtifactVersion,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    Homework,
    Operation,
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewPublication,
    ReviewRevision,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    ReviewOutcome,
    WorkDraft,
    WorkspaceArtifact,
)


async def submit_uploaded_draft(
    runtime: FoundationRuntime,
    session: AsyncSession,
    actor: RequestActor,
    identity: UUID,
    expected: int,
) -> ResourceResult:
    user = require_roles(actor, "student")
    draft = await row(session, WorkDraft, actor.organization_id, identity)
    if draft.student_id != user:
        raise WorkspaceFailure("forbidden", "Черновик недоступен.", 403)
    revision(draft.revision, expected)
    publication = await row(
        session, CourseRunHomework, actor.organization_id, draft.publication_id, lock=True
    )
    draft = await row(session, WorkDraft, actor.organization_id, identity, lock=True)
    revision(draft.revision, expected)
    await course_scope(session, actor, publication.course_run_id, write=True)
    if publication.current_publication_id is None:
        raise WorkspaceFailure("not_published", "Задание не опубликовано.")
    published = await row(
        session,
        CourseRunHomeworkPublication,
        actor.organization_id,
        publication.current_publication_id,
    )
    uploaded = await row(
        session, WorkspaceArtifact, actor.organization_id, await prepared_artifact(session, draft)
    )
    if uploaded.owner_id != user or uploaded.private:
        raise WorkspaceFailure("forbidden", "Артефакт недоступен.", 403)
    submission = await session.scalar(
        select(Submission)
        .where(
            Submission.organization_id == actor.organization_id,
            Submission.course_run_homework_id == publication.id,
            Submission.student_id == user,
        )
        .with_for_update()
    )
    if submission is None:
        submission = Submission(
            id=runtime.id_factory(),
            organization_id=actor.organization_id,
            course_run_homework_id=publication.id,
            course_run_id=publication.course_run_id,
            homework_id=publication.homework_id,
            student_id=user,
            revision=0,
        )
        session.add(submission)
        await session.flush()
    await validate_submission_limit(session, actor.organization_id, submission, runtime.clock())
    artifact = await session.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.organization_id == actor.organization_id,
            ArtifactVersion.id == uploaded.id,
        )
    )
    if artifact is None:
        reference = ArtifactReference(
            id=runtime.id_factory(),
            organization_id=actor.organization_id,
            provider="workspace" if draft.artifact_url else "upload",
            credential_binding_id=None,
            credential_binding_version=None,
            original_url=draft.artifact_url or f"upload:{uploaded.id}",
            locator={"workspace_artifact_id": str(uploaded.id)},
            read_capability="available",
            feedback_capability="not_supported",
            last_checked_at=runtime.clock(),
            revision=0,
        )
        session.add(reference)
        await session.flush()
        artifact = ArtifactVersion(
            id=uploaded.id,
            organization_id=actor.organization_id,
            artifact_reference_id=reference.id,
            provider_version=uploaded.digest,
            content_digest=uploaded.digest,
            object_key=uploaded.object_key,
            media_type=uploaded.media_type,
            byte_size=uploaded.byte_size,
            captured_at=runtime.clock(),
            artifact_metadata={
                "source": "workspace" if draft.artifact_url else "upload",
                "filename": uploaded.filename,
                "provenance": uploaded.provenance,
            },
        )
        session.add(artifact)
        await session.flush()
    sequence = (
        await session.scalar(
            select(func.max(SubmissionVersion.sequence)).where(
                SubmissionVersion.organization_id == actor.organization_id,
                SubmissionVersion.submission_id == submission.id,
            )
        )
    ) or 0
    operation = Operation(
        id=runtime.id_factory(),
        organization_id=actor.organization_id,
        kind="artifact_capture",
        input_version=uploaded.digest,
        state="succeeded",
        revision=0,
        created_at=runtime.clock(),
        updated_at=runtime.clock(),
        finished_at=runtime.clock(),
    )
    session.add(operation)
    await session.flush()
    version = SubmissionVersion(
        id=runtime.id_factory(),
        organization_id=actor.organization_id,
        submission_id=submission.id,
        course_run_id=submission.course_run_id,
        homework_id=submission.homework_id,
        sequence=sequence + 1,
        homework_version_id=published.homework_version_id,
        artifact_reference_id=artifact.artifact_reference_id,
        artifact_version_id=artifact.id,
        submitted_at=runtime.clock(),
        effective_deadline=published.submission_deadline,
        phase="before_deadline" if runtime.clock() <= published.submission_deadline else "revision",
        status="ready",
        capture_operation_id=operation.id,
        revision=0,
    )
    session.add(version)
    await session.flush()
    if version.phase == "before_deadline":
        submission.current_predeadline_version_id = version.id
    submission.revision += 1
    await snapshot_submission_policy(session, actor.organization_id, version.id)
    return ResourceResult(id=submission.id, revision=submission.revision)


async def student_submission(
    runtime: FoundationRuntime, session: AsyncSession, actor: RequestActor, identity: UUID
) -> StudentSubmissionView:
    submission = await row(session, Submission, actor.organization_id, identity)
    if submission.student_id != actor.user_id:
        require_roles(actor, "reviewer", "methodologist")
    await course_scope(session, actor, submission.course_run_id)
    homework = await row(session, Homework, actor.organization_id, submission.homework_id)
    versions = (
        await session.scalars(
            select(SubmissionVersion)
            .where(
                SubmissionVersion.organization_id == actor.organization_id,
                SubmissionVersion.submission_id == identity,
            )
            .order_by(SubmissionVersion.sequence)
        )
    ).all()
    publications = (
        await session.execute(
            select(ReviewPublication, ReviewRevision, ReviewIteration, ReviewOutcome)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.id == ReviewPublication.review_iteration_id,
                    ReviewIteration.organization_id == ReviewPublication.organization_id,
                ),
            )
            .join(
                ReviewRevision,
                and_(
                    ReviewRevision.id == ReviewPublication.review_revision_id,
                    ReviewRevision.organization_id == ReviewPublication.organization_id,
                ),
            )
            .outerjoin(
                ReviewOutcome,
                and_(
                    ReviewOutcome.id == ReviewIteration.id,
                    ReviewOutcome.organization_id == ReviewIteration.organization_id,
                ),
            )
            .where(
                ReviewPublication.organization_id == actor.organization_id,
                ReviewIteration.student_id == submission.student_id,
                ReviewIteration.course_run_id == submission.course_run_id,
                ReviewIteration.homework_id == submission.homework_id,
            )
            .order_by(ReviewIteration.iteration_number)
        )
    ).all()
    grades = await published_grades(
        session, actor.organization_id, [pub.id for pub, _, _, _ in publications]
    )
    revision_ids = [rev.id for _, rev, _, _ in publications]
    decisions = (
        await session.execute(
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
    by_revision: dict[UUID, list[PublishedCriterionView]] = {}
    for decision, criterion in decisions:
        by_revision.setdefault(decision.review_revision_id, []).append(
            PublishedCriterionView(
                title=criterion.title,
                points=float(decision.points),
                max_points=float(criterion.max_points),
                reason=decision.reason,
            )
        )
    return StudentSubmissionView(
        id=identity,
        title=homework.title,
        publication_id=submission.course_run_homework_id,
        course_run_id=submission.course_run_id,
        attempts=[
            SubmissionAttemptView(
                id=v.id,
                sequence=v.sequence,
                comment=v.comment,
                submitted_at=v.submitted_at,
                status=v.status,
                artifact_id=v.artifact_version_id,
                capture_operation_id=v.capture_operation_id,
            )
            for v in versions
        ],
        reviews=[
            StudentReviewView(
                id=p.id,
                iteration_id=i.id,
                submission_version_id=i.submission_version_id,
                published_at=p.published_at,
                score=grades[p.id].final_score if p.id in grades else float(rev.total_score),
                grade=grades.get(p.id),
                feedback=rev.feedback,
                decision=outcome.decision if outcome else None,
                decision_reason=outcome.reason if outcome else None,
                revision_deadline=outcome.revision_deadline if outcome else None,
                criteria=by_revision.get(rev.id, []),
            )
            for p, rev, i, outcome in publications
        ],
        current_publication_id=publications[-1][0].id if publications else None,
    )
