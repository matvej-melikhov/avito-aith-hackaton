"""Immutable publication grading policy; criterion totals remain auditable."""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.workspace.common import WorkspaceFailure, row
from review_platform.contracts.workspace import GradePreview, PublicationPolicyInput
from review_platform.infrastructure.db.models import ReviewIteration, ReviewRevision, SubmissionVersion
from review_platform.infrastructure.db.models.workspace import SubmissionPolicySnapshot

async def grade_preview(session: AsyncSession,org: UUID,iteration: ReviewIteration,*,apply_penalty: bool=True) -> GradePreview:
    if iteration.current_revision_id is None:
        raw=Decimal('0')
    else:
        current=await row(session,ReviewRevision,org,iteration.current_revision_id);raw=current.total_score
    selected=await row(session,SubmissionVersion,org,iteration.submission_version_id)
    first=await session.scalar(select(SubmissionVersion).where(SubmissionVersion.organization_id==org,SubmissionVersion.submission_id==selected.submission_id).order_by(SubmissionVersion.sequence).limit(1))
    assert first is not None
    snapshot=await session.scalar(select(SubmissionPolicySnapshot).where(SubmissionPolicySnapshot.organization_id==org,SubmissionPolicySnapshot.id==first.id))
    policy=PublicationPolicyInput.model_validate(snapshot.policy) if snapshot else None
    late_seconds=max(0,(first.submitted_at-first.effective_deadline).total_seconds())
    days=int((Decimal(str(late_seconds))/Decimal(86400)).to_integral_value(rounding=ROUND_CEILING))
    rate=Decimal(str(policy.penalty_per_day)) if policy else Decimal(0)
    deduction=min(raw,(days*rate).quantize(Decimal('0.01'))) if apply_penalty else Decimal(0)
    return GradePreview(raw_score=float(raw),penalty_days=days,penalty_rate=float(rate),penalty=float(deduction),final_score=float(raw-deduction),pass_score=policy.pass_score if policy else None,policy_revision=snapshot.policy_revision if snapshot else None)

async def validate_submission_limit(session: AsyncSession,org: UUID,submission_id: UUID,now: datetime,policy: PublicationPolicyInput) -> None:
    versions=(await session.scalars(select(SubmissionVersion).where(SubmissionVersion.organization_id==org,SubmissionVersion.submission_id==submission_id).order_by(SubmissionVersion.sequence))).all()
    if not versions or now <= versions[0].effective_deadline:return
    revisions=sum(v.phase=='revision' for v in versions[1:])
    if revisions>=policy.max_resubmissions:raise WorkspaceFailure('resubmission_limit','Лимит пересдач исчерпан.',409)
