"""Atomic delivery scheduling and schema-backed delivery/reconciliation workers."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import import_module
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import sanitize_shared_value
from review_platform.application.ports.providers import DeliveryProvider, JsonValue
from review_platform.application.services.deliveries import (
    DeliveryAuditDraft,
    DeliveryAuditPort,
    DeliveryRecord,
    DeliveryScheduler,
)
from review_platform.application.services.review_publication import (
    ExternalDeliveryIntent,
    ReviewDeliveryScheduler,
)
from review_platform.domain.delivery_payload import (
    DeliverRequest,
    DeliveryResult,
    ReconcileRequest,
    build_reconcile_request,
    parse_deliver_request,
    parse_delivery_result,
    render_deliver_request,
    render_delivery_error,
    render_delivery_result,
    render_reconcile_request,
)
from review_platform.domain.primitives import (
    canonical_json_sha256,
    require_utc,
    utc_now,
    uuid7,
)
from review_platform.infrastructure.db.models.delivery import (
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.learning import DestinationBinding
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.publication import ExternalDelivery
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.deliveries import (
    ReconciliationObservationDraft,
    SqlDeliveryRepository,
)
from review_platform.infrastructure.db.repositories.operations import (
    AuditEventRepository,
    OutboxMessageRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

from .registry import task_handler

DELIVERY_TASK_RUNTIME_FACTORY_ENV = "REVIEW_PLATFORM_DELIVERY_TASK_RUNTIME_FACTORY"
DELIVERY_EVENT = "ExternalDeliveryRequested"
RECONCILIATION_EVENT = "ExternalDeliveryReconciliationRequested"


class DeliveryTaskError(RuntimeError):
    """A delivery task cannot safely use its durable intent or provider."""


class StaleDeliveryClaim(DeliveryTaskError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryTaskRuntime:
    session_factory: AsyncSessionFactory
    providers: Mapping[str, DeliveryProvider]
    id_factory: Callable[[], UUID] = uuid7
    clock: Callable[[], datetime] = utc_now
    max_attempts: int = 5
    lease_seconds: int = 300
    retry_seconds: int = 60
    audit_factory: Callable[[str], DeliveryAuditPort] | None = None

    def __post_init__(self) -> None:
        if set(self.providers) != {"stepik", "github"}:
            raise ValueError("delivery runtime requires exact stepik and github providers")
        for provider in self.providers.values():
            if (
                provider.contract_version != "1.1.0"
                or provider.schema_name != "delivery.schema.json"
            ):
                raise ValueError("delivery provider must use frozen delivery schema 1.1.0")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("delivery max attempts must be between 1 and 10")
        if not 1 <= self.lease_seconds <= 3600 or self.retry_seconds < 1:
            raise ValueError("delivery lease/retry configuration is invalid")


class SqlDeliveryWorkerAuditRecorder:
    """Persist delivery worker actions inside the state-change transaction."""

    def __init__(
        self,
        *,
        worker_identity: str,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not worker_identity or len(worker_identity) > 255:
            raise ValueError("delivery audit worker identity must contain 1..255 characters")
        self._worker_identity = worker_identity
        self._id_factory = id_factory
        self._clock = clock

    async def record(
        self,
        draft: DeliveryAuditDraft,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        details = sanitize_shared_value(
            {**dict(draft.details), "worker_identity": self._worker_identity}
        )
        await AuditEventRepository(session).append(
            AuditEvent(
                id=self._id_factory(),
                organization_id=draft.organization_id,
                actor_type="worker",
                actor_user_id=None,
                installation_operator_id=None,
                agent_id=None,
                agent_authorization_id=None,
                action=draft.action,
                entity_type="external_delivery",
                entity_id=draft.delivery_id,
                before_revision=draft.before_revision,
                after_revision=draft.after_revision,
                request_id=draft.delivery_id,
                trace_id=self._id_factory(),
                outcome=draft.outcome,
                sanitized_details=dict(details),
                occurred_at=require_utc(self._clock()),
            )
        )


class SqlReviewDeliveryScheduler(ReviewDeliveryScheduler):
    """Materialize delivery + Operation + outbox in the publication transaction."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("delivery outbox max attempts must be between 1 and 10")
        self._id_factory = id_factory
        self._clock = clock
        self._max_attempts = max_attempts

    async def schedule(
        self,
        intent: ExternalDeliveryIntent,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        request = parse_deliver_request(intent.request)
        _validate_intent(intent, request)
        existing = await session.scalar(
            select(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == intent.organization_id,
                ExternalDelivery.id == intent.delivery_id,
            )
            .with_for_update()
        )
        if existing is not None:
            await _validate_scheduled_replay(session, intent, existing)
            return
        if intent.delivery_id == intent.operation_id:
            raise DeliveryTaskError("delivery and Operation identities must be distinct")
        operation = Operation(
            id=intent.operation_id,
            organization_id=intent.organization_id,
            kind="external_delivery",
            input_version=f"delivery:{intent.payload_version}:{intent.payload_digest}",
            state="pending",
            revision=0,
            created_at=intent.requested_at,
            updated_at=intent.requested_at,
            finished_at=None,
            error_code=None,
            sanitized_error=None,
        )
        session.add(operation)
        await session.flush([operation])
        row = ExternalDelivery(
            id=intent.delivery_id,
            organization_id=intent.organization_id,
            publication_id=intent.publication_id,
            operation_id=intent.operation_id,
            delivery_key=intent.delivery_key,
            destination_binding_id=intent.destination.destination_binding_id,
            binding_version=intent.destination.binding_version,
            credential_binding_id=intent.destination.credential_binding_id,
            credential_binding_version=(
                intent.destination.credential_binding_version
            ),
            destination_kind=intent.destination.kind,
            recipient_ref=intent.destination.recipient_ref,
            course_run_id=intent.course_run_id,
            homework_version_id=intent.homework_version_id,
            criterion_set_id=intent.criterion_set_id,
            submission_version_id=intent.submission_version_id,
            artifact_version_id=intent.artifact_version_id,
            artifact_content_digest=intent.artifact_content_digest,
            review_iteration_id=intent.review_iteration_id,
            review_revision_id=intent.review_revision_id,
            contract_version=intent.contract_version,
            publication_fingerprint=intent.publication_fingerprint,
            payload_version=intent.payload_version,
            payload_digest=intent.payload_digest,
            payload=render_deliver_request(request),
            state="pending",
            attempt_count=0,
            next_attempt_at=None,
            external_id=None,
            external_url=None,
            last_error_code=None,
            sanitized_error=None,
            revision=0,
        )
        session.add(row)
        await session.flush([row])
        await _append_outbox(
            session,
            organization_id=intent.organization_id,
            message_id=self._id_factory(),
            delivery_id=intent.delivery_id,
            event_type=DELIVERY_EVENT,
            payload={"request": render_deliver_request(request)},
            available_at=intent.requested_at,
            max_attempts=self._max_attempts,
            id_factory=self._id_factory,
            clock=self._clock,
        )


class SqlManualDeliveryScheduler(DeliveryScheduler):
    """Create one durable manual recovery message without calling a provider."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("manual delivery outbox attempts must be between 1 and 10")
        self._id_factory = id_factory
        self._clock = clock
        self._max_attempts = max_attempts

    async def schedule(
        self,
        delivery: DeliveryRecord,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        row = await session.scalar(
            select(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == delivery.organization_id,
                ExternalDelivery.id == delivery.delivery_id,
            )
            .with_for_update()
        )
        if row is None:
            raise DeliveryTaskError("tenant delivery was not found for manual recovery")
        if (
            row.state != delivery.state
            or row.revision != delivery.revision
            or row.attempt_count != delivery.attempt_count
            or row.course_run_id != delivery.provenance.course_run_id
            or row.review_iteration_id != delivery.provenance.review_iteration_id
            or row.review_revision_id != delivery.provenance.review_revision_id
        ):
            raise DeliveryTaskError("manual delivery snapshot provenance mismatched")
        if row.state in {"succeeded", "superseded", "processing", "reconciling"}:
            raise DeliveryTaskError(
                f"delivery state {row.state!r} cannot schedule manual recovery"
            )
        event_type = await _manual_recovery_event(session, row)
        payload: dict[str, Any] = {
            "request": render_deliver_request(parse_deliver_request(row.payload)),
            "trigger": "manual_recovery",
            "delivery_revision": row.revision,
        }
        messages = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.organization_id == row.organization_id,
                    OutboxMessage.aggregate_type == "external_delivery",
                    OutboxMessage.aggregate_id == row.id,
                    OutboxMessage.event_type == event_type,
                )
            )
        ).all()
        if any(message.payload == payload for message in messages):
            return
        await _append_outbox(
            session,
            organization_id=row.organization_id,
            message_id=self._id_factory(),
            delivery_id=row.id,
            event_type=event_type,
            payload=payload,
            available_at=require_utc(self._clock()),
            max_attempts=self._max_attempts,
            id_factory=self._id_factory,
            clock=self._clock,
        )


async def _manual_recovery_event(
    session: AsyncSession,
    delivery: ExternalDelivery,
) -> str:
    if delivery.state in {"unknown_outcome", "action_required"}:
        return RECONCILIATION_EVENT
    if delivery.state == "pending":
        return DELIVERY_EVENT
    if delivery.state != "retryable_failed":
        raise DeliveryTaskError(f"delivery state {delivery.state!r} is not recoverable")
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.organization_id == delivery.organization_id,
            DeliveryAttempt.delivery_id == delivery.id,
            DeliveryAttempt.attempt_number == delivery.attempt_count,
        )
        .order_by(DeliveryAttempt.id.desc())
    )
    if attempt is None:
        return RECONCILIATION_EVENT
    observed_not_found = await session.scalar(
        select(DeliveryReconciliationObservation.id)
        .where(
            DeliveryReconciliationObservation.organization_id
            == delivery.organization_id,
            DeliveryReconciliationObservation.delivery_id == delivery.id,
            DeliveryReconciliationObservation.delivery_attempt_id == attempt.id,
            DeliveryReconciliationObservation.outcome == "not_found",
        )
        .limit(1)
    )
    return DELIVERY_EVENT if observed_not_found is not None else RECONCILIATION_EVENT


@dataclass(frozen=True, slots=True)
class _WorkerClaim:
    organization_id: UUID
    message_id: UUID
    delivery_id: UUID
    operation_id: UUID
    delivery_attempt_id: UUID
    operation_attempt_id: UUID
    attempt_number: int
    claim_token: UUID
    delivery_key: str
    provider_kind: str
    credential_binding_id: UUID
    credential_binding_version: int
    request: DeliverRequest


class DeliveryTaskHandler:
    def __init__(self, runtime: DeliveryTaskRuntime, *, worker_identity: str) -> None:
        self._runtime = runtime
        self._worker_identity = _worker_identity(worker_identity)

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with session_scope(self._runtime.session_factory) as session:
            terminal = await _terminal_message_result(
                session,
                organization_uuid,
                message_uuid,
                expected_event=DELIVERY_EVENT,
            )
            if terminal is not None:
                return terminal
            claim = await _claim_delivery(
                session,
                organization_id=organization_uuid,
                message_id=message_uuid,
                worker_identity=self._worker_identity,
                runtime=self._runtime,
            )
        provider = self._runtime.providers[claim.provider_kind]
        result = await _deliver(provider, claim.request)
        async with session_scope(self._runtime.session_factory) as session:
            stored = await _finish_delivery(
                session,
                claim,
                result,
                runtime=self._runtime,
            )
        return _result_payload(claim, stored.state, result, replayed=False)


class DeliveryReconciliationTaskHandler:
    def __init__(self, runtime: DeliveryTaskRuntime, *, worker_identity: str) -> None:
        self._runtime = runtime
        self._worker_identity = _worker_identity(worker_identity)

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with session_scope(self._runtime.session_factory) as session:
            terminal = await _terminal_message_result(
                session,
                organization_uuid,
                message_uuid,
                expected_event=RECONCILIATION_EVENT,
            )
            if terminal is not None:
                return terminal
            claim, request = await _begin_reconciliation(
                session,
                organization_id=organization_uuid,
                message_id=message_uuid,
                worker_identity=self._worker_identity,
                runtime=self._runtime,
            )
        provider = self._runtime.providers[claim.provider_kind]
        result = await _reconcile(provider, request, claim=claim)
        async with session_scope(self._runtime.session_factory) as session:
            state = await _finish_reconciliation(
                session,
                claim,
                request,
                result,
                runtime=self._runtime,
            )
        return _result_payload(claim, state, result, replayed=False)


async def _claim_delivery(
    session: AsyncSession,
    *,
    organization_id: UUID,
    message_id: UUID,
    worker_identity: str,
    runtime: DeliveryTaskRuntime,
) -> _WorkerClaim:
    message, delivery, request = await _load_message_delivery(
        session,
        organization_id,
        message_id,
        expected_event=DELIVERY_EVENT,
    )
    if delivery.state in {"unknown_outcome", "reconciling"}:
        raise DeliveryTaskError("unknown delivery outcome requires reconciliation")
    if delivery.state not in {"pending", "retryable_failed"}:
        raise DeliveryTaskError(f"delivery state {delivery.state!r} is not claimable")
    await _require_exact_active_credential(session, delivery)
    repository = _repository(runtime)
    record = await repository.lock(organization_id, delivery.id, transaction=session)
    if record is None:
        raise DeliveryTaskError("tenant delivery disappeared during claim")
    processing = await repository.transition(
        record,
        target_state="processing",
        next_attempt_at=None,
        error=None,
        transaction=session,
    )
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.organization_id == organization_id,
            DeliveryAttempt.delivery_id == delivery.id,
            DeliveryAttempt.attempt_number == processing.attempt_count,
            DeliveryAttempt.state == "processing",
        )
        .with_for_update()
    )
    if attempt is None:
        raise DeliveryTaskError("delivery claim did not create a processing attempt")
    stable_identity = _logical_worker_key(
        worker_identity,
        delivery.delivery_key,
        attempt.attempt_number,
        delivery.credential_binding_id,
        delivery.credential_binding_version,
    )
    attempt.worker_identity = stable_identity
    operation_attempt = OperationAttempt(
        id=runtime.id_factory(),
        organization_id=organization_id,
        operation_id=delivery.operation_id,
        attempt_number=await _next_operation_attempt(
            session,
            organization_id,
            delivery.operation_id,
        ),
        worker_identity=stable_identity,
        started_at=require_utc(runtime.clock()),
        finished_at=None,
        outcome="processing",
        error_code=None,
        sanitized_error=None,
    )
    session.add(operation_attempt)
    operation = await session.get(Operation, delivery.operation_id)
    if operation is None:
        raise DeliveryTaskError("delivery Operation is missing")
    operation.state = "processing"
    operation.finished_at = None
    operation.updated_at = require_utc(runtime.clock())
    operation.revision += 1
    await _delivery_audit(runtime, stable_identity).record(
        DeliveryAuditDraft(
            organization_id=organization_id,
            action="claim_delivery",
            delivery_id=delivery.id,
            before_revision=record.revision,
            after_revision=processing.revision,
            outcome="succeeded",
            details={"attempt_number": attempt.attempt_number},
        ),
        transaction=session,
    )
    await session.flush()
    return _WorkerClaim(
        organization_id,
        message.message_id,
        delivery.id,
        delivery.operation_id,
        attempt.id,
        operation_attempt.id,
        attempt.attempt_number,
        attempt.claim_token,
        delivery.delivery_key,
        delivery.destination_kind,
        delivery.credential_binding_id,
        delivery.credential_binding_version,
        request,
    )


async def _finish_delivery(
    session: AsyncSession,
    claim: _WorkerClaim,
    result: DeliveryResult,
    *,
    runtime: DeliveryTaskRuntime,
) -> ExternalDelivery:
    delivery, attempt, operation_attempt, operation = await _lock_claim_rows(
        session,
        claim,
    )
    await _require_exact_active_credential(session, delivery)
    target = result.outcome
    error = result.error.model_dump(mode="json") if result.error is not None else None
    if target == "not_found":
        target = "retryable_failed"
    if target == "retryable_failed" and delivery.attempt_count >= runtime.max_attempts:
        target = "action_required"
        error = {
            "code": "delivery_attempts_exhausted",
            "message": "Delivery exhausted its retry budget",
            "retryable": False,
            "action": "inspect_delivery",
        }
    retry_at = (
        require_utc(runtime.clock()) + timedelta(seconds=runtime.retry_seconds)
        if target == "retryable_failed"
        else None
    )
    record = await _repository(runtime).lock(
        claim.organization_id,
        claim.delivery_id,
        transaction=session,
    )
    if record is None:
        raise StaleDeliveryClaim("delivery claim disappeared")
    transitioned = await _repository(runtime).transition(
        record,
        target_state=cast(Any, target),
        next_attempt_at=retry_at,
        error=cast(Mapping[str, object] | None, error),
        transaction=session,
    )
    if result.outcome == "succeeded":
        delivery.external_id = result.external_id
        delivery.external_url = (
            str(result.external_url) if result.external_url is not None else None
        )
    await _finish_operation_attempt(
        operation_attempt,
        operation,
        state=str(target),
        error=cast(Mapping[str, object] | None, error),
        now=require_utc(runtime.clock()),
    )
    if target == "unknown_outcome":
        await _schedule_followup(
            session,
            claim,
            event_type=RECONCILIATION_EVENT,
            available_at=require_utc(runtime.clock()),
            runtime=runtime,
        )
    elif target == "retryable_failed":
        assert retry_at is not None
        await _schedule_followup(
            session,
            claim,
            event_type=DELIVERY_EVENT,
            available_at=retry_at,
            runtime=runtime,
        )
    await _delivery_audit(runtime, attempt.worker_identity).record(
        DeliveryAuditDraft(
            organization_id=claim.organization_id,
            action="record_delivery_result",
            delivery_id=claim.delivery_id,
            before_revision=record.revision,
            after_revision=transitioned.revision,
            outcome="succeeded",
            details={"delivery_outcome": str(target), "error": error},
        ),
        transaction=session,
    )
    await session.flush()
    return delivery


async def _begin_reconciliation(
    session: AsyncSession,
    *,
    organization_id: UUID,
    message_id: UUID,
    worker_identity: str,
    runtime: DeliveryTaskRuntime,
) -> tuple[_WorkerClaim, ReconcileRequest]:
    message, delivery, deliver_request = await _load_message_delivery(
        session,
        organization_id,
        message_id,
        expected_event=RECONCILIATION_EVENT,
    )
    if delivery.state != "unknown_outcome":
        raise DeliveryTaskError("reconciliation requires unknown_outcome state")
    await _require_exact_active_credential(session, delivery)
    record = await _repository(runtime).lock(
        organization_id,
        delivery.id,
        transaction=session,
    )
    if record is None:
        raise DeliveryTaskError("tenant delivery disappeared before reconciliation")
    await _repository(runtime).transition(
        record,
        target_state="reconciling",
        next_attempt_at=None,
        error=None,
        transaction=session,
    )
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.organization_id == organization_id,
            DeliveryAttempt.delivery_id == delivery.id,
            DeliveryAttempt.attempt_number == delivery.attempt_count,
        )
        .with_for_update()
    )
    if attempt is None:
        raise DeliveryTaskError("unknown outcome has no exact delivery attempt")
    stable_identity = _logical_worker_key(
        f"{worker_identity}:reconcile",
        delivery.delivery_key,
        attempt.attempt_number,
        delivery.credential_binding_id,
        delivery.credential_binding_version,
    )
    operation_attempt = OperationAttempt(
        id=runtime.id_factory(),
        organization_id=organization_id,
        operation_id=delivery.operation_id,
        attempt_number=await _next_operation_attempt(
            session,
            organization_id,
            delivery.operation_id,
        ),
        worker_identity=stable_identity,
        started_at=require_utc(runtime.clock()),
        finished_at=None,
        outcome="processing",
        error_code=None,
        sanitized_error=None,
    )
    session.add(operation_attempt)
    operation = await session.get(Operation, delivery.operation_id)
    if operation is None:
        raise DeliveryTaskError("reconciliation Operation is missing")
    operation.state = "reconciling"
    operation.updated_at = require_utc(runtime.clock())
    operation.finished_at = None
    operation.revision += 1
    request = build_reconcile_request(deliver_request)
    await session.flush()
    return (
        _WorkerClaim(
            organization_id,
            message.message_id,
            delivery.id,
            delivery.operation_id,
            attempt.id,
            operation_attempt.id,
            attempt.attempt_number,
            attempt.claim_token,
            delivery.delivery_key,
            delivery.destination_kind,
            delivery.credential_binding_id,
            delivery.credential_binding_version,
            deliver_request,
        ),
        request,
    )


async def _finish_reconciliation(
    session: AsyncSession,
    claim: _WorkerClaim,
    request: ReconcileRequest,
    result: DeliveryResult,
    *,
    runtime: DeliveryTaskRuntime,
) -> str:
    delivery, attempt, operation_attempt, operation = await _lock_claim_rows(
        session,
        claim,
        expected_delivery_state="reconciling",
        expected_attempt_state="unknown_outcome",
    )
    await _require_exact_active_credential(session, delivery)
    rendered_result = render_delivery_result(result)
    error = result.error.model_dump(mode="json") if result.error is not None else None
    await _repository(runtime).append_reconciliation_observation(
        ReconciliationObservationDraft(
            observation_id=runtime.id_factory(),
            organization_id=claim.organization_id,
            delivery_id=claim.delivery_id,
            delivery_attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            operation_id=claim.operation_id,
            credential_binding_id=claim.credential_binding_id,
            credential_binding_version=claim.credential_binding_version,
            request_payload_version="1.1.0",
            request_digest=canonical_json_sha256(render_reconcile_request(request)),
            result_payload_version="1.1.0",
            result_digest=canonical_json_sha256(rendered_result),
            observed_at=require_utc(runtime.clock()),
            outcome=result.outcome,
            external_id=result.external_id,
            external_url=(str(result.external_url) if result.external_url is not None else None),
            error=cast(Mapping[str, object] | None, error),
        ),
        transaction=session,
    )
    state_map = {
        "succeeded": "succeeded",
        "not_found": "retryable_failed",
        "action_required": "action_required",
        "unknown_outcome": "unknown_outcome",
        "retryable_failed": "retryable_failed",
    }
    target = state_map[result.outcome]
    retry_at = (
        require_utc(runtime.clock()) + timedelta(seconds=runtime.retry_seconds)
        if target == "retryable_failed"
        else None
    )
    before_revision = delivery.revision
    await _transition_reconciled_delivery(
        session,
        delivery,
        target_state=target,
        next_attempt_at=retry_at,
        error=cast(Mapping[str, object] | None, error),
        now=require_utc(runtime.clock()),
    )
    if result.outcome == "succeeded":
        delivery.external_id = result.external_id
        delivery.external_url = (
            str(result.external_url) if result.external_url is not None else None
        )
    await _finish_operation_attempt(
        operation_attempt,
        operation,
        state=target,
        error=cast(Mapping[str, object] | None, error),
        now=require_utc(runtime.clock()),
    )
    if target == "retryable_failed":
        assert retry_at is not None
        event_type = DELIVERY_EVENT if result.outcome == "not_found" else RECONCILIATION_EVENT
        await _schedule_followup(
            session,
            claim,
            event_type=event_type,
            available_at=retry_at,
            runtime=runtime,
        )
    elif target == "unknown_outcome":
        await _schedule_followup(
            session,
            claim,
            event_type=RECONCILIATION_EVENT,
            available_at=require_utc(runtime.clock()) + timedelta(seconds=runtime.retry_seconds),
            runtime=runtime,
        )
    await _delivery_audit(runtime, operation_attempt.worker_identity).record(
        DeliveryAuditDraft(
            organization_id=claim.organization_id,
            action="reconcile_delivery",
            delivery_id=claim.delivery_id,
            before_revision=before_revision,
            after_revision=delivery.revision,
            outcome="succeeded",
            details={"reconciliation_outcome": result.outcome, "error": error},
        ),
        transaction=session,
    )
    await session.flush()
    return target


async def _load_message_delivery(
    session: AsyncSession,
    organization_id: UUID,
    message_id: UUID,
    *,
    expected_event: str,
) -> tuple[OutboxMessage, ExternalDelivery, DeliverRequest]:
    message = await OutboxMessageRepository(session).get(organization_id, message_id)
    if message is None:
        raise DeliveryTaskError("tenant delivery outbox message was not found")
    if (
        message.event_type != expected_event
        or message.payload_version != "1.1.0"
        or message.aggregate_type != "external_delivery"
    ):
        raise DeliveryTaskError("outbox message is not the expected delivery event")
    delivery = await session.scalar(
        select(ExternalDelivery).where(
            ExternalDelivery.organization_id == organization_id,
            ExternalDelivery.id == message.aggregate_id,
        )
    )
    if delivery is None:
        raise DeliveryTaskError("tenant ExternalDelivery was not found")
    raw_request = message.payload.get("request")
    if not isinstance(raw_request, Mapping):
        raw_request = delivery.payload
    request = parse_deliver_request(cast(Mapping[str, Any], raw_request))
    _validate_row_request(delivery, request)
    return message, delivery, request


async def _terminal_message_result(
    session: AsyncSession,
    organization_id: UUID,
    message_id: UUID,
    *,
    expected_event: str,
) -> dict[str, Any] | None:
    message, delivery, request = await _load_message_delivery(
        session,
        organization_id,
        message_id,
        expected_event=expected_event,
    )
    del request
    if delivery.state not in {"succeeded", "action_required", "superseded"}:
        return None
    return {
        "organization_id": str(organization_id),
        "message_id": str(message.message_id),
        "delivery_id": str(delivery.id),
        "state": delivery.state,
        "replayed": True,
        "external_id": delivery.external_id,
        "external_url": delivery.external_url,
    }


async def _lock_claim_rows(
    session: AsyncSession,
    claim: _WorkerClaim,
    *,
    expected_delivery_state: str = "processing",
    expected_attempt_state: str = "processing",
) -> tuple[ExternalDelivery, DeliveryAttempt, OperationAttempt, Operation]:
    delivery = await session.scalar(
        select(ExternalDelivery)
        .where(
            ExternalDelivery.organization_id == claim.organization_id,
            ExternalDelivery.id == claim.delivery_id,
            ExternalDelivery.operation_id == claim.operation_id,
            ExternalDelivery.state == expected_delivery_state,
            ExternalDelivery.attempt_count == claim.attempt_number,
        )
        .with_for_update()
    )
    attempt = await session.scalar(
        select(DeliveryAttempt)
        .where(
            DeliveryAttempt.organization_id == claim.organization_id,
            DeliveryAttempt.id == claim.delivery_attempt_id,
            DeliveryAttempt.delivery_id == claim.delivery_id,
            DeliveryAttempt.attempt_number == claim.attempt_number,
            DeliveryAttempt.claim_token == claim.claim_token,
            DeliveryAttempt.state == expected_attempt_state,
        )
        .with_for_update()
    )
    operation_attempt = await session.scalar(
        select(OperationAttempt)
        .where(
            OperationAttempt.organization_id == claim.organization_id,
            OperationAttempt.id == claim.operation_attempt_id,
            OperationAttempt.operation_id == claim.operation_id,
            OperationAttempt.outcome == "processing",
        )
        .with_for_update()
    )
    operation = await session.scalar(
        select(Operation)
        .where(
            Operation.organization_id == claim.organization_id,
            Operation.id == claim.operation_id,
        )
        .with_for_update()
    )
    if delivery is None or attempt is None or operation_attempt is None or operation is None:
        raise StaleDeliveryClaim("delivery worker claim token or attempt is stale")
    return delivery, attempt, operation_attempt, operation


async def _require_exact_active_credential(
    session: AsyncSession,
    delivery: ExternalDelivery,
) -> None:
    credential = await session.scalar(
        select(ExternalCredential).where(
            ExternalCredential.organization_id == delivery.organization_id,
            ExternalCredential.id == delivery.credential_binding_id,
            ExternalCredential.binding_version == delivery.credential_binding_version,
            ExternalCredential.provider == delivery.destination_kind,
            ExternalCredential.status == "active",
        )
    )
    binding = await session.scalar(
        select(DestinationBinding).where(
            DestinationBinding.organization_id == delivery.organization_id,
            DestinationBinding.id == delivery.destination_binding_id,
            DestinationBinding.binding_version == delivery.binding_version,
            DestinationBinding.credential_id == delivery.credential_binding_id,
            DestinationBinding.credential_binding_version
            == delivery.credential_binding_version,
        )
    )
    if credential is None or binding is None:
        raise DeliveryTaskError("exact active delivery credential/binding was not found")


async def _deliver(provider: DeliveryProvider, request: DeliverRequest) -> DeliveryResult:
    try:
        result = await provider.deliver(
            cast(Mapping[str, JsonValue], render_deliver_request(request))
        )
        parsed = parse_delivery_result(result)
        if (
            parsed.organization_id != request.organization_id
            or parsed.delivery_id != request.delivery_id
        ):
            raise DeliveryTaskError("delivery provider result provenance mismatched")
        return parsed
    except TimeoutError as error:
        return parse_delivery_result(
            {
                "contract_version": "1.1.0",
                "organization_id": request.organization_id,
                "delivery_id": request.delivery_id,
                "outcome": "unknown_outcome",
                "external_id": None,
                "external_url": None,
                "error": render_delivery_error(error, retryable=True),
            }
        )
    except DeliveryTaskError:
        raise
    except Exception as error:
        return parse_delivery_result(
            {
                "contract_version": "1.1.0",
                "organization_id": request.organization_id,
                "delivery_id": request.delivery_id,
                "outcome": "retryable_failed",
                "external_id": None,
                "external_url": None,
                "error": render_delivery_error(error, retryable=True),
            }
        )


async def _reconcile(
    provider: DeliveryProvider,
    request: ReconcileRequest,
    *,
    claim: _WorkerClaim,
) -> DeliveryResult:
    try:
        result = await provider.reconcile(
            cast(Mapping[str, JsonValue], render_reconcile_request(request))
        )
        parsed = parse_delivery_result(result)
        if (
            parsed.organization_id != claim.organization_id
            or parsed.delivery_id != claim.delivery_id
        ):
            raise DeliveryTaskError("reconciliation result delivery provenance mismatched")
        return parsed
    except TimeoutError as error:
        return parse_delivery_result(
            {
                "contract_version": "1.1.0",
                "organization_id": claim.organization_id,
                "delivery_id": claim.delivery_id,
                "outcome": "unknown_outcome",
                "external_id": None,
                "external_url": None,
                "error": render_delivery_error(error, retryable=True),
            }
        )
    except DeliveryTaskError:
        raise
    except Exception as error:
        return parse_delivery_result(
            {
                "contract_version": "1.1.0",
                "organization_id": claim.organization_id,
                "delivery_id": claim.delivery_id,
                "outcome": "retryable_failed",
                "external_id": None,
                "external_url": None,
                "error": render_delivery_error(error, retryable=True),
            }
        )


async def _finish_operation_attempt(
    attempt: OperationAttempt,
    operation: Operation,
    *,
    state: str,
    error: Mapping[str, object] | None,
    now: datetime,
) -> None:
    rendered_error = render_delivery_error(error) if error is not None else None
    operation_state = state
    attempt_outcome = state
    if state == "unknown_outcome":
        operation_state = "unknown_outcome"
    elif state == "reconciling":
        operation_state = "reconciling"
        attempt_outcome = "processing"
    attempt.outcome = attempt_outcome
    attempt.finished_at = now
    attempt.error_code = str(rendered_error["code"]) if rendered_error is not None else None
    attempt.sanitized_error = dict(rendered_error) if rendered_error is not None else None
    operation.state = operation_state
    operation.updated_at = now
    operation.finished_at = now if state in {"succeeded", "action_required"} else None
    operation.error_code = attempt.error_code
    operation.sanitized_error = attempt.sanitized_error
    operation.revision += 1


async def _transition_reconciled_delivery(
    session: AsyncSession,
    delivery: ExternalDelivery,
    *,
    target_state: str,
    next_attempt_at: datetime | None,
    error: Mapping[str, object] | None,
    now: datetime,
) -> None:
    if target_state not in {
        "succeeded",
        "retryable_failed",
        "unknown_outcome",
        "action_required",
    }:
        raise DeliveryTaskError("unsupported reconciliation target state")
    rendered_error = render_delivery_error(error) if error is not None else None
    result = await session.execute(
        update(ExternalDelivery)
        .where(
            ExternalDelivery.organization_id == delivery.organization_id,
            ExternalDelivery.id == delivery.id,
            ExternalDelivery.revision == delivery.revision,
            ExternalDelivery.state == "reconciling",
            ExternalDelivery.attempt_count == delivery.attempt_count,
        )
        .values(
            state=target_state,
            next_attempt_at=next_attempt_at,
            last_error_code=(
                str(rendered_error["code"]) if rendered_error is not None else None
            ),
            sanitized_error=(
                dict(rendered_error) if rendered_error is not None else None
            ),
            revision=delivery.revision + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        raise StaleDeliveryClaim("reconciliation delivery CAS lost")
    await session.refresh(delivery)


async def _schedule_followup(
    session: AsyncSession,
    claim: _WorkerClaim,
    *,
    event_type: str,
    available_at: datetime,
    runtime: DeliveryTaskRuntime,
) -> None:
    await _append_outbox(
        session,
        organization_id=claim.organization_id,
        message_id=runtime.id_factory(),
        delivery_id=claim.delivery_id,
        event_type=event_type,
        payload={"request": render_deliver_request(claim.request)},
        available_at=available_at,
        max_attempts=runtime.max_attempts,
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


async def _append_outbox(
    session: AsyncSession,
    *,
    organization_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
    event_type: str,
    payload: Mapping[str, Any],
    available_at: datetime,
    max_attempts: int,
    id_factory: Callable[[], UUID],
    clock: Callable[[], datetime],
) -> None:
    await OutboxService(
        OutboxMessageRepository(session),
        token_factory=id_factory,
        clock=clock,
    ).create(
        OutboxDraft(
            organization_id=organization_id,
            message_id=message_id,
            aggregate_type="external_delivery",
            aggregate_id=delivery_id,
            event_type=event_type,
            payload_version="1.1.0",
            payload=payload,
            available_at=available_at,
            max_attempts=max_attempts,
        )
    )


async def _next_operation_attempt(
    session: AsyncSession,
    organization_id: UUID,
    operation_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.max(OperationAttempt.attempt_number)).where(
            OperationAttempt.organization_id == organization_id,
            OperationAttempt.operation_id == operation_id,
        )
    )
    return int(value or 0) + 1


def _repository(runtime: DeliveryTaskRuntime) -> SqlDeliveryRepository:
    return SqlDeliveryRepository(
        id_factory=runtime.id_factory,
        clock=runtime.clock,
        max_attempts=runtime.max_attempts,
        default_lease=timedelta(seconds=runtime.lease_seconds),
    )


def _validate_intent(intent: ExternalDeliveryIntent, request: DeliverRequest) -> None:
    destination = intent.destination
    if (
        intent.organization_id != request.organization_id
        or intent.delivery_id != request.delivery_id
        or intent.delivery_key != request.delivery_key
        or intent.contract_version != "1.1.0"
        or intent.payload_version != "1.1.0"
        or intent.destination.destination_binding_id != request.destination.binding_id
        or destination.binding_version != request.destination.binding_version
        or destination.credential_binding_id
        != request.destination.credential_binding_id
        or destination.credential_binding_version
        != request.destination.credential_binding_version
        or destination.kind != request.destination.kind
        or destination.recipient_ref != request.destination.recipient_ref
        or destination.organization_id != intent.organization_id
        or destination.course_run_id != intent.course_run_id
        or not destination.required
        or destination.status != "active"
        or intent.payload_digest != request.payload_digest
        or intent.publication_fingerprint != request.publication_fingerprint
        or intent.course_run_id != request.provenance.course_run_id
        or intent.homework_version_id != request.provenance.homework_version_id
        or intent.criterion_set_id != request.provenance.criterion_set_id
        or intent.submission_version_id != request.provenance.submission_version_id
        or intent.artifact_version_id != request.provenance.artifact_version_id
        or intent.artifact_content_digest
        != request.provenance.artifact_content_digest
        or intent.review_iteration_id != request.provenance.review_iteration_id
        or intent.review_revision_id != request.provenance.review_revision_id
        or intent.contract_version != request.provenance.contract_version
    ):
        raise DeliveryTaskError("ExternalDeliveryIntent violates frozen request provenance")


def _validate_row_request(row: ExternalDelivery, request: DeliverRequest) -> None:
    if (
        row.organization_id != request.organization_id
        or row.id != request.delivery_id
        or row.delivery_key != request.delivery_key
        or row.destination_binding_id != request.destination.binding_id
        or row.binding_version != request.destination.binding_version
        or row.credential_binding_id != request.destination.credential_binding_id
        or row.credential_binding_version
        != request.destination.credential_binding_version
        or row.destination_kind != request.destination.kind
        or row.recipient_ref != request.destination.recipient_ref
        or row.payload_digest != request.payload_digest
        or row.publication_fingerprint != request.publication_fingerprint
    ):
        raise DeliveryTaskError("ExternalDelivery row/request provenance mismatched")


async def _validate_scheduled_replay(
    session: AsyncSession,
    intent: ExternalDeliveryIntent,
    row: ExternalDelivery,
) -> None:
    request = parse_deliver_request(row.payload)
    _validate_intent(intent, request)
    _validate_row_request(row, request)
    if row.operation_id != intent.operation_id or row.publication_id != intent.publication_id:
        raise DeliveryTaskError("delivery replay operation/publication provenance mismatched")
    operation = await session.scalar(
        select(Operation).where(
            Operation.organization_id == intent.organization_id,
            Operation.id == intent.operation_id,
            Operation.kind == "external_delivery",
        )
    )
    messages = (
        await session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.organization_id == intent.organization_id,
                OutboxMessage.aggregate_id == intent.delivery_id,
                OutboxMessage.event_type == DELIVERY_EVENT,
            )
        )
    ).all()
    matching_messages = [
        message
        for message in messages
        if message.payload.get("request") == render_deliver_request(request)
    ]
    if operation is None or not matching_messages:
        raise DeliveryTaskError("delivery replay lacks exact Operation/outbox")


def _worker_identity(value: str) -> str:
    if not value or len(value) > 100:
        raise ValueError("delivery worker identity must contain 1..100 characters")
    return value


def _logical_worker_key(
    worker: str,
    delivery_key: str,
    attempt_number: int,
    credential_id: UUID,
    credential_version: int,
) -> str:
    value = (
        f"{worker}|{delivery_key}|attempt={attempt_number}|"
        f"credential={credential_id}@{credential_version}"
    )
    if len(value) > 255:
        raise DeliveryTaskError("delivery logical worker key exceeds storage bound")
    return value


def _result_payload(
    claim: _WorkerClaim,
    state: str,
    result: DeliveryResult,
    *,
    replayed: bool,
) -> dict[str, Any]:
    return {
        "organization_id": str(claim.organization_id),
        "message_id": str(claim.message_id),
        "delivery_id": str(claim.delivery_id),
        "operation_id": str(claim.operation_id),
        "state": state,
        "replayed": replayed,
        "external_id": result.external_id,
        "external_url": str(result.external_url) if result.external_url is not None else None,
        "error": result.error.model_dump(mode="json") if result.error is not None else None,
    }


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise DeliveryTaskError(f"delivery task {field} must be a UUID") from error


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise DeliveryTaskError("delivery task requires caller-owned AsyncSession")
    return transaction


def _delivery_audit(runtime: DeliveryTaskRuntime, worker_identity: str) -> DeliveryAuditPort:
    if runtime.audit_factory is not None:
        return runtime.audit_factory(worker_identity)
    return SqlDeliveryWorkerAuditRecorder(
        worker_identity=worker_identity,
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


def load_delivery_task_runtime() -> DeliveryTaskRuntime:
    specification = os.environ.get(DELIVERY_TASK_RUNTIME_FACTORY_ENV)
    if not specification:
        raise DeliveryTaskError(f"{DELIVERY_TASK_RUNTIME_FACTORY_ENV} is required")
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise DeliveryTaskError(
            f"{DELIVERY_TASK_RUNTIME_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise DeliveryTaskError("delivery task runtime factory is not callable")
    runtime = factory()
    if not isinstance(runtime, DeliveryTaskRuntime):
        raise DeliveryTaskError("delivery factory did not return DeliveryTaskRuntime")
    return runtime


def validate_delivery_task_configuration() -> None:
    """Fail worker startup before binding tasks when runtime DI is incomplete."""

    load_delivery_task_runtime()


@task_handler(
    name="review_platform.external_delivery",
    kind="external_delivery",
    event_type=DELIVERY_EVENT,
    requires_auth_revalidation=False,
    startup_validator=validate_delivery_task_configuration,
)
async def handle_external_delivery(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    return await DeliveryTaskHandler(
        load_delivery_task_runtime(),
        worker_identity="taskiq-external-delivery",
    )(organization_id=organization_id, message_id=message_id)


@task_handler(
    name="review_platform.external_delivery_reconciliation",
    kind="external_delivery",
    event_type=RECONCILIATION_EVENT,
    requires_auth_revalidation=False,
    startup_validator=validate_delivery_task_configuration,
)
async def handle_external_delivery_reconciliation(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    return await DeliveryReconciliationTaskHandler(
        load_delivery_task_runtime(),
        worker_identity="taskiq-external-delivery",
    )(organization_id=organization_id, message_id=message_id)


_scheduler_protocol: ReviewDeliveryScheduler = SqlReviewDeliveryScheduler()
_manual_scheduler_protocol: DeliveryScheduler = SqlManualDeliveryScheduler()
_delivery_audit_protocol: DeliveryAuditPort = SqlDeliveryWorkerAuditRecorder(
    worker_identity="delivery-worker-protocol-check"
)


__all__ = [
    "DELIVERY_EVENT",
    "DELIVERY_TASK_RUNTIME_FACTORY_ENV",
    "RECONCILIATION_EVENT",
    "DeliveryReconciliationTaskHandler",
    "DeliveryTaskError",
    "DeliveryTaskHandler",
    "DeliveryTaskRuntime",
    "SqlManualDeliveryScheduler",
    "SqlReviewDeliveryScheduler",
    "StaleDeliveryClaim",
    "handle_external_delivery",
    "handle_external_delivery_reconciliation",
    "load_delivery_task_runtime",
    "validate_delivery_task_configuration",
]
