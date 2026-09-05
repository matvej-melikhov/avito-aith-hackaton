"""Idempotent projection of homework requirement changes onto affected reviews."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from review_platform.application.services.homeworks import HomeworkRequirementsChanged
from review_platform.contracts.registry import CONTRACT_VERSION
from review_platform.domain.primitives import require_utc, utc_now, uuid7


class ReviewRequirementImpactError(RuntimeError):
    """Base typed impact-projection failure."""


class ReviewRequirementImpactNotFound(ReviewRequirementImpactError):
    pass


class ReviewRequirementImpactConflict(ReviewRequirementImpactError):
    pass


@dataclass(frozen=True, slots=True)
class RequirementsChangeContext:
    organization_id: UUID
    course_run_id: UUID
    course_run_homework_id: UUID
    homework_id: UUID
    previous_homework_version_id: UUID
    current_homework_version_id: UUID
    current_criterion_set_id: UUID
    previous_publication_id: UUID
    current_publication_id: UUID
    publication_sequence: int


@dataclass(frozen=True, slots=True)
class AffectedReviewContext:
    organization_id: UUID
    review_case_id: UUID
    review_iteration_id: UUID
    course_run_id: UUID
    course_run_homework_id: UUID
    homework_id: UUID
    effective_homework_version_id: UUID
    iteration_number: int
    iteration_status: str


@dataclass(frozen=True, slots=True)
class ReviewImpactRecord:
    impact_id: UUID
    organization_id: UUID
    source_event_id: UUID
    review_case_id: UUID
    review_iteration_id: UUID
    course_run_homework_id: UUID
    course_run_id: UUID
    homework_id: UUID
    previous_homework_version_id: UUID
    current_homework_version_id: UUID
    previous_publication_id: UUID
    current_publication_id: UUID
    publication_sequence: int
    occurred_at: datetime
    resolved_by_iteration_id: UUID | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReviewSuccessorProposal:
    organization_id: UUID
    review_case_id: UUID
    predecessor_iteration_id: UUID
    predecessor_iteration_number: int
    effective_homework_version_id: UUID
    target_homework_version_id: UUID
    target_criterion_set_id: UUID
    origin: str = "requirements_migration"


@dataclass(frozen=True, slots=True)
class ReviewRequirementImpactResult:
    organization_id: UUID
    source_event_id: UUID
    created_impact_ids: tuple[UUID, ...]
    replayed_impact_ids: tuple[UUID, ...]
    proposals: tuple[ReviewSuccessorProposal, ...]


class ReviewRequirementImpactRepository(Protocol):
    """Narrow T129/T132 port. It exposes append and projection reads only."""

    async def lock_change_context(
        self,
        event: HomeworkRequirementsChanged,
        *,
        transaction: object,
    ) -> RequirementsChangeContext | None: ...

    async def list_affected_reviews(
        self,
        context: RequirementsChangeContext,
        *,
        transaction: object,
    ) -> Sequence[AffectedReviewContext]: ...

    async def reserve_impact(
        self,
        candidate: ReviewImpactRecord,
        *,
        transaction: object,
    ) -> tuple[ReviewImpactRecord, bool]: ...


class ReviewRequirementImpactOperationPort(Protocol):
    async def record_ingestion(
        self,
        *,
        organization_id: UUID,
        source_event_id: UUID,
        created_count: int,
        replayed_count: int,
        transaction: object,
    ) -> None: ...


class ReviewRequirementImpactAuditPort(Protocol):
    async def record_impacts(
        self,
        *,
        organization_id: UUID,
        source_event_id: UUID,
        created_impact_ids: tuple[UUID, ...],
        affected_iteration_ids: tuple[UUID, ...],
        transaction: object,
    ) -> None: ...


class ReviewRequirementImpactService:
    def __init__(
        self,
        *,
        repository: ReviewRequirementImpactRepository,
        operations: ReviewRequirementImpactOperationPort,
        audit: ReviewRequirementImpactAuditPort,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._operations = operations
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def consume(
        self,
        *,
        source_event_id: UUID,
        event: HomeworkRequirementsChanged,
        transaction: object,
    ) -> ReviewRequirementImpactResult:
        self._validate_event(source_event_id, event)
        # A first-ever publication has no older requirements to affect.
        if (
            event.previous_homework_version_id is None
            or event.previous_publication_id is None
        ):
            await self._operations.record_ingestion(
                organization_id=event.organization_id,
                source_event_id=source_event_id,
                created_count=0,
                replayed_count=0,
                transaction=transaction,
            )
            return ReviewRequirementImpactResult(
                event.organization_id,
                source_event_id,
                (),
                (),
                (),
            )
        context = await self._repository.lock_change_context(
            event,
            transaction=transaction,
        )
        if context is None:
            raise ReviewRequirementImpactNotFound(
                "tenant CourseRunHomework current publication/version was not found"
            )
        self._validate_change_context(event, context)
        affected = tuple(
            await self._repository.list_affected_reviews(
                context,
                transaction=transaction,
            )
        )
        self._validate_affected(context, affected)
        occurred_at = require_utc(self._clock())
        created_ids: list[UUID] = []
        replayed_ids: list[UUID] = []
        proposals: list[ReviewSuccessorProposal] = []
        for review in affected:
            candidate = ReviewImpactRecord(
                impact_id=self._id_factory(),
                organization_id=context.organization_id,
                source_event_id=source_event_id,
                review_case_id=review.review_case_id,
                review_iteration_id=review.review_iteration_id,
                course_run_homework_id=context.course_run_homework_id,
                course_run_id=context.course_run_id,
                homework_id=context.homework_id,
                previous_homework_version_id=context.previous_homework_version_id,
                current_homework_version_id=context.current_homework_version_id,
                previous_publication_id=context.previous_publication_id,
                current_publication_id=context.current_publication_id,
                publication_sequence=context.publication_sequence,
                occurred_at=occurred_at,
            )
            stored, created = await self._repository.reserve_impact(
                candidate,
                transaction=transaction,
            )
            self._validate_stored(candidate, stored)
            (created_ids if created else replayed_ids).append(stored.impact_id)
            proposals.append(
                ReviewSuccessorProposal(
                    organization_id=context.organization_id,
                    review_case_id=review.review_case_id,
                    predecessor_iteration_id=review.review_iteration_id,
                    predecessor_iteration_number=review.iteration_number,
                    effective_homework_version_id=(
                        review.effective_homework_version_id
                    ),
                    target_homework_version_id=context.current_homework_version_id,
                    target_criterion_set_id=context.current_criterion_set_id,
                )
            )
        await self._operations.record_ingestion(
            organization_id=context.organization_id,
            source_event_id=source_event_id,
            created_count=len(created_ids),
            replayed_count=len(replayed_ids),
            transaction=transaction,
        )
        if created_ids:
            await self._audit.record_impacts(
                organization_id=context.organization_id,
                source_event_id=source_event_id,
                created_impact_ids=tuple(created_ids),
                affected_iteration_ids=tuple(
                    review.review_iteration_id for review in affected
                ),
                transaction=transaction,
            )
        return ReviewRequirementImpactResult(
            organization_id=context.organization_id,
            source_event_id=source_event_id,
            created_impact_ids=tuple(created_ids),
            replayed_impact_ids=tuple(replayed_ids),
            proposals=tuple(proposals),
        )

    @staticmethod
    def _validate_event(
        source_event_id: UUID,
        event: HomeworkRequirementsChanged,
    ) -> None:
        if (
            event.contract_version != CONTRACT_VERSION
            or event.publication_sequence < 1
            or event.current_homework_version_id
            == event.previous_homework_version_id
            or event.current_publication_id == event.previous_publication_id
        ):
            raise ReviewRequirementImpactConflict(
                "HomeworkRequirementsChanged identity/version is invalid"
            )
        if source_event_id.int == 0:
            raise ReviewRequirementImpactConflict("source event identity is invalid")

    @staticmethod
    def _validate_change_context(
        event: HomeworkRequirementsChanged,
        context: RequirementsChangeContext,
    ) -> None:
        expected = (
            event.organization_id,
            event.course_run_id,
            event.course_run_homework_id,
            event.homework_id,
            event.previous_homework_version_id,
            event.current_homework_version_id,
            event.previous_publication_id,
            event.current_publication_id,
            event.publication_sequence,
        )
        actual = (
            context.organization_id,
            context.course_run_id,
            context.course_run_homework_id,
            context.homework_id,
            context.previous_homework_version_id,
            context.current_homework_version_id,
            context.previous_publication_id,
            context.current_publication_id,
            context.publication_sequence,
        )
        if actual != expected or context.current_criterion_set_id.int == 0:
            raise ReviewRequirementImpactConflict(
                "CourseRunHomework publication provenance mismatched event"
            )

    @staticmethod
    def _validate_affected(
        context: RequirementsChangeContext,
        affected: tuple[AffectedReviewContext, ...],
    ) -> None:
        identities: set[UUID] = set()
        ordering: list[tuple[int, UUID]] = []
        for review in affected:
            if (
                review.organization_id != context.organization_id
                or review.course_run_id != context.course_run_id
                or review.course_run_homework_id
                != context.course_run_homework_id
                or review.homework_id != context.homework_id
                or review.effective_homework_version_id
                == context.current_homework_version_id
                or review.review_iteration_id in identities
                or review.iteration_number < 1
            ):
                raise ReviewRequirementImpactConflict(
                    "affected review projection crossed scope or included current requirements"
                )
            identities.add(review.review_iteration_id)
            ordering.append((review.iteration_number, review.review_iteration_id))
        if ordering != sorted(ordering):
            raise ReviewRequirementImpactConflict(
                "affected reviews must use deterministic iteration ordering"
            )

    @staticmethod
    def _validate_stored(
        candidate: ReviewImpactRecord,
        stored: ReviewImpactRecord,
    ) -> None:
        identity_fields = (
            "organization_id",
            "source_event_id",
            "review_case_id",
            "review_iteration_id",
            "course_run_homework_id",
            "course_run_id",
            "homework_id",
            "previous_homework_version_id",
            "current_homework_version_id",
            "previous_publication_id",
            "current_publication_id",
            "publication_sequence",
        )
        if any(
            getattr(candidate, field) != getattr(stored, field)
            for field in identity_fields
        ):
            raise ReviewRequirementImpactConflict(
                "idempotent ReviewImpactEvent replay provenance mismatched"
            )


__all__ = [
    "AffectedReviewContext",
    "RequirementsChangeContext",
    "ReviewImpactRecord",
    "ReviewRequirementImpactAuditPort",
    "ReviewRequirementImpactConflict",
    "ReviewRequirementImpactError",
    "ReviewRequirementImpactNotFound",
    "ReviewRequirementImpactOperationPort",
    "ReviewRequirementImpactRepository",
    "ReviewRequirementImpactResult",
    "ReviewRequirementImpactService",
    "ReviewSuccessorProposal",
]
