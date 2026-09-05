"""Canonical tenant-scoped ReviewDetail read projection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.contracts.registry import CONTRACT_VERSION
from review_platform.domain.primitives import require_utc, validate_digest
from review_platform.infrastructure.db.models.ai_review import AIReviewRun
from review_platform.infrastructure.db.models.operations import OperationAttempt
from review_platform.infrastructure.db.models.publication import (
    ExternalDelivery,
    PublicationRequest,
    ReviewPublication,
)
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.review_work import ReviewResponsibility
from review_platform.infrastructure.db.models.submission import ArtifactVersion
from review_platform.infrastructure.db.repositories.ai_reviews import (
    AIReviewHistory,
    AIReviewRepository,
)


class ReviewDetailProjectionError(RuntimeError):
    """Persisted review provenance cannot form the frozen response."""


@dataclass(frozen=True, slots=True)
class ArtifactDownloadGrant:
    organization_id: UUID
    artifact_version_id: UUID
    url: str
    expires_at: datetime


type ArtifactDownloadSigner = Callable[[UUID, UUID], ArtifactDownloadGrant]


async def read_review_detail(
    session: AsyncSession,
    *,
    organization_id: UUID,
    review_iteration_id: UUID,
    sign_artifact_download: ArtifactDownloadSigner,
) -> dict[str, Any] | None:
    """Read one ReviewDetail without locks or any cross-tenant fallback."""

    target_row = (
        await session.execute(
            select(ReviewIteration, ReviewCase)
            .join(
                ReviewCase,
                and_(
                    ReviewCase.organization_id == ReviewIteration.organization_id,
                    ReviewCase.id == ReviewIteration.review_case_id,
                ),
            )
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.id == review_iteration_id,
            )
        )
    ).one_or_none()
    if target_row is None:
        return None
    target, review_case = target_row
    artifact = await session.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.organization_id == organization_id,
            ArtifactVersion.id == target.artifact_version_id,
        )
    )
    if artifact is None:
        raise ReviewDetailProjectionError("ReviewIteration ArtifactVersion is missing")
    try:
        artifact_digest = validate_digest(artifact.content_digest)
    except ValueError as error:
        raise ReviewDetailProjectionError("ArtifactVersion digest is invalid") from error
    grant = sign_artifact_download(organization_id, artifact.id)
    if grant.organization_id != organization_id or grant.artifact_version_id != artifact.id:
        raise ReviewDetailProjectionError("artifact download grant scope mismatched")
    expires_at = require_utc(grant.expires_at)
    if not grant.url:
        raise ReviewDetailProjectionError("artifact download grant URL is empty")

    published = await _highest_published_revision(
        session,
        organization_id=organization_id,
        review_case_id=review_case.id,
    )
    if published is None:
        publication = None
        current_revision = None
        decisions: Sequence[ReviewCriterionDecision] = ()
        notes: Sequence[ReviewNote] = ()
    else:
        publication, _published_iteration, current_revision = published
        decisions = (
            await session.scalars(
                select(ReviewCriterionDecision)
                .where(
                    ReviewCriterionDecision.organization_id == organization_id,
                    ReviewCriterionDecision.review_revision_id == current_revision.id,
                )
                .order_by(ReviewCriterionDecision.criterion_id, ReviewCriterionDecision.id)
            )
        ).all()
        notes = (
            await session.scalars(
                select(ReviewNote)
                .where(
                    ReviewNote.organization_id == organization_id,
                    ReviewNote.review_revision_id == current_revision.id,
                )
                .order_by(ReviewNote.position, ReviewNote.id)
            )
        ).all()

    ai_run = await session.scalar(
        select(AIReviewRun)
        .where(
            AIReviewRun.organization_id == organization_id,
            AIReviewRun.review_iteration_id == target.id,
        )
        .order_by(AIReviewRun.created_at.desc(), AIReviewRun.id.desc())
        .limit(1)
    )
    ai_history = (
        await AIReviewRepository(session).history(organization_id, ai_run.id)
        if ai_run is not None
        else None
    )
    responsibility = (
        await session.scalars(
            select(ReviewResponsibility)
            .where(
                ReviewResponsibility.organization_id == organization_id,
                ReviewResponsibility.review_case_id == review_case.id,
                or_(
                    ReviewResponsibility.review_iteration_id.is_(None),
                    ReviewResponsibility.review_iteration_id == target.id,
                ),
            )
            .order_by(ReviewResponsibility.occurred_at, ReviewResponsibility.id)
        )
    ).all()
    publication_request = await session.scalar(
        select(PublicationRequest)
        .where(
            PublicationRequest.organization_id == organization_id,
            PublicationRequest.review_iteration_id == target.id,
        )
        .order_by(PublicationRequest.created_at.desc(), PublicationRequest.id.desc())
        .limit(1)
    )
    deliveries = (
        (
            await session.scalars(
                select(ExternalDelivery)
                .where(
                    ExternalDelivery.organization_id == organization_id,
                    ExternalDelivery.publication_id == publication.id,
                )
                .order_by(
                    ExternalDelivery.destination_binding_id,
                    ExternalDelivery.binding_version,
                    ExternalDelivery.id,
                )
            )
        ).all()
        if publication is not None
        else []
    )
    attempts_by_operation = await _delivery_attempts(
        session,
        organization_id=organization_id,
        operation_ids={item.operation_id for item in deliveries},
    )

    return {
        "review_iteration_id": str(target.id),
        "immutable_inputs": {
            "course_run_id": str(target.course_run_id),
            "submission_version_id": str(target.submission_version_id),
            "effective_deadline": require_utc(target.effective_deadline).isoformat(),
            "artifact_version_id": str(artifact.id),
            "artifact_content_digest": artifact_digest,
            "artifact_download_url": grant.url,
            "artifact_download_expires_at": expires_at.isoformat(),
            "homework_version_id": str(target.homework_version_id),
            "criterion_set_id": str(target.criterion_set_id),
            "contract_version": CONTRACT_VERSION,
        },
        "current_review_revision_id": (
            str(current_revision.id) if current_revision is not None else None
        ),
        "current_review_revision": (
            _revision_summary(current_revision) if current_revision is not None else None
        ),
        "revision": target.revision,
        "status": target.status,
        "criterion_decisions": [_decision(item) for item in decisions],
        "review_notes": [_note(item) for item in notes],
        "ai_review": project_ai_review(ai_history),
        "responsibility_events": [_responsibility(item) for item in responsibility],
        "publication_request": (
            _publication_request(publication_request)
            if publication_request is not None
            else None
        ),
        "deliveries": [
            _delivery(item, attempts_by_operation.get(item.operation_id, ()))
            for item in deliveries
        ],
    }


def project_ai_review(history: AIReviewHistory | None) -> dict[str, Any] | None:
    if history is None:
        return None
    run = history.run
    suggestions = [
        {
            "id": str(item.id),
            "criterion_id": str(item.criterion_id),
            "status": item.status,
            "proposed_points": (
                float(item.proposed_points) if item.proposed_points is not None else None
            ),
            "reason": item.reason,
            "evidence": list(item.evidence),
            "confidence": item.confidence,
            "reviewer_note": item.reviewer_note,
            "student_feedback": item.student_feedback,
            "flags": list(item.flags),
        }
        for item in history.suggestions
    ]
    signal_row = history.signals[-1] if history.signals else None
    signal = (
        {
            "level": signal_row.level,
            "evidence": list(signal_row.evidence),
            "limitations": list(signal_row.limitations),
            "questions": list(signal_row.questions),
        }
        if signal_row is not None
        else None
    )
    attempts = [
        {
            "attempt_number": item.attempt_number,
            "state": _ai_attempt_state(item.status),
            "started_at": require_utc(item.started_at).isoformat(),
            "finished_at": (
                require_utc(item.finished_at).isoformat()
                if item.finished_at is not None
                else None
            ),
            "error": _error(item.sanitized_error),
        }
        for item in history.attempts
    ]
    current_attempt = next(
        (
            item
            for item in reversed(history.attempts)
            if item.attempt_number == run.current_attempt_no
        ),
        None,
    )
    return {
        "run_id": str(run.id),
        "input_fingerprint": run.input_fingerprint,
        "contract_version": run.contract_version,
        "state": run.status,
        "attempts": attempts,
        "suggestions": suggestions,
        "signal": signal,
        "error": _error(current_attempt.sanitized_error) if current_attempt is not None else None,
    }


async def _highest_published_revision(
    session: AsyncSession,
    *,
    organization_id: UUID,
    review_case_id: UUID,
) -> tuple[ReviewPublication, ReviewIteration, ReviewRevision] | None:
    row = (
        await session.execute(
            select(ReviewPublication, ReviewIteration, ReviewRevision)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.organization_id == ReviewPublication.organization_id,
                    ReviewIteration.id == ReviewPublication.review_iteration_id,
                ),
            )
            .join(
                ReviewRevision,
                and_(
                    ReviewRevision.organization_id == ReviewPublication.organization_id,
                    ReviewRevision.review_iteration_id
                    == ReviewPublication.review_iteration_id,
                    ReviewRevision.id == ReviewPublication.review_revision_id,
                ),
            )
            .where(
                ReviewPublication.organization_id == organization_id,
                ReviewPublication.status == "published",
                ReviewIteration.review_case_id == review_case_id,
            )
            .order_by(
                ReviewIteration.iteration_number.desc(),
                ReviewPublication.publication_version.desc(),
                ReviewPublication.published_at.desc(),
                ReviewPublication.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    return cast(tuple[ReviewPublication, ReviewIteration, ReviewRevision] | None, row)


async def _delivery_attempts(
    session: AsyncSession,
    *,
    organization_id: UUID,
    operation_ids: set[UUID],
) -> dict[UUID, tuple[OperationAttempt, ...]]:
    if not operation_ids:
        return {}
    rows = (
        await session.scalars(
            select(OperationAttempt)
            .where(
                OperationAttempt.organization_id == organization_id,
                OperationAttempt.operation_id.in_(operation_ids),
            )
            .order_by(OperationAttempt.operation_id, OperationAttempt.attempt_number)
        )
    ).all()
    grouped: defaultdict[UUID, list[OperationAttempt]] = defaultdict(list)
    for row in rows:
        grouped[row.operation_id].append(row)
    return {identity: tuple(values) for identity, values in grouped.items()}


def _revision_summary(row: ReviewRevision) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "review_iteration_id": str(row.review_iteration_id),
        "revision_number": row.revision_number,
        "author_user_id": str(row.author_user_id),
        "feedback": row.feedback,
        "total_score": float(row.total_score),
        "created_at": require_utc(row.created_at).isoformat(),
    }


def _decision(row: ReviewCriterionDecision) -> dict[str, Any]:
    return {
        "criterion_id": str(row.criterion_id),
        "points": float(row.points),
        "decision": row.decision,
        "reason": row.reason,
        "evidence_ids": list(row.evidence_ids),
    }


def _note(row: ReviewNote) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "criterion_id": str(row.criterion_id) if row.criterion_id is not None else None,
        "text": row.text,
        "author_user_id": str(row.author_user_id),
        "position": row.position,
    }


def _responsibility(row: ReviewResponsibility) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "reviewer_id": str(row.reviewer_id),
        "actor_id": str(row.actor_id),
        "action": row.action,
        "occurred_at": require_utc(row.occurred_at).isoformat(),
    }


def _publication_request(row: PublicationRequest) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "review_revision_id": str(row.review_revision_id),
        "status": row.status,
        "expires_at": require_utc(row.expires_at).isoformat(),
        "revision": row.revision,
    }


def _delivery(
    row: ExternalDelivery,
    attempts: Sequence[OperationAttempt],
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "operation_id": str(row.operation_id),
        "destination_binding_id": str(row.destination_binding_id),
        "destination_kind": row.destination_kind,
        "state": row.state,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "state": attempt.outcome,
                "started_at": require_utc(attempt.started_at).isoformat(),
                "finished_at": (
                    require_utc(attempt.finished_at).isoformat()
                    if attempt.finished_at is not None
                    else None
                ),
                "error": _error(attempt.sanitized_error),
            }
            for attempt in attempts
        ],
        "provenance": {
            "course_run_id": str(row.course_run_id),
            "homework_version_id": str(row.homework_version_id),
            "criterion_set_id": str(row.criterion_set_id),
            "submission_version_id": str(row.submission_version_id),
            "artifact_version_id": str(row.artifact_version_id),
            "artifact_content_digest": row.artifact_content_digest,
            "review_iteration_id": str(row.review_iteration_id),
            "review_revision_id": str(row.review_revision_id),
            "contract_version": row.contract_version,
        },
        "error": _error(row.sanitized_error),
    }


def _error(value: Mapping[str, Any] | None) -> dict[str, str | None] | None:
    if value is None:
        return None
    raw_code = value.get("code", value.get("safe_code"))
    raw_message = value.get("message")
    raw_action = value.get("action", value.get("safe_action"))
    code = raw_code if isinstance(raw_code, str) and raw_code else "operation_failed"
    message = raw_message if isinstance(raw_message, str) else "Operation failed"
    action = raw_action if isinstance(raw_action, str) else None
    return {
        "code": code[:128],
        "message": message[:2048],
        "action": action[:512] if action is not None else None,
    }


def _ai_attempt_state(status: str) -> str:
    if status in {"pending", "running", "partial"}:
        return "processing"
    if status == "stale":
        return "action_required"
    return status


__all__ = [
    "ArtifactDownloadGrant",
    "ArtifactDownloadSigner",
    "ReviewDetailProjectionError",
    "project_ai_review",
    "read_review_detail",
]
