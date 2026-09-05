"""Append-only human ReviewRevision creation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionDraft,
    ReviewDecisionKind,
    ReviewIterationRevisionContext,
    ReviewNoteDraft,
    ReviewRevisionDraft,
    ReviewRevisionRecord,
)

_HUMAN_REVIEWER = AuthorizationPolicy(required_roles=frozenset({"reviewer", "methodologist"}))


class ReviewDraftError(RuntimeError):
    pass


class ReviewDraftNotFound(ReviewDraftError):
    pass


class ReviewDraftConflict(ReviewDraftError):
    pass


class ReviewDraftIncomplete(ReviewDraftError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewDecisionInput:
    criterion_id: UUID
    points: Decimal
    decision: ReviewDecisionKind
    reason: str
    evidence_ids: tuple[str, ...] = ()
    ai_suggestion_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReviewNoteInput:
    text: str
    criterion_id: UUID | None = None


class ReviewDraftRepository(Protocol):
    async def lock_iteration(
        self, organization_id: UUID, review_iteration_id: UUID
    ) -> ReviewIterationRevisionContext | None: ...

    async def next_revision_number(
        self, organization_id: UUID, review_iteration_id: UUID
    ) -> int: ...

    async def append(self, draft: ReviewRevisionDraft) -> ReviewRevisionRecord: ...

    async def compare_and_set_current(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID | None,
        new_revision_id: UUID,
    ) -> bool: ...


class ReviewDraftService:
    def __init__(
        self,
        *,
        repository: ReviewDraftRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def save(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        review_iteration_id: UUID,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID | None,
        feedback: str,
        decisions: Sequence[ReviewDecisionInput],
        notes: Sequence[ReviewNoteInput],
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> ReviewRevisionRecord:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_HUMAN_REVIEWER,
        )
        if actor.user_id is None:
            raise ReviewDraftError("human review author identity is required")
        context = await self._repository.lock_iteration(organization_id, review_iteration_id)
        if context is None:
            raise ReviewDraftNotFound("tenant ReviewIteration was not found")
        if (
            context.organization_id != organization_id
            or context.review_iteration_id != review_iteration_id
        ):
            raise ReviewDraftNotFound("repository returned another tenant iteration")
        if (
            context.iteration_revision != expected_iteration_revision
            or context.current_revision_id != expected_current_revision_id
        ):
            raise ReviewDraftConflict("expected current human revision is stale")
        expected_criteria = {criterion.criterion_id for criterion in context.criteria}
        actual_criteria = [decision.criterion_id for decision in decisions]
        if len(actual_criteria) != len(set(actual_criteria)):
            raise ReviewDraftIncomplete("human snapshot contains duplicate criterion decisions")
        if set(actual_criteria) != expected_criteria:
            raise ReviewDraftIncomplete(
                "human snapshot must contain exactly one decision for every active criterion"
            )

        revision_id = self._id_factory()
        draft = ReviewRevisionDraft(
            organization_id=organization_id,
            review_revision_id=revision_id,
            review_iteration_id=review_iteration_id,
            revision_number=await self._repository.next_revision_number(
                organization_id, review_iteration_id
            ),
            author_user_id=actor.user_id,
            base_revision_id=expected_current_revision_id,
            feedback=feedback,
            decisions=tuple(
                ReviewDecisionDraft(
                    decision_id=self._id_factory(),
                    criterion_id=item.criterion_id,
                    points=item.points,
                    decision=item.decision,
                    reason=item.reason,
                    evidence_ids=item.evidence_ids,
                    ai_suggestion_id=item.ai_suggestion_id,
                )
                for item in decisions
            ),
            notes=tuple(
                ReviewNoteDraft(
                    note_id=self._id_factory(),
                    criterion_id=item.criterion_id,
                    text=item.text,
                    author_user_id=actor.user_id,
                    position=position,
                )
                for position, item in enumerate(notes)
            ),
            created_at=require_utc(self._clock()),
        )
        record = await self._repository.append(draft)
        if not await self._repository.compare_and_set_current(
            organization_id,
            review_iteration_id,
            expected_iteration_revision=expected_iteration_revision,
            expected_current_revision_id=expected_current_revision_id,
            new_revision_id=revision_id,
        ):
            raise ReviewDraftConflict("ReviewIteration current revision CAS lost a race")
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="save_review_revision",
                entity_type="review_revision",
                entity_id=revision_id,
                before_revision=expected_iteration_revision,
                after_revision=expected_iteration_revision + 1,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"revision_number": record.revision_number},
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return record


__all__ = [
    "ReviewDecisionInput",
    "ReviewDraftConflict",
    "ReviewDraftError",
    "ReviewDraftIncomplete",
    "ReviewDraftNotFound",
    "ReviewDraftRepository",
    "ReviewDraftService",
    "ReviewNoteInput",
]
