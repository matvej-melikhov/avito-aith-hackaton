"""Canonical tenant-scoped ReviewDetail read projection."""

from __future__ import annotations

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
from review_platform.infrastructure.db.models.delivery import (
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
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
    delivery_summaries = await project_delivery_summaries(
        session,
        organization_id=organization_id,
        deliveries=deliveries,
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
            _publication_request(publication_request) if publication_request is not None else None
        ),
        "deliveries": delivery_summaries,
    }


def project_ai_review(history: AIReviewHistory | None) -> dict[str, Any] | None:
    if history is None:
        return None
    run = history.run
    event_order = {
        event.event_id: (event.attempt_number, event.sequence) for event in history.events
    }
    suggestion_rows = (
        tuple(
            sorted(
                enumerate(history.suggestions),
                key=lambda indexed: (
                    *event_order.get(indexed[1].event_id, (2**31, 2**31)),
                    indexed[1].criterion_id.int,
                    indexed[1].id.int,
                    indexed[0],
                ),
            )
        )
        if event_order
        else tuple(enumerate(history.suggestions))
    )
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
        for _, item in suggestion_rows
    ]
    signal_rows = (
        tuple(
            sorted(
                enumerate(history.signals),
                key=lambda indexed: (
                    *event_order.get(indexed[1].event_id, (2**31, 2**31)),
                    indexed[1].id.int,
                    indexed[0],
                ),
            )
        )
        if event_order
        else tuple(enumerate(history.signals))
    )
    signal_row = signal_rows[-1][1] if signal_rows else None
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
                require_utc(item.finished_at).isoformat() if item.finished_at is not None else None
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
                    ReviewRevision.review_iteration_id == ReviewPublication.review_iteration_id,
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


async def project_delivery_summaries(
    session: AsyncSession,
    *,
    organization_id: UUID,
    deliveries: Sequence[ExternalDelivery],
) -> list[dict[str, Any]]:
    """Project frozen DeliverySummary objects with T140 history in bulk."""

    if not deliveries:
        return []
    delivery_ids = {row.id for row in deliveries}
    attempts = (
        await session.scalars(
            select(DeliveryAttempt)
            .where(
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.delivery_id.in_(delivery_ids),
            )
            .order_by(
                DeliveryAttempt.delivery_id,
                DeliveryAttempt.attempt_number,
                DeliveryAttempt.id,
            )
        )
    ).all()
    observations = (
        await session.scalars(
            select(DeliveryReconciliationObservation)
            .where(
                DeliveryReconciliationObservation.organization_id == organization_id,
                DeliveryReconciliationObservation.delivery_id.in_(delivery_ids),
            )
            .order_by(
                DeliveryReconciliationObservation.delivery_id,
                DeliveryReconciliationObservation.attempt_number,
                DeliveryReconciliationObservation.observed_at,
                DeliveryReconciliationObservation.id,
            )
        )
    ).all()
    attempts_by_delivery: dict[UUID, list[DeliveryAttempt]] = {
        identity: [] for identity in delivery_ids
    }
    observations_by_delivery: dict[UUID, list[DeliveryReconciliationObservation]] = {
        identity: [] for identity in delivery_ids
    }
    for attempt in attempts:
        attempts_by_delivery[attempt.delivery_id].append(attempt)
    for observation in observations:
        observations_by_delivery[observation.delivery_id].append(observation)
    return [
        project_delivery_summary(
            row,
            attempts=attempts_by_delivery[row.id],
            observations=observations_by_delivery[row.id],
        )
        for row in deliveries
    ]


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


