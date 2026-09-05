"""Explicit requirements-migration successor creation for human reviews."""

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
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.db.models.publication import ReviewIterationRelation
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionDraft,
    ReviewNoteDraft,
    ReviewRevisionDraft,
    ReviewRevisionRecord,
)

_MIGRATE = AuthorizationPolicy(required_roles=frozenset({"reviewer", "methodologist"}))


class ReviewRequirementsError(RuntimeError):
    """Base typed requirements migration failure."""


class ReviewRequirementsNotFound(ReviewRequirementsError):
    pass


class ReviewRequirementsConflict(ReviewRequirementsError):
    pass


@dataclass(frozen=True, slots=True)
class MigrateReviewRequirementsCommand:
    organization_id: UUID
    predecessor_iteration_id: UUID
    expected_predecessor_revision: int
    target_homework_version_id: UUID
    target_criterion_set_id: UUID
    request_id: UUID
    trace_id: UUID

    def __post_init__(self) -> None:
        if self.expected_predecessor_revision < 0:
            raise ValueError("expected predecessor revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class RequirementsCriterion:
    criterion_id: UUID
    stable_key: str
    position: int
    max_points: Decimal


@dataclass(frozen=True, slots=True)
class RequirementsMigrationContext:
    organization_id: UUID
    review_case_id: UUID
    review_case_revision: int
    current_iteration_id: UUID
    predecessor_iteration_id: UUID
    predecessor_iteration_revision: int
    iteration_number: int
    course_run_id: UUID
    homework_id: UUID
    student_id: UUID
    submission_version_id: UUID
    artifact_version_id: UUID
    effective_deadline: datetime
    responsible_reviewer_id: UUID | None
    status: str
    predecessor_homework_version_id: UUID
    predecessor_criterion_set_id: UUID
    current_human_revision: ReviewRevisionRecord | None
    predecessor_criteria: tuple[RequirementsCriterion, ...]


@dataclass(frozen=True, slots=True)
class RequirementsTarget:
    organization_id: UUID
    homework_id: UUID
    homework_version_id: UUID
    criterion_set_id: UUID
    criteria: tuple[RequirementsCriterion, ...]


@dataclass(frozen=True, slots=True)
class ExistingRequirementsMigration:
    organization_id: UUID
    review_case_id: UUID
    predecessor_iteration_id: UUID
    successor_iteration_id: UUID
    successor_iteration_revision: int
    successor_revision_id: UUID
    target_homework_version_id: UUID
    target_criterion_set_id: UUID
    transferred_decision_count: int


@dataclass(frozen=True, slots=True)
class RequirementsMigrationResult:
    organization_id: UUID
    review_case_id: UUID
    predecessor_iteration_id: UUID
    successor_iteration_id: UUID
    successor_iteration_revision: int
    successor_revision_id: UUID
    transferred_decision_count: int
    replayed: bool


class ReviewRequirementsRepository(Protocol):
    """T129 port; implementations lock ReviewCase before ReviewIteration."""

    async def find_existing_migration(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        target_homework_version_id: UUID,
        target_criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> ExistingRequirementsMigration | None: ...

    async def lock_current_predecessor(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        *,
        expected_predecessor_revision: int,
        transaction: object,
    ) -> RequirementsMigrationContext | None: ...

    async def load_target(
        self,
        organization_id: UUID,
        homework_id: UUID,
        homework_version_id: UUID,
        criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> RequirementsTarget | None: ...

    async def next_iteration_number(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        transaction: object,
    ) -> int: ...

    async def append_successor(
        self,
        successor: ReviewIteration,
        relation: ReviewIterationRelation,
        *,
        transaction: object,
    ) -> None: ...

    async def compare_and_set_current_iteration(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        expected_review_case_revision: int,
        expected_current_iteration_id: UUID,
        new_iteration_id: UUID,
        transaction: object,
    ) -> bool: ...


class SuccessorRevisionRepository(Protocol):
    async def next_revision_number(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
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


class ReviewRequirementsMigrationService:
    def __init__(
        self,
        *,
        repository: ReviewRequirementsRepository,
        revisions: SuccessorRevisionRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._revisions = revisions
        self._authorizer = authorizer
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def migrate(
        self,
        command: MigrateReviewRequirementsCommand,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> RequirementsMigrationResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=command.organization_id,
            policy=_MIGRATE,
        )
        if actor.user_id is None:
            raise ReviewRequirementsConflict("requirements migration requires a human actor")
        existing = await self._repository.find_existing_migration(
            command.organization_id,
            command.predecessor_iteration_id,
            command.target_homework_version_id,
            command.target_criterion_set_id,
            transaction=transaction,
        )
        if existing is not None:
            result = self._existing_result(command, existing)
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return result

        context = await self._repository.lock_current_predecessor(
            command.organization_id,
            command.predecessor_iteration_id,
            expected_predecessor_revision=command.expected_predecessor_revision,
            transaction=transaction,
        )
        if context is None:
            # A concurrent winner may have advanced ReviewCase between the
            # initial replay read and the fixed-order lock acquisition.
            winner = await self._repository.find_existing_migration(
                command.organization_id,
                command.predecessor_iteration_id,
                command.target_homework_version_id,
                command.target_criterion_set_id,
                transaction=transaction,
            )
            if winner is not None:
                result = self._existing_result(command, winner)
                await self._authorizer.revalidate_for_commit(
                    grant,
                    transaction=transaction,
                )
                return result
            raise ReviewRequirementsConflict(
                "predecessor is stale or no longer the current ReviewCase iteration"
            )
        self._validate_context(command, context)
        target = await self._repository.load_target(
            command.organization_id,
            context.homework_id,
            command.target_homework_version_id,
            command.target_criterion_set_id,
            transaction=transaction,
        )
        if target is None:
            raise ReviewRequirementsNotFound(
                "target HomeworkVersion and CriterionSet were not found in review scope"
            )
        self._validate_target(command, context, target)

        now = require_utc(self._clock())
        successor_id = self._id_factory()
        relation_id = self._id_factory()
        iteration_number = await self._repository.next_iteration_number(
            command.organization_id,
            context.review_case_id,
            transaction=transaction,
        )
        if iteration_number != context.iteration_number + 1:
            raise ReviewRequirementsConflict(
                "successor iteration number is not the next ReviewCase number"
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
            homework_version_id=target.homework_version_id,
            criterion_set_id=target.criterion_set_id,
            effective_deadline=context.effective_deadline,
            responsible_reviewer_id=context.responsible_reviewer_id,
            status="in_review",
            current_revision_id=None,
            predecessor_iteration_id=context.predecessor_iteration_id,
            origin="requirements_migration",
            revision=0,
        )
        relation = ReviewIterationRelation(
            id=relation_id,
            organization_id=command.organization_id,
            review_case_id=context.review_case_id,
            predecessor_iteration_id=context.predecessor_iteration_id,
            successor_iteration_id=successor_id,
            kind="requirements_migration",
            created_at=now,
        )
        await self._repository.append_successor(
            successor,
            relation,
            transaction=transaction,
        )

        revision_id = self._id_factory()
        decisions, notes, feedback = self._transferred_draft(
            context,
            target,
            author_user_id=actor.user_id,
        )
        revision_number = await self._revisions.next_revision_number(
            command.organization_id,
            successor_id,
        )
        if revision_number != 1:
            raise ReviewRequirementsConflict(
                "new requirements successor has unexpected revision history"
            )
        revision = await self._revisions.append(
            ReviewRevisionDraft(
                organization_id=command.organization_id,
                review_revision_id=revision_id,
                review_iteration_id=successor_id,
                revision_number=revision_number,
                author_user_id=actor.user_id,
                base_revision_id=None,
                feedback=feedback,
                decisions=decisions,
                notes=notes,
                created_at=now,
            )
        )
        if (
            revision.organization_id != command.organization_id
            or revision.review_iteration_id != successor_id
            or revision.review_revision_id != revision_id
        ):
            raise ReviewRequirementsConflict(
                "successor revision repository returned different provenance"
            )
        if not await self._revisions.compare_and_set_current(
            command.organization_id,
            successor_id,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            new_revision_id=revision_id,
        ):
            raise ReviewRequirementsConflict("successor human revision CAS lost")
        if not await self._repository.compare_and_set_current_iteration(
            command.organization_id,
            context.review_case_id,
            expected_review_case_revision=context.review_case_revision,
            expected_current_iteration_id=context.predecessor_iteration_id,
            new_iteration_id=successor_id,
            transaction=transaction,
        ):
            raise ReviewRequirementsConflict("ReviewCase successor CAS lost")

        await self._audit.record(
            AuditEventDraft(
                organization_id=command.organization_id,
                actor=actor,
                action="migrate_review_requirements",
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
                    "target_homework_version_id": str(target.homework_version_id),
                    "target_criterion_set_id": str(target.criterion_set_id),
                    "successor_revision_id": str(revision_id),
                    "transferred_decision_count": len(decisions),
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return RequirementsMigrationResult(
            organization_id=command.organization_id,
            review_case_id=context.review_case_id,
            predecessor_iteration_id=context.predecessor_iteration_id,
            successor_iteration_id=successor_id,
            successor_iteration_revision=1,
            successor_revision_id=revision_id,
            transferred_decision_count=len(decisions),
            replayed=False,
        )

    @staticmethod
    def _validate_context(
        command: MigrateReviewRequirementsCommand,
        context: RequirementsMigrationContext,
    ) -> None:
        if (
            context.organization_id != command.organization_id
            or context.current_iteration_id != command.predecessor_iteration_id
            or context.predecessor_iteration_id != command.predecessor_iteration_id
            or context.predecessor_iteration_revision
            != command.expected_predecessor_revision
        ):
            raise ReviewRequirementsConflict(
                "repository returned a different current predecessor scope"
            )
        if context.current_human_revision is not None and (
            context.current_human_revision.organization_id != command.organization_id
            or context.current_human_revision.review_iteration_id
            != command.predecessor_iteration_id
        ):
            raise ReviewRequirementsConflict(
                "current human revision does not belong to predecessor"
            )
        source_ids = [criterion.criterion_id for criterion in context.predecessor_criteria]
        source_keys = [criterion.stable_key for criterion in context.predecessor_criteria]
        if (
            len(set(source_ids)) != len(source_ids)
            or len(set(source_keys)) != len(source_keys)
            or any(not key for key in source_keys)
        ):
            raise ReviewRequirementsConflict("predecessor criterion snapshot is invalid")

    @staticmethod
    def _validate_target(
        command: MigrateReviewRequirementsCommand,
        context: RequirementsMigrationContext,
        target: RequirementsTarget,
    ) -> None:
        if (
            target.organization_id != command.organization_id
            or target.homework_id != context.homework_id
            or target.homework_version_id != command.target_homework_version_id
            or target.criterion_set_id != command.target_criterion_set_id
            or target.homework_version_id == context.predecessor_homework_version_id
            or target.criterion_set_id == context.predecessor_criterion_set_id
        ):
            raise ReviewRequirementsConflict("target requirements provenance mismatched")
        keys = [criterion.stable_key for criterion in target.criteria]
        ids = [criterion.criterion_id for criterion in target.criteria]
        positions = [criterion.position for criterion in target.criteria]
        if (
            not target.criteria
            or len(set(keys)) != len(keys)
            or len(set(ids)) != len(ids)
            or len(set(positions)) != len(positions)
            or any(
                not key or position < 0
                for key, position in zip(keys, positions, strict=True)
            )
            or any(criterion.max_points < 0 for criterion in target.criteria)
        ):
            raise ReviewRequirementsConflict("target criterion snapshot is invalid")

    def _transferred_draft(
        self,
        context: RequirementsMigrationContext,
        target: RequirementsTarget,
        *,
        author_user_id: UUID,
    ) -> tuple[
        tuple[ReviewDecisionDraft, ...],
        tuple[ReviewNoteDraft, ...],
        str,
    ]:
        source = context.current_human_revision
        if source is None:
            return (), (), ""
        source_keys = {
            criterion.criterion_id: criterion.stable_key
            for criterion in context.predecessor_criteria
        }
        target_by_key = {
            criterion.stable_key: criterion for criterion in target.criteria
        }
        decisions: list[ReviewDecisionDraft] = []
        for decision in source.decisions:
            key = source_keys.get(decision.criterion_id)
            target_criterion = target_by_key.get(key) if key is not None else None
            if target_criterion is None:
                continue
            if decision.points > target_criterion.max_points:
                raise ReviewRequirementsConflict(
                    "matching criterion score exceeds target criterion maximum"
                )
            decisions.append(
                ReviewDecisionDraft(
                    decision_id=self._id_factory(),
                    criterion_id=target_criterion.criterion_id,
                    points=decision.points,
                    decision=decision.decision,
                    reason=decision.reason,
                    evidence_ids=decision.evidence_ids,
                    # AI suggestions are criterion/run-specific immutable rows
                    # and cannot be rebound to the successor criterion.
                    ai_suggestion_id=None,
                )
            )
        notes: list[ReviewNoteDraft] = []
        for note in source.notes:
            target_criterion_id: UUID | None = None
            if note.criterion_id is not None:
                key = source_keys.get(note.criterion_id)
                target_criterion = target_by_key.get(key) if key is not None else None
                if target_criterion is None:
                    continue
                target_criterion_id = target_criterion.criterion_id
            notes.append(
                ReviewNoteDraft(
                    note_id=self._id_factory(),
                    criterion_id=target_criterion_id,
                    text=note.text,
                    author_user_id=author_user_id,
                    position=len(notes),
                )
            )
        return tuple(decisions), tuple(notes), source.feedback

    @staticmethod
    def _existing_result(
        command: MigrateReviewRequirementsCommand,
        existing: ExistingRequirementsMigration,
    ) -> RequirementsMigrationResult:
        if (
            existing.organization_id != command.organization_id
            or existing.predecessor_iteration_id
            != command.predecessor_iteration_id
            or existing.target_homework_version_id
            != command.target_homework_version_id
            or existing.target_criterion_set_id != command.target_criterion_set_id
        ):
            raise ReviewRequirementsConflict(
                "existing requirements migration provenance mismatched"
            )
        return RequirementsMigrationResult(
            organization_id=existing.organization_id,
            review_case_id=existing.review_case_id,
            predecessor_iteration_id=existing.predecessor_iteration_id,
            successor_iteration_id=existing.successor_iteration_id,
            successor_iteration_revision=existing.successor_iteration_revision,
            successor_revision_id=existing.successor_revision_id,
            transferred_decision_count=existing.transferred_decision_count,
            replayed=True,
        )


__all__ = [
    "ExistingRequirementsMigration",
    "MigrateReviewRequirementsCommand",
    "RequirementsCriterion",
    "RequirementsMigrationContext",
    "RequirementsMigrationResult",
    "RequirementsTarget",
    "ReviewRequirementsConflict",
    "ReviewRequirementsError",
    "ReviewRequirementsMigrationService",
    "ReviewRequirementsNotFound",
    "ReviewRequirementsRepository",
    "SuccessorRevisionRepository",
]
