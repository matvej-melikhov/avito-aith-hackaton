"""Interactive-human publication with atomic external delivery intents."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, cast
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.ports.providers import JsonValue
from review_platform.application.request_context import RequestActor
from review_platform.application.services.publication_requests import (
    PublicationRequestRecord,
)
from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry
from review_platform.domain.primitives import (
    canonical_json_sha256,
    require_utc,
    utc_now,
    uuid7,
    validate_digest,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewRevisionRecord,
)

type DestinationKind = Literal["stepik", "github"]

_PUBLISH = AuthorizationPolicy(
    required_roles=frozenset({"reviewer", "methodologist"}),
    allowed_actor_types=frozenset({"user"}),
)


class ReviewPublicationError(RuntimeError):
    """Base typed publication boundary failure."""


class ReviewPublicationNotFound(ReviewPublicationError):
    pass


class ReviewPublicationConflict(ReviewPublicationError):
    pass


class ReviewPublicationArchived(ReviewPublicationError):
    pass


@dataclass(frozen=True, slots=True)
class PublishReviewCommand:
    organization_id: UUID
    review_iteration_id: UUID
    expected_iteration_revision: int
    expected_current_revision_id: UUID
    publication_request_id: UUID | None
    request_id: UUID
    trace_id: UUID

    def __post_init__(self) -> None:
        if self.expected_iteration_revision < 0:
            raise ValueError("expected ReviewIteration revision must be nonnegative")


@dataclass(frozen=True, slots=True)
class PublicationCriterion:
    criterion_id: UUID
    position: int
    max_points: Decimal


@dataclass(frozen=True, slots=True)
class DestinationSnapshot:
    organization_id: UUID
    course_run_id: UUID
    destination_binding_id: UUID
    binding_version: int
    credential_binding_id: UUID
    credential_binding_version: int
    kind: DestinationKind
    recipient_ref: str
    required: bool
    status: str


@dataclass(frozen=True, slots=True)
class ReviewPublicationContext:
    organization_id: UUID
    review_case_id: UUID
    current_iteration_id: UUID
    review_iteration_id: UUID
    iteration_revision: int
    current_review_revision_id: UUID | None
    iteration_status: str
    course_id: UUID
    course_status: str
    course_run_id: UUID
    course_run_status: str
    homework_version_id: UUID
    homework_max_score: Decimal
    criterion_set_id: UUID
    submission_version_id: UUID
    artifact_version_id: UUID
    artifact_content_digest: str
    current_revision: ReviewRevisionRecord
    criteria: tuple[PublicationCriterion, ...]
    destinations: tuple[DestinationSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ReviewPublicationRecord:
    organization_id: UUID
    publication_id: UUID
    review_iteration_id: UUID
    review_revision_id: UUID
    publication_request_id: UUID | None
    publication_version: int
    published_by: UUID
    published_at: datetime
    status: Literal["published"] = "published"
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ExternalDeliveryIntent:
    organization_id: UUID
    delivery_id: UUID
    operation_id: UUID
    publication_id: UUID
    delivery_key: str
    destination: DestinationSnapshot
    course_run_id: UUID
    homework_version_id: UUID
    criterion_set_id: UUID
    submission_version_id: UUID
    artifact_version_id: UUID
    artifact_content_digest: str
    review_iteration_id: UUID
    review_revision_id: UUID
    contract_version: str
    publication_fingerprint: str
    payload_version: str
    payload_digest: str
    request: Mapping[str, JsonValue]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class ExistingReviewPublication:
    publication: ReviewPublicationRecord
    review_iteration_revision: int
    deliveries: tuple[ExternalDeliveryIntent, ...]


@dataclass(frozen=True, slots=True)
class ReviewPublicationResult:
    organization_id: UUID
    publication_id: UUID
    review_iteration_id: UUID
    review_revision_id: UUID
    publication_request_id: UUID | None
    review_iteration_revision: int
    delivery_ids: tuple[UUID, ...]
    operation_ids: tuple[UUID, ...]
    replayed: bool


class ReviewPublicationRepository(Protocol):
    """T129 port; lock order is Course, CourseRun, ReviewCase, Iteration, Revision."""

    async def find_existing_publication(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        review_revision_id: UUID,
        *,
        transaction: object,
    ) -> ExistingReviewPublication | None: ...

    async def lock_publication_context(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID,
        transaction: object,
    ) -> ReviewPublicationContext | None: ...

    async def next_publication_version(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        transaction: object,
    ) -> int: ...

    async def reserve_publication(
        self,
        publication: ReviewPublicationRecord,
        *,
        transaction: object,
    ) -> tuple[ExistingReviewPublication, bool]: ...

    async def lock_publication_request(
        self,
        organization_id: UUID,
        publication_request_id: UUID,
        *,
        transaction: object,
    ) -> PublicationRequestRecord | None: ...

    async def confirm_publication_request(
        self,
        organization_id: UUID,
        publication_request_id: UUID,
        *,
        expected_revision: int,
        confirmed_by: UUID,
        confirmed_at: datetime,
        transaction: object,
    ) -> bool: ...

    async def compare_and_set_published(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID,
        transaction: object,
    ) -> bool: ...


class ReviewDeliveryScheduler(Protocol):
    """T141 port: atomically add delivery, its Operation, and outbox message."""

    async def schedule(
        self,
        intent: ExternalDeliveryIntent,
        *,
        transaction: object,
    ) -> None: ...


class ReviewPublicationService:
    def __init__(
        self,
        *,
        repository: ReviewPublicationRepository,
        scheduler: ReviewDeliveryScheduler,
        authorizer: Authorizer,
        audit: AuditRecorder,
        registry: ContractRegistry | None = None,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._authorizer = authorizer
        self._audit = audit
        self._registry = registry or ContractRegistry()
        self._id_factory = id_factory
        self._clock = clock

    async def publish(
        self,
        command: PublishReviewCommand,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> ReviewPublicationResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=command.organization_id,
            policy=_PUBLISH,
        )
        if actor.actor_type != "user" or actor.user_id is None:
            raise ReviewPublicationConflict("publication requires an interactive human user")

        existing = await self._repository.find_existing_publication(
            command.organization_id,
            command.review_iteration_id,
            command.expected_current_revision_id,
            transaction=transaction,
        )
        if existing is not None:
            result = self._existing_result(command, existing)
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return result

        context = await self._repository.lock_publication_context(
            command.organization_id,
            command.review_iteration_id,
            expected_iteration_revision=command.expected_iteration_revision,
            expected_current_revision_id=command.expected_current_revision_id,
            transaction=transaction,
        )
        if context is None:
            winner = await self._repository.find_existing_publication(
                command.organization_id,
                command.review_iteration_id,
                command.expected_current_revision_id,
                transaction=transaction,
            )
            if winner is not None:
                result = self._existing_result(command, winner)
                await self._authorizer.revalidate_for_commit(
                    grant,
                    transaction=transaction,
                )
                return result
            raise ReviewPublicationConflict(
                "ReviewIteration is stale or current revision changed"
            )
        self._validate_context(command, context)
        now = require_utc(self._clock())
        request = await self._optional_request(
            command,
            context,
            actor_user_id=actor.user_id,
            now=now,
            transaction=transaction,
        )
        publication_version = await self._repository.next_publication_version(
            command.organization_id,
            command.review_iteration_id,
            transaction=transaction,
        )
        if publication_version < 1:
            raise ReviewPublicationConflict("publication version must be positive")
        publication = ReviewPublicationRecord(
            organization_id=command.organization_id,
            publication_id=self._id_factory(),
            review_iteration_id=command.review_iteration_id,
            review_revision_id=command.expected_current_revision_id,
            publication_request_id=(
                request.publication_request_id if request is not None else None
            ),
            publication_version=publication_version,
            published_by=actor.user_id,
            published_at=now,
        )
        stored, created = await self._repository.reserve_publication(
            publication,
            transaction=transaction,
        )
        if not created:
            result = self._existing_result(command, stored)
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return result
        self._validate_reserved(
            publication,
            stored,
            expected_iteration_revision=command.expected_iteration_revision + 1,
        )

        payload = self._delivery_payload(context.current_revision)
        payload_digest = canonical_json_sha256(payload)
        provenance = self._provenance(context)
        publication_fingerprint = canonical_json_sha256(
            {
                "contract_version": CONTRACT_VERSION,
                "organization_id": str(command.organization_id),
                "publication_id": str(publication.publication_id),
                "publication_version": publication.publication_version,
                "provenance": provenance,
                "payload_digest": payload_digest,
            }
        )
        intents = tuple(
            self._intent(
                publication,
                destination,
                context=context,
                provenance=provenance,
                payload=payload,
                payload_digest=payload_digest,
                publication_fingerprint=publication_fingerprint,
            )
            for destination in context.destinations
        )
        if (
            len({intent.delivery_id for intent in intents}) != len(intents)
            or len({intent.operation_id for intent in intents}) != len(intents)
            or {intent.delivery_id for intent in intents}.intersection(
                intent.operation_id for intent in intents
            )
        ):
            raise ReviewPublicationConflict(
                "delivery and Operation identities must be globally distinct"
            )
        for intent in intents:
            await self._scheduler.schedule(intent, transaction=transaction)

        if request is not None and not await self._repository.confirm_publication_request(
            command.organization_id,
            request.publication_request_id,
            expected_revision=request.revision,
            confirmed_by=actor.user_id,
            confirmed_at=now,
            transaction=transaction,
        ):
            raise ReviewPublicationConflict("PublicationRequest confirmation CAS lost")
        if not await self._repository.compare_and_set_published(
            command.organization_id,
            command.review_iteration_id,
            expected_iteration_revision=command.expected_iteration_revision,
            expected_current_revision_id=command.expected_current_revision_id,
            transaction=transaction,
        ):
            raise ReviewPublicationConflict("ReviewIteration publication CAS lost")

        await self._audit.record(
            AuditEventDraft(
                organization_id=command.organization_id,
                actor=actor,
                action="publish_review",
                entity_type="review_publication",
                entity_id=publication.publication_id,
                before_revision=command.expected_iteration_revision,
                after_revision=command.expected_iteration_revision + 1,
                request_id=command.request_id,
                trace_id=command.trace_id,
                outcome="succeeded",
                details={
                    "review_iteration_id": str(command.review_iteration_id),
                    "review_revision_id": str(command.expected_current_revision_id),
                    "publication_request_id": (
                        str(request.publication_request_id)
                        if request is not None
                        else None
                    ),
                    "destination_count": len(intents),
                    "delivery_ids": [str(intent.delivery_id) for intent in intents],
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return ReviewPublicationResult(
            organization_id=command.organization_id,
            publication_id=publication.publication_id,
            review_iteration_id=command.review_iteration_id,
            review_revision_id=command.expected_current_revision_id,
            publication_request_id=publication.publication_request_id,
            review_iteration_revision=command.expected_iteration_revision + 1,
            delivery_ids=tuple(intent.delivery_id for intent in intents),
            operation_ids=tuple(intent.operation_id for intent in intents),
            replayed=False,
        )

    async def _optional_request(
        self,
        command: PublishReviewCommand,
        context: ReviewPublicationContext,
        *,
        actor_user_id: UUID,
        now: datetime,
        transaction: object,
    ) -> PublicationRequestRecord | None:
        del actor_user_id
        if command.publication_request_id is None:
            return None
        request = await self._repository.lock_publication_request(
            command.organization_id,
            command.publication_request_id,
            transaction=transaction,
        )
        if request is None:
            raise ReviewPublicationNotFound("PublicationRequest was not found")
        if (
            request.organization_id != command.organization_id
            or request.publication_request_id != command.publication_request_id
            or request.review_iteration_id != context.review_iteration_id
            or request.review_revision_id != context.current_revision.review_revision_id
            or request.status != "pending"
            or require_utc(request.expires_at) <= now
        ):
            raise ReviewPublicationConflict(
                "PublicationRequest is not a matching unexpired pending request"
            )
        return request

    @staticmethod
    def _validate_context(
        command: PublishReviewCommand,
        context: ReviewPublicationContext,
    ) -> None:
        revision = context.current_revision
        if (
            context.organization_id != command.organization_id
            or context.review_iteration_id != command.review_iteration_id
            or context.current_iteration_id != command.review_iteration_id
            or context.iteration_revision != command.expected_iteration_revision
            or context.current_review_revision_id
            != command.expected_current_revision_id
            or revision.organization_id != command.organization_id
            or revision.review_iteration_id != command.review_iteration_id
            or revision.review_revision_id != command.expected_current_revision_id
        ):
            raise ReviewPublicationConflict("publication context provenance mismatched")
        if context.course_status != "active" or context.course_run_status != "active":
            raise ReviewPublicationArchived("archived Course or CourseRun rejects publication")
        if context.iteration_status not in {"in_review", "ready_to_publish"}:
            raise ReviewPublicationConflict(
                "ReviewIteration is not editable and ready for human publication"
            )
        try:
            validate_digest(context.artifact_content_digest)
        except ValueError as error:
            raise ReviewPublicationConflict(
                "publication artifact digest is invalid"
            ) from error
        criteria = {criterion.criterion_id: criterion for criterion in context.criteria}
        decision_ids = [decision.criterion_id for decision in revision.decisions]
        total = sum(
            (decision.points for decision in revision.decisions),
            start=Decimal("0"),
        )
        if (
            not criteria
            or len(criteria) != len(context.criteria)
            or set(decision_ids) != set(criteria)
            or len(decision_ids) != len(set(decision_ids))
            or any(
                decision.points < 0
                or decision.points > criteria[decision.criterion_id].max_points
                for decision in revision.decisions
            )
            or total != revision.total_score
            or total > context.homework_max_score
        ):
            raise ReviewPublicationConflict(
                "current ReviewRevision is incomplete or has invalid scores"
            )
        destination_ids = [
            (destination.destination_binding_id, destination.binding_version)
            for destination in context.destinations
        ]
        if len(destination_ids) != len(set(destination_ids)):
            raise ReviewPublicationConflict("duplicate destination binding snapshot")
        for destination in context.destinations:
            if (
                destination.organization_id != command.organization_id
                or destination.course_run_id != context.course_run_id
                or not destination.required
                or destination.status != "active"
                or destination.binding_version < 1
                or destination.credential_binding_version < 1
                or destination.kind not in {"stepik", "github"}
                or not destination.recipient_ref
            ):
                raise ReviewPublicationConflict(
                    "required DestinationBinding snapshot is invalid"
                )

    @staticmethod
    def _delivery_payload(revision: ReviewRevisionRecord) -> dict[str, JsonValue]:
        return {
            "total_score": float(revision.total_score),
            "feedback": revision.feedback,
            "criteria": [
                {
                    "criterion_id": str(decision.criterion_id),
                    "points": float(decision.points),
                    "reason": decision.reason,
                }
                for decision in revision.decisions
            ],
        }

    @staticmethod
    def _provenance(context: ReviewPublicationContext) -> dict[str, JsonValue]:
        return {
            "course_run_id": str(context.course_run_id),
            "homework_version_id": str(context.homework_version_id),
            "criterion_set_id": str(context.criterion_set_id),
            "submission_version_id": str(context.submission_version_id),
            "artifact_version_id": str(context.artifact_version_id),
            "artifact_content_digest": context.artifact_content_digest,
            "review_iteration_id": str(context.review_iteration_id),
            "review_revision_id": str(context.current_revision.review_revision_id),
            "contract_version": CONTRACT_VERSION,
        }

    def _intent(
        self,
        publication: ReviewPublicationRecord,
        destination: DestinationSnapshot,
        *,
        context: ReviewPublicationContext,
        provenance: Mapping[str, JsonValue],
        payload: Mapping[str, JsonValue],
        payload_digest: str,
        publication_fingerprint: str,
    ) -> ExternalDeliveryIntent:
        delivery_id = self._id_factory()
        operation_id = self._id_factory()
        delivery_key = (
            f"review:{publication.publication_id}:"
            f"{destination.destination_binding_id}:{destination.binding_version}"
        )
        request: dict[str, JsonValue] = {
            "contract_version": CONTRACT_VERSION,
            "organization_id": str(publication.organization_id),
            "delivery_id": str(delivery_id),
            "delivery_key": delivery_key,
            "destination": {
                "binding_id": str(destination.destination_binding_id),
                "binding_version": destination.binding_version,
                "credential_binding_id": str(destination.credential_binding_id),
                "credential_binding_version": destination.credential_binding_version,
                "kind": destination.kind,
                "recipient_ref": destination.recipient_ref,
            },
            "provenance": dict(provenance),
            "publication_fingerprint": publication_fingerprint,
            "payload_digest": payload_digest,
            "payload": dict(payload),
        }
        try:
            self._registry.validate(
                request,
                "delivery.schema.json",
                definition="deliver_request",
            )
        except Exception as error:
            raise ReviewPublicationConflict(
                "delivery intent violates frozen 1.1.0 contract"
            ) from error
        return ExternalDeliveryIntent(
            organization_id=publication.organization_id,
            delivery_id=delivery_id,
            operation_id=operation_id,
            publication_id=publication.publication_id,
            delivery_key=delivery_key,
            destination=destination,
            course_run_id=context.course_run_id,
            homework_version_id=context.homework_version_id,
            criterion_set_id=context.criterion_set_id,
            submission_version_id=context.submission_version_id,
            artifact_version_id=context.artifact_version_id,
            artifact_content_digest=context.artifact_content_digest,
            review_iteration_id=context.review_iteration_id,
            review_revision_id=context.current_revision.review_revision_id,
            contract_version=CONTRACT_VERSION,
            publication_fingerprint=publication_fingerprint,
            payload_version=CONTRACT_VERSION,
            payload_digest=payload_digest,
            request=cast(Mapping[str, JsonValue], request),
            requested_at=publication.published_at,
        )

    @staticmethod
    def _validate_reserved(
        proposed: ReviewPublicationRecord,
        stored: ExistingReviewPublication,
        *,
        expected_iteration_revision: int,
    ) -> None:
        if (
            stored.publication != proposed
            or stored.review_iteration_revision != expected_iteration_revision
            or stored.deliveries
        ):
            raise ReviewPublicationConflict(
                "new publication reserve returned different or prepopulated state"
            )

    @staticmethod
    def _existing_result(
        command: PublishReviewCommand,
        existing: ExistingReviewPublication,
    ) -> ReviewPublicationResult:
        publication = existing.publication
        if (
            publication.organization_id != command.organization_id
            or publication.review_iteration_id != command.review_iteration_id
            or publication.review_revision_id != command.expected_current_revision_id
            or publication.publication_request_id != command.publication_request_id
        ):
            raise ReviewPublicationConflict(
                "existing publication provenance mismatched publish command"
            )
        delivery_ids = tuple(delivery.delivery_id for delivery in existing.deliveries)
        operation_ids = tuple(delivery.operation_id for delivery in existing.deliveries)
        if len(operation_ids) != len(set(operation_ids)):
            raise ReviewPublicationConflict(
                "existing publication has duplicate delivery Operation identity"
            )
        return ReviewPublicationResult(
            organization_id=publication.organization_id,
            publication_id=publication.publication_id,
            review_iteration_id=publication.review_iteration_id,
            review_revision_id=publication.review_revision_id,
            publication_request_id=publication.publication_request_id,
            review_iteration_revision=existing.review_iteration_revision,
            delivery_ids=delivery_ids,
            operation_ids=operation_ids,
            replayed=True,
        )


__all__ = [
    "DestinationKind",
    "DestinationSnapshot",
    "ExistingReviewPublication",
    "ExternalDeliveryIntent",
    "PublicationCriterion",
    "PublishReviewCommand",
    "ReviewDeliveryScheduler",
    "ReviewPublicationArchived",
    "ReviewPublicationConflict",
    "ReviewPublicationContext",
    "ReviewPublicationError",
    "ReviewPublicationNotFound",
    "ReviewPublicationRecord",
    "ReviewPublicationRepository",
    "ReviewPublicationResult",
    "ReviewPublicationService",
]
