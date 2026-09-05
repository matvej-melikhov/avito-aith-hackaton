"""Explicit external-delivery state policy and orchestration ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, sanitize_error, utc_now

type DeliveryState = Literal[
    "pending",
    "processing",
    "retryable_failed",
    "unknown_outcome",
    "reconciling",
    "succeeded",
    "action_required",
    "superseded",
]
type ReconcileOutcome = Literal["found", "not_found", "ambiguous"]

_TERMINAL = frozenset({"succeeded", "action_required", "superseded"})
_TRANSITIONS = {
    "pending": {"processing", "superseded"},
    "processing": {
        "succeeded",
        "retryable_failed",
        "unknown_outcome",
        "action_required",
        "superseded",
    },
    "retryable_failed": {"processing", "action_required", "superseded"},
    "unknown_outcome": {"reconciling", "superseded"},
    "reconciling": {
        "succeeded",
        "retryable_failed",
        "unknown_outcome",
        "action_required",
        "superseded",
    },
}


class DeliveryServiceError(RuntimeError):
    pass


class DeliveryTransitionError(DeliveryServiceError, ValueError):
    pass


def validate_delivery_transition(
    *,
    current_state: str,
    target_state: str,
    attempt_count: int,
    max_attempts: int,
    reconciliation_observed: bool,
) -> str:
    if attempt_count < 0 or max_attempts < 1 or attempt_count > max_attempts:
        raise DeliveryTransitionError("delivery attempt budget is invalid")
    if current_state in _TERMINAL or target_state not in _TRANSITIONS.get(current_state, set()):
        raise DeliveryTransitionError(
            f"illegal or terminal delivery transition: {current_state} -> {target_state}"
        )
    if current_state == "unknown_outcome" and target_state != "reconciling":
        raise DeliveryTransitionError("unknown outcome requires reconciliation before retry")
    if (
        current_state == "reconciling"
        and target_state != "unknown_outcome"
        and not reconciliation_observed
    ):
        raise DeliveryTransitionError("reconciliation result must be observed before transition")
    if target_state == "processing" and attempt_count >= max_attempts:
        raise DeliveryTransitionError("delivery retry cap is exhausted")
    return target_state


@dataclass(frozen=True, slots=True)
class DeliveryProvenance:
    course_run_id: UUID
    homework_version_id: UUID
    criterion_set_id: UUID
    submission_version_id: UUID
    artifact_version_id: UUID
    artifact_content_digest: str
    review_iteration_id: UUID
    review_revision_id: UUID
    contract_version: str


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    organization_id: UUID
    delivery_id: UUID
    state: DeliveryState
    attempt_count: int
    max_attempts: int
    revision: int
    provenance: DeliveryProvenance


class DeliveryRepository(Protocol):
    async def lock(
        self, organization_id: UUID, delivery_id: UUID, *, transaction: object
    ) -> DeliveryRecord | None: ...
    async def transition(
        self,
        delivery: DeliveryRecord,
        *,
        target_state: DeliveryState,
        next_attempt_at: datetime | None,
        error: Mapping[str, object] | None,
        transaction: object,
    ) -> DeliveryRecord: ...


class DeliveryScheduler(Protocol):
    async def schedule(self, delivery: DeliveryRecord, *, transaction: object) -> None: ...


class DeliveryService:
    def __init__(
        self,
        *,
        repository: DeliveryRepository,
        scheduler: DeliveryScheduler,
        authorizer: Authorizer,
        audit: AuditRecorder,
        clock: Callable[[], datetime] = utc_now,
        retry_delay: Callable[[int], timedelta] = lambda attempt: timedelta(
            seconds=60 * 2 ** max(attempt - 1, 0)
        ),
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._authorizer = authorizer
        self._audit = audit
        self._clock = clock
        self._retry_delay = retry_delay

    async def claim(
        self, *, organization_id: UUID, delivery_id: UUID, transaction: object
    ) -> DeliveryRecord:
        delivery = await self._required(organization_id, delivery_id, transaction)
        target = validate_delivery_transition(
            current_state=delivery.state,
            target_state="processing",
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
            reconciliation_observed=delivery.state != "unknown_outcome",
        )
        return await self._repository.transition(
            delivery,
            target_state=cast(DeliveryState, target),
            next_attempt_at=None,
            error=None,
            transaction=transaction,
        )

    async def begin_reconciliation(
        self, *, organization_id: UUID, delivery_id: UUID, transaction: object
    ) -> DeliveryRecord:
        delivery = await self._required(organization_id, delivery_id, transaction)
        validate_delivery_transition(
            current_state=delivery.state,
            target_state="reconciling",
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
            reconciliation_observed=False,
        )
        return await self._repository.transition(
            delivery,
            target_state="reconciling",
            next_attempt_at=None,
            error=None,
            transaction=transaction,
        )

    async def record_worker_result(
        self,
        *,
        organization_id: UUID,
        delivery_id: UUID,
        outcome: Literal["succeeded", "retryable_failed", "unknown_outcome", "action_required"],
        error: Mapping[str, object] | None,
        transaction: object,
    ) -> DeliveryRecord:
        """Advance an already-durable intent without consulting archive state."""

        delivery = await self._required(organization_id, delivery_id, transaction)
        validate_delivery_transition(
            current_state=delivery.state,
            target_state=outcome,
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
            reconciliation_observed=delivery.state == "reconciling",
        )
        retry_at = (
            require_utc(self._clock()) + self._retry_delay(delivery.attempt_count)
            if outcome == "retryable_failed"
            else None
        )
        return await self._repository.transition(
            delivery,
            target_state=outcome,
            next_attempt_at=retry_at,
            error=sanitize_error(error) if error is not None else None,
            transaction=transaction,
        )

    async def apply_reconciliation(
        self,
        *,
        organization_id: UUID,
        delivery_id: UUID,
        outcome: ReconcileOutcome,
        error: Mapping[str, object] | None,
        transaction: object,
    ) -> DeliveryRecord:
        delivery = await self._required(organization_id, delivery_id, transaction)
        outcomes: dict[ReconcileOutcome, DeliveryState] = {
            "found": "succeeded",
            "not_found": "retryable_failed",
            "ambiguous": "action_required",
        }
        target = outcomes[outcome]
        validate_delivery_transition(
            current_state=delivery.state,
            target_state=target,
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
            reconciliation_observed=True,
        )
        retry_at = (
            require_utc(self._clock()) + self._retry_delay(delivery.attempt_count)
            if target == "retryable_failed"
            else None
        )
        return await self._repository.transition(
            delivery,
            target_state=target,
            next_attempt_at=retry_at,
            error=sanitize_error(error) if error is not None else None,
            transaction=transaction,
        )

    async def manual_recovery(
        self,
        *,
        organization_id: UUID,
        delivery_id: UUID,
        reconcile_first: bool,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
        transaction: object,
    ) -> DeliveryRecord:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=AuthorizationPolicy(required_roles=frozenset({"methodologist"})),
        )
        delivery = await self._required(organization_id, delivery_id, transaction)
        if not reconcile_first:
            raise DeliveryTransitionError("manual recovery requires reconcile_first=true")
        if delivery.state == "unknown_outcome":
            result = await self.begin_reconciliation(
                organization_id=organization_id, delivery_id=delivery_id, transaction=transaction
            )
        else:
            await self._scheduler.schedule(delivery, transaction=transaction)
            result = delivery
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="retry_delivery",
                entity_type="external_delivery",
                entity_id=delivery_id,
                before_revision=delivery.revision,
                after_revision=result.revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"reconcile_first": True},
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return result

    async def supersede(
        self, *, organization_id: UUID, delivery_id: UUID, transaction: object
    ) -> DeliveryRecord:
        delivery = await self._required(organization_id, delivery_id, transaction)
        validate_delivery_transition(
            current_state=delivery.state,
            target_state="superseded",
            attempt_count=delivery.attempt_count,
            max_attempts=delivery.max_attempts,
            reconciliation_observed=delivery.state == "reconciling",
        )
        return await self._repository.transition(
            delivery,
            target_state="superseded",
            next_attempt_at=None,
            error=None,
            transaction=transaction,
        )

    async def _required(
        self, organization_id: UUID, delivery_id: UUID, transaction: object
    ) -> DeliveryRecord:
        delivery = await self._repository.lock(
            organization_id, delivery_id, transaction=transaction
        )
        if delivery is None or delivery.organization_id != organization_id:
            raise DeliveryServiceError("tenant delivery was not found")
        return delivery


__all__ = [
    "DeliveryProvenance",
    "DeliveryRecord",
    "DeliveryRepository",
    "DeliveryScheduler",
    "DeliveryService",
    "DeliveryServiceError",
    "DeliveryState",
    "DeliveryTransitionError",
    "ReconcileOutcome",
    "validate_delivery_transition",
]
