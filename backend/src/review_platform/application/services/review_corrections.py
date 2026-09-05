"""Published-review correction through one immutable successor iteration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.application.services.review_requirements import (
    RequirementsMigrationContext,
    ReviewRequirementsRepository,
    SuccessorRevisionRepository,
)
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.db.models.publication import ReviewIterationRelation
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionDraft,
    ReviewNoteDraft,
    ReviewRevisionDraft,
    ReviewRevisionRecord,
)

_CORRECT = AuthorizationPolicy(required_roles=frozenset({"reviewer", "methodologist"}))


class ReviewCorrectionError(RuntimeError):
    """Base typed correction failure."""


class ReviewCorrectionNotFound(ReviewCorrectionError):
    pass


class ReviewCorrectionConflict(ReviewCorrectionError):
    pass


@dataclass(frozen=True, slots=True)
class CreateReviewCorrectionCommand:
    organization_id: UUID
    predecessor_iteration_id: UUID
    expected_predecessor_revision: int
    published_review_revision_id: UUID
    reason: str
    request_id: UUID
    trace_id: UUID

    def __post_init__(self) -> None:
        if self.expected_predecessor_revision < 0:
            raise ValueError("expected predecessor revision must be nonnegative")
        if not self.reason or len(self.reason) > 10_000:
            raise ValueError("correction reason must contain 1..10000 characters")


@dataclass(frozen=True, slots=True)
class PublishedReviewSnapshot:
    organization_id: UUID
    review_iteration_id: UUID
    publication_id: UUID
    revision: ReviewRevisionRecord


@dataclass(frozen=True, slots=True)
class ExistingReviewCorrection:
    organization_id: UUID
    review_case_id: UUID
    predecessor_iteration_id: UUID
    published_review_revision_id: UUID
    reason: str
    successor_iteration_id: UUID
    successor_iteration_revision: int
    successor_revision_id: UUID


@dataclass(frozen=True, slots=True)
class ReviewCorrectionResult:
    organization_id: UUID
    review_case_id: UUID
    predecessor_iteration_id: UUID
    published_review_revision_id: UUID
    successor_iteration_id: UUID
    successor_iteration_revision: int
    successor_revision_id: UUID
    replayed: bool


class ReviewCorrectionPublicationRepository(Protocol):
    """T129 correction-specific publication/replay reads."""

    async def find_existing_correction(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        published_review_revision_id: UUID,
        reason: str,
        *,
        transaction: object,
    ) -> ExistingReviewCorrection | None: ...

    async def require_published_revision(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        published_review_revision_id: UUID,
        *,
        transaction: object,
    ) -> PublishedReviewSnapshot | None: ...


class ReviewCorrectionService:
    def __init__(
        self,
        *,
        successors: ReviewRequirementsRepository,
        publications: ReviewCorrectionPublicationRepository,
        revisions: SuccessorRevisionRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._successors = successors
        self._publications = publications
        self._revisions = revisions
        self._authorizer = authorizer
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def create(
        self,
        command: CreateReviewCorrectionCommand,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> ReviewCorrectionResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=command.organization_id,
            policy=_CORRECT,
        )
        if actor.user_id is None:
            raise ReviewCorrectionConflict("correction requires a human actor")
        existing = await self._find_existing(command, transaction=transaction)
        if existing is not None:
            result = self._existing_result(command, existing)
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return result

        context = await self._successors.lock_current_predecessor(
            command.organization_id,
            command.predecessor_iteration_id,
            expected_predecessor_revision=command.expected_predecessor_revision,
            transaction=transaction,
        )
        if context is None:
            winner = await self._find_existing(command, transaction=transaction)
            if winner is not None:
                result = self._existing_result(command, winner)
                await self._authorizer.revalidate_for_commit(
                    grant,
                    transaction=transaction,
                )
                return result
            raise ReviewCorrectionConflict(
                "predecessor is stale or no longer the current ReviewCase iteration"
            )
        self._validate_context(command, context)
        published = await self._publications.require_published_revision(
            command.organization_id,
            command.predecessor_iteration_id,
            command.published_review_revision_id,
            transaction=transaction,
        )
        if published is None:
            raise ReviewCorrectionNotFound(
                "exact published ReviewRevision was not found for predecessor"
            )
        self._validate_published(command, context, published)

        successor_id = self._id_factory()
        relation_id = self._id_factory()
        revision_id = self._id_factory()
        now = require_utc(self._clock())
        iteration_number = await self._successors.next_iteration_number(
            command.organization_id,
            context.review_case_id,
            transaction=transaction,
        )
        if iteration_number != context.iteration_number + 1:
            raise ReviewCorrectionConflict(
                "correction successor is not the next ReviewCase iteration"
            )
        successor = ReviewIteration(
            id=successor_id,
            organization_id=command.organization_id,
            review_case_id=context.review_case_id,
            course_run_id=context.course_run_id,
            homework_id=context.homework_id,
            student_id=context.student_id,
            iteration_number=iteration_number,
            submission_version_id=context.submission_version_id,
            artifact_version_id=context.artifact_version_id,
            homework_version_id=context.predecessor_homework_version_id,
            criterion_set_id=context.predecessor_criterion_set_id,
            effective_deadline=context.effective_deadline,
            responsible_reviewer_id=context.responsible_reviewer_id,
            status="in_review",
            current_revision_id=None,
            predecessor_iteration_id=context.predecessor_iteration_id,
            origin="correction",
            revision=0,
        )
        relation = ReviewIterationRelation(
            id=relation_id,
            organization_id=command.organization_id,
            review_case_id=context.review_case_id,
            predecessor_iteration_id=context.predecessor_iteration_id,
            successor_iteration_id=successor_id,
            kind="correction",
            created_at=now,
        )
        await self._successors.append_successor(
            successor,
            relation,
            transaction=transaction,
        )

        revision_number = await self._revisions.next_revision_number(
            command.organization_id,
            successor_id,
        )
        if revision_number != 1:
            raise ReviewCorrectionConflict(
                "new correction successor has unexpected revision history"
            )
        draft = self._copy_published_revision(
            published.revision,
            organization_id=command.organization_id,
            successor_iteration_id=successor_id,
            successor_revision_id=revision_id,
            author_user_id=actor.user_id,
            created_at=now,
        )
        revision = await self._revisions.append(draft)
        if (
            revision.organization_id != command.organization_id
            or revision.review_iteration_id != successor_id
            or revision.review_revision_id != revision_id
        ):
            raise ReviewCorrectionConflict(
                "correction revision repository returned different provenance"
            )
        if not await self._revisions.compare_and_set_current(
            command.organization_id,
            successor_id,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            new_revision_id=revision_id,
        ):
            raise ReviewCorrectionConflict("correction human revision CAS lost")
        if not await self._successors.compare_and_set_current_iteration(
            command.organization_id,
            context.review_case_id,
            expected_review_case_revision=context.review_case_revision,
            expected_current_iteration_id=context.predecessor_iteration_id,
            new_iteration_id=successor_id,
            transaction=transaction,
        ):
            raise ReviewCorrectionConflict("ReviewCase correction successor CAS lost")

        await self._audit.record(
            AuditEventDraft(
                organization_id=command.organization_id,
                actor=actor,
                action="create_review_correction",
                entity_type="review_iteration",
                entity_id=successor_id,
                before_revision=context.predecessor_iteration_revision,
                after_revision=1,
                request_id=command.request_id,
                trace_id=command.trace_id,
                outcome="succeeded",
                details={
                    "review_case_id": str(context.review_case_id),
                    "predecessor_iteration_id": str(
                        context.predecessor_iteration_id
                    ),
                    "published_review_revision_id": str(
                        command.published_review_revision_id
                    ),
                    "publication_id": str(published.publication_id),
                    "successor_revision_id": str(revision_id),
                    "reason": command.reason,
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return ReviewCorrectionResult(
            organization_id=command.organization_id,
            review_case_id=context.review_case_id,
            predecessor_iteration_id=context.predecessor_iteration_id,
            published_review_revision_id=command.published_review_revision_id,
            successor_iteration_id=successor_id,
            successor_iteration_revision=1,
            successor_revision_id=revision_id,
            replayed=False,
        )

    async def _find_existing(
        self,
        command: CreateReviewCorrectionCommand,
        *,
        transaction: object,
    ) -> ExistingReviewCorrection | None:
        return await self._publications.find_existing_correction(
            command.organization_id,
            command.predecessor_iteration_id,
            command.published_review_revision_id,
            command.reason,
            transaction=transaction,
        )

    @staticmethod
    def _validate_context(
        command: CreateReviewCorrectionCommand,
        context: RequirementsMigrationContext,
    ) -> None:
        if (
            context.organization_id != command.organization_id
            or context.current_iteration_id != command.predecessor_iteration_id
            or context.predecessor_iteration_id != command.predecessor_iteration_id
            or context.predecessor_iteration_revision
            != command.expected_predecessor_revision
        ):
            raise ReviewCorrectionConflict(
                "repository returned a different current predecessor scope"
            )

    @staticmethod
    def _validate_published(
        command: CreateReviewCorrectionCommand,
        context: RequirementsMigrationContext,
        published: PublishedReviewSnapshot,
    ) -> None:
        revision = published.revision
        if (
            published.organization_id != command.organization_id
            or published.review_iteration_id != command.predecessor_iteration_id
            or revision.organization_id != command.organization_id
            or revision.review_iteration_id != command.predecessor_iteration_id
            or revision.review_revision_id != command.published_review_revision_id
        ):
            raise ReviewCorrectionConflict(
                "published ReviewRevision provenance mismatched correction command"
            )
        criterion_limits = {
            criterion.criterion_id: criterion.max_points
            for criterion in context.predecessor_criteria
        }
        criteria = set(criterion_limits)
        decisions = {decision.criterion_id for decision in revision.decisions}
        note_criteria = {
            note.criterion_id
            for note in revision.notes
            if note.criterion_id is not None
        }
        total = sum(
            (decision.points for decision in revision.decisions),
            start=Decimal("0"),
        )
        if (
            decisions != criteria
            or len(criteria) != len(context.predecessor_criteria)
            or len({item.stable_key for item in context.predecessor_criteria})
            != len(context.predecessor_criteria)
            or not note_criteria.issubset(criteria)
            or len(decisions) != len(revision.decisions)
            or any(
                decision.points < 0
                or decision.points > criterion_limits[decision.criterion_id]
                for decision in revision.decisions
            )
            or total != revision.total_score
        ):
            raise ReviewCorrectionConflict(
                "published ReviewRevision is not an exact immutable criterion snapshot"
            )

    def _copy_published_revision(
        self,
        source: ReviewRevisionRecord,
        *,
        organization_id: UUID,
        successor_iteration_id: UUID,
        successor_revision_id: UUID,
        author_user_id: UUID,
        created_at: datetime,
    ) -> ReviewRevisionDraft:
        return ReviewRevisionDraft(
            organization_id=organization_id,
            review_revision_id=successor_revision_id,
            review_iteration_id=successor_iteration_id,
            revision_number=1,
            author_user_id=author_user_id,
            base_revision_id=None,
            feedback=source.feedback,
            decisions=tuple(
                ReviewDecisionDraft(
                    decision_id=self._id_factory(),
                    criterion_id=decision.criterion_id,
                    points=decision.points,
                    decision=decision.decision,
                    reason=decision.reason,
                    evidence_ids=decision.evidence_ids,
                    # The source suggestion belongs to the predecessor AI run.
                    ai_suggestion_id=None,
                )
                for decision in source.decisions
            ),
            notes=tuple(
                ReviewNoteDraft(
                    note_id=self._id_factory(),
                    criterion_id=note.criterion_id,
                    text=note.text,
                    author_user_id=author_user_id,
                    position=note.position,
                )
                for note in source.notes
            ),
            created_at=created_at,
        )

    @staticmethod
    def _existing_result(
        command: CreateReviewCorrectionCommand,
        existing: ExistingReviewCorrection,
    ) -> ReviewCorrectionResult:
        if (
            existing.organization_id != command.organization_id
            or existing.predecessor_iteration_id
            != command.predecessor_iteration_id
            or existing.published_review_revision_id
            != command.published_review_revision_id
            or existing.reason != command.reason
        ):
            raise ReviewCorrectionConflict(
                "existing correction provenance mismatched command"
            )
        return ReviewCorrectionResult(
            organization_id=existing.organization_id,
            review_case_id=existing.review_case_id,
            predecessor_iteration_id=existing.predecessor_iteration_id,
            published_review_revision_id=existing.published_review_revision_id,
            successor_iteration_id=existing.successor_iteration_id,
            successor_iteration_revision=existing.successor_iteration_revision,
            successor_revision_id=existing.successor_revision_id,
            replayed=True,
        )


__all__ = [
    "CreateReviewCorrectionCommand",
    "ExistingReviewCorrection",
    "PublishedReviewSnapshot",
    "ReviewCorrectionConflict",
    "ReviewCorrectionError",
    "ReviewCorrectionNotFound",
    "ReviewCorrectionPublicationRepository",
    "ReviewCorrectionResult",
    "ReviewCorrectionService",
]
