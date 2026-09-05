"""Command-oriented human review revision service for HTTP/MCP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from review_platform.application.request_context import RequestActor
from review_platform.application.services.review_drafts import (
    ReviewDecisionInput,
    ReviewDraftService,
    ReviewNoteInput,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewRevisionRecord,
)


@dataclass(frozen=True, slots=True)
class SaveReviewRevisionCommand:
    organization_id: UUID
    review_iteration_id: UUID
    expected_iteration_revision: int
    expected_current_revision_id: UUID | None
    feedback: str
    decisions: tuple[ReviewDecisionInput, ...]
    notes: tuple[ReviewNoteInput, ...]
    request_id: UUID
    trace_id: UUID

    def __post_init__(self) -> None:
        if self.expected_iteration_revision < 0:
            raise ValueError("expected ReviewIteration revision must be non-negative")


@dataclass(frozen=True, slots=True)
class SaveReviewRevisionResult:
    review_revision_id: UUID
    review_iteration_id: UUID
    review_iteration_revision: int
    revision_number: int
    total_score: Decimal


class ReviewRevisionService:
    """Reuse T104's sole immutable append/CAS implementation without duplication."""

    def __init__(self, draft_service: ReviewDraftService) -> None:
        self._draft_service = draft_service

    async def save(
        self,
        command: SaveReviewRevisionCommand,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> SaveReviewRevisionResult:
        record: ReviewRevisionRecord = await self._draft_service.save(
            transaction=transaction,
            organization_id=command.organization_id,
            review_iteration_id=command.review_iteration_id,
            expected_iteration_revision=command.expected_iteration_revision,
            expected_current_revision_id=command.expected_current_revision_id,
            feedback=command.feedback,
            decisions=command.decisions,
            notes=command.notes,
            actor=actor,
            request_id=command.request_id,
            trace_id=command.trace_id,
        )
        return SaveReviewRevisionResult(
            review_revision_id=record.review_revision_id,
            review_iteration_id=record.review_iteration_id,
            review_iteration_revision=command.expected_iteration_revision + 1,
            revision_number=record.revision_number,
            total_score=record.total_score,
        )


__all__ = [
    "ReviewDecisionInput",
    "ReviewNoteInput",
    "ReviewRevisionService",
    "SaveReviewRevisionCommand",
    "SaveReviewRevisionResult",
]
