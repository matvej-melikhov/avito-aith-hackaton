"""Snapshot submission rules and project the immutable published grade."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.workspace.common import WorkspaceFailure, row
from review_platform.contracts.workspace import GradePreview, PublicationPolicyInput
from review_platform.infrastructure.db.models import (
    ArtifactReference,
    ReviewIteration,
    ReviewRevision,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.models.workspace import (
    PublicationPolicy,
    SubmissionPolicySnapshot,
    WorkDraft,
    WorkspacePublishedGrade,
)


async def validate_submission_limit(
    session: AsyncSession, org: UUID, submission: Submission, now: datetime
) -> None:
    policy_row = await session.scalar(
        select(PublicationPolicy).where(
            PublicationPolicy.organization_id == org,
            PublicationPolicy.id == submission.course_run_homework_id,
        )
    )
    versions = (
        await session.scalars(
            select(SubmissionVersion)
            .where(
                SubmissionVersion.organization_id == org,
                SubmissionVersion.submission_id == submission.id,
            )
            .order_by(SubmissionVersion.sequence)
        )
    ).all()
    if not versions or now <= versions[0].effective_deadline:
        return
    snapshot = await session.scalar(
        select(SubmissionPolicySnapshot).where(
            SubmissionPolicySnapshot.organization_id == org,
            SubmissionPolicySnapshot.id == versions[0].id,
        )
    )
    stored_policy = snapshot.policy if snapshot else policy_row.policy if policy_row else None
    if stored_policy is None:
        return  # Legacy submissions predate the versioned workspace policy.
    policy = PublicationPolicyInput.model_validate(stored_policy)
    revisions = sum(v.phase == "revision" for v in versions[1:])
    if revisions >= policy.max_resubmissions:
        raise WorkspaceFailure("resubmission_limit", "Лимит пересдач исчерпан.", 409)


async def snapshot_submission_policy(session: AsyncSession, org: UUID, version_id: UUID) -> None:
    version = await row(session, SubmissionVersion, org, version_id)
    submission = await row(session, Submission, org, version.submission_id)
    policy = await session.scalar(
        select(PublicationPolicy).where(
            PublicationPolicy.organization_id == org,
            PublicationPolicy.id == submission.course_run_homework_id,
        )
    )
    snapshot = await session.scalar(
        select(SubmissionPolicySnapshot.id).where(
            SubmissionPolicySnapshot.organization_id == org,
            SubmissionPolicySnapshot.id == version.id,
        )
    )
    if policy and snapshot is None:
        session.add(
            SubmissionPolicySnapshot(
                id=version.id,
                organization_id=org,
                policy=dict(policy.policy),
                policy_revision=policy.revision,
            )
        )
    draft = await session.scalar(
        select(WorkDraft).where(
            WorkDraft.organization_id == org,
            WorkDraft.publication_id == submission.course_run_homework_id,
            WorkDraft.student_id == submission.student_id,
        )
    )
    reference = await row(session, ArtifactReference, org, version.artifact_reference_id)
    if draft and (
        draft.artifact_url == reference.original_url
        or draft.upload_id == version.artifact_version_id
    ):
        version.comment = draft.comment
    await session.flush()


async def grade_preview(
    session: AsyncSession, org: UUID, iteration: ReviewIteration, *, apply_penalty: bool = True
) -> GradePreview:
    raw = Decimal("0")
    if iteration.current_revision_id:
        current = await row(session, ReviewRevision, org, iteration.current_revision_id)
        raw = current.total_score
    selected = await row(session, SubmissionVersion, org, iteration.submission_version_id)
    first = await session.scalar(
        select(SubmissionVersion)
        .where(
            SubmissionVersion.organization_id == org,
            SubmissionVersion.submission_id == selected.submission_id,
        )
        .order_by(SubmissionVersion.sequence)
        .limit(1)
    )
    assert first is not None
    snapshot = await session.scalar(
        select(SubmissionPolicySnapshot).where(
            SubmissionPolicySnapshot.organization_id == org, SubmissionPolicySnapshot.id == first.id
        )
    )
    policy = PublicationPolicyInput.model_validate(snapshot.policy) if snapshot else None
    late_seconds = max(0, (first.submitted_at - first.effective_deadline).total_seconds())
    days = int(
        (Decimal(str(late_seconds)) / Decimal(86400)).to_integral_value(rounding=ROUND_CEILING)
    )
    rate = Decimal(str(policy.penalty_per_day)) if policy else Decimal(0)
    deduction = min(raw, (days * rate).quantize(Decimal("0.01"))) if apply_penalty else Decimal(0)
    return GradePreview(
        raw_score=float(raw),
        penalty_days=days,
        penalty_rate=float(rate),
        penalty=float(deduction),
        final_score=float(raw - deduction),
        pass_score=policy.pass_score if policy else None,
        policy_revision=snapshot.policy_revision if snapshot else None,
    )


async def published_grades(
    session: AsyncSession, org: UUID, publication_ids: list[UUID]
) -> dict[UUID, GradePreview]:
    rows = (
        await session.scalars(
            select(WorkspacePublishedGrade).where(
                WorkspacePublishedGrade.organization_id == org,
                WorkspacePublishedGrade.id.in_(publication_ids),
            )
        )
    ).all()
    return {grade.id: GradePreview.model_validate(grade.details) for grade in rows}