def project_delivery_summary(
    row: ExternalDelivery,
    *,
    attempts: Sequence[DeliveryAttempt],
    observations: Sequence[DeliveryReconciliationObservation],
) -> dict[str, Any]:
    """Serialize one delivery while preserving its exact immutable snapshots."""

    try:
        artifact_content_digest = validate_digest(row.artifact_content_digest)
    except ValueError as error:
        raise ReviewDetailProjectionError("delivery artifact digest is invalid") from error
    attempt_by_id = {attempt.id: attempt for attempt in attempts}
    if len(attempt_by_id) != len(attempts):
        raise ReviewDetailProjectionError("delivery attempt identity is duplicated")
    for attempt in attempts:
        _validate_delivery_attempt(row, attempt)
    latest_observation: dict[UUID, DeliveryReconciliationObservation] = {}
    for observation in observations:
        matched_attempt = attempt_by_id.get(observation.delivery_attempt_id)
        if matched_attempt is None:
            raise ReviewDetailProjectionError(
                "delivery reconciliation observation has no projected attempt"
            )
        _validate_reconciliation_observation(row, matched_attempt, observation)
        previous = latest_observation.get(matched_attempt.id)
        if previous is None or (
            require_utc(observation.observed_at), observation.id
        ) > (require_utc(previous.observed_at), previous.id):
            latest_observation[matched_attempt.id] = observation

    return {
        "id": str(row.id),
        "operation_id": str(row.operation_id),
        "destination_binding_id": str(row.destination_binding_id),
        "destination_kind": row.destination_kind,
        "state": row.state,
        "attempts": [
            _project_delivery_attempt(attempt, latest_observation.get(attempt.id))
            for attempt in sorted(attempts, key=lambda item: (item.attempt_number, item.id))
        ],
        "provenance": {
            "course_run_id": str(row.course_run_id),
            "homework_version_id": str(row.homework_version_id),
            "criterion_set_id": str(row.criterion_set_id),
            "submission_version_id": str(row.submission_version_id),
            "artifact_version_id": str(row.artifact_version_id),
            "artifact_content_digest": artifact_content_digest,
            "review_iteration_id": str(row.review_iteration_id),
            "review_revision_id": str(row.review_revision_id),
            "contract_version": row.contract_version,
        },
        "error": _error(row.sanitized_error),
    }


def _project_delivery_attempt(
    attempt: DeliveryAttempt,
    observation: DeliveryReconciliationObservation | None,
) -> dict[str, Any]:
    state = attempt.outcome
    finished_at = attempt.finished_at
    error: Mapping[str, Any] | None = attempt.sanitized_error
    if observation is not None:
        state = "retryable_failed" if observation.outcome == "not_found" else observation.outcome
        finished_at = observation.observed_at
        error = _reconciliation_error(observation)
    return {
        "attempt_number": attempt.attempt_number,
        "state": state,
        "started_at": require_utc(attempt.started_at).isoformat(),
        "finished_at": (
            require_utc(finished_at).isoformat() if finished_at is not None else None
        ),
        "error": _error(error),
    }


def _reconciliation_error(
    observation: DeliveryReconciliationObservation,
) -> Mapping[str, Any] | None:
    if observation.sanitized_error is not None:
        return observation.sanitized_error
    defaults: dict[str, dict[str, str]] = {
        "not_found": {
            "code": "delivery_not_found",
            "message": "Provider reports no result for the delivery attempt",
            "action": "retry",
        },
        "retryable_failed": {
            "code": "reconciliation_retryable_failed",
            "message": "Delivery reconciliation can be retried",
            "action": "retry",
        },
        "unknown_outcome": {
            "code": "reconciliation_unknown_outcome",
            "message": "Delivery outcome remains unknown after reconciliation",
            "action": "reconcile",
        },
        "action_required": {
            "code": "reconciliation_action_required",
            "message": "Delivery reconciliation requires operator action",
            "action": "operator_review",
        },
    }
    return defaults.get(observation.outcome)


def _validate_delivery_attempt(row: ExternalDelivery, attempt: DeliveryAttempt) -> None:
    if (
        attempt.organization_id != row.organization_id
        or attempt.delivery_id != row.id
        or attempt.external_delivery_id != row.id
        or attempt.operation_id != row.operation_id
        or attempt.credential_binding_id != row.credential_binding_id
        or attempt.credential_binding_version != row.credential_binding_version
    ):
        raise ReviewDetailProjectionError("delivery attempt provenance mismatched")


def _validate_reconciliation_observation(
    row: ExternalDelivery,
    attempt: DeliveryAttempt,
    observation: DeliveryReconciliationObservation,
) -> None:
    if (
        observation.organization_id != row.organization_id
        or observation.delivery_id != row.id
        or observation.external_delivery_id != row.id
        or observation.delivery_attempt_id != attempt.id
        or observation.attempt_number != attempt.attempt_number
        or observation.operation_id != row.operation_id
        or observation.credential_binding_id != row.credential_binding_id
        or observation.credential_binding_version != row.credential_binding_version
    ):
        raise ReviewDetailProjectionError(
            "delivery reconciliation observation provenance mismatched"
        )


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
    "project_delivery_summaries",
    "project_delivery_summary",
    "read_review_detail",
]
