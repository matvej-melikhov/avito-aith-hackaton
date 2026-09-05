"""Tenant delivery visibility and explicit human recovery routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import AuthorizationError, Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyCoordinator, IdempotencyError
from review_platform.application.request_context import RequestActor
from review_platform.application.services.deliveries import (
    DeliveryScheduler,
    DeliveryService,
    DeliveryServiceError,
)
from review_platform.contracts.commands import RetryDeliveryPayload, WireCommand
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.publication import ExternalDelivery
from review_platform.infrastructure.db.repositories.deliveries import SqlDeliveryRepository
from review_platform.infrastructure.db.repositories.operations import CommandReceiptRepository
from review_platform.infrastructure.tasks.deliveries import SqlManualDeliveryScheduler

router = APIRouter(tags=["deliveries"])


class DeliveryRouteError(RuntimeError):
    pass


@router.get("/v1/deliveries", operation_id="listDeliveries")
async def list_deliveries(request: Request, state: str | None = Query(default=None)) -> Response:
    try:
        runtime, actor = _context(request)
        if actor.actor_type != "user" or "methodologist" not in actor.roles:
            raise AuthorizationError("interactive methodologist is required")
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            statement = select(ExternalDelivery).where(
                ExternalDelivery.organization_id == actor.organization_id
            )
            if state is not None:
                statement = statement.where(ExternalDelivery.state == state)
            rows = (await transaction.scalars(statement.order_by(ExternalDelivery.id))).all()
        return JSONResponse({"items": [_summary(item) for item in rows]})
    except _ERRORS as error:
        return _error(error)


@router.post("/v1/deliveries/{deliveryId}/retry", operation_id="retryDelivery", status_code=202)
async def retry_delivery(deliveryId: UUID, request: Request, body: Mapping[str, Any]) -> Response:
    try:
        runtime, actor = _context(request)
        if actor.actor_type != "user" or "methodologist" not in actor.roles:
            raise AuthorizationError("interactive methodologist is required")
        command = WireCommand.model_validate(dict(body))
        if command.command_name != "retry_delivery" or command.target_id != deliveryId:
            raise DeliveryRouteError("route command or path target mismatched")
        payload = cast(RetryDeliveryPayload, command.payload)
        scheduler = getattr(request.app.state, "manual_delivery_scheduler", None)
        if scheduler is None:
            scheduler = SqlManualDeliveryScheduler(
                id_factory=runtime.id_factory,
                clock=runtime.clock,
                max_attempts=runtime.settings.provider_max_attempts,
            )
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            replay_operation_id = await _reserve(runtime, transaction, command, actor)
            repository = SqlDeliveryRepository(
                id_factory=runtime.id_factory,
                clock=runtime.clock,
                max_attempts=runtime.settings.provider_max_attempts,
            )
            delivery = await repository.lock(
                actor.organization_id, deliveryId, transaction=transaction
            )
            if delivery is None:
                raise DeliveryRouteError("tenant delivery was not found")
            operation_id = await transaction.scalar(
                select(ExternalDelivery.operation_id).where(
                    ExternalDelivery.organization_id == actor.organization_id,
                    ExternalDelivery.id == deliveryId,
                )
            )
            if operation_id is None:
                raise DeliveryRouteError("delivery Operation identity is missing")
            if replay_operation_id is None and delivery.state == "unknown_outcome":
                await cast(DeliveryScheduler, scheduler).schedule(delivery, transaction=transaction)
            if replay_operation_id is None:
                recovery_result = await DeliveryService(
                    repository=repository,
                    scheduler=cast(DeliveryScheduler, scheduler),
                    authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
                    audit=AuditRecorder(
                        SqlAppendOnlyAuditRepository(),
                        event_id_factory=runtime.id_factory,
                        clock=runtime.clock,
                    ),
                    clock=runtime.clock,
                ).manual_recovery(
                    organization_id=actor.organization_id,
                    delivery_id=deliveryId,
                    reconcile_first=payload.reconcile_first,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                    transaction=transaction,
                )
                if delivery.state == "action_required":
                    # Explicit human recovery re-opens the otherwise terminal
                    # state only into unknown_outcome; the already scheduled
                    # reconciliation must resolve it before any delivery retry.
                    await repository.transition(
                        recovery_result,
                        target_state="unknown_outcome",
                        next_attempt_at=None,
                        error=None,
                        transaction=transaction,
                    )
                await _complete(
                    transaction,
                    actor.organization_id,
                    command,
                    operation_id,
                    actor,
                )
            else:
                operation_id = replay_operation_id
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        operation = await runtime.get_operation(
            organization_id=str(actor.organization_id), operation_id=str(operation_id)
        )
        if operation is None:
            raise DeliveryRouteError("delivery Operation is missing")
        return JSONResponse(_operation(operation), status_code=202)
    except _ERRORS as error:
        return _error(error)


def _summary(delivery: ExternalDelivery) -> dict[str, Any]:
    return {
        "id": str(delivery.id),
        "operation_id": str(delivery.operation_id),
        "destination_binding_id": str(delivery.destination_binding_id),
        "destination_kind": delivery.destination_kind,
        "state": delivery.state,
        "attempts": [],
        "provenance": {
            "course_run_id": str(delivery.course_run_id),
            "homework_version_id": str(delivery.homework_version_id),
            "criterion_set_id": str(delivery.criterion_set_id),
            "submission_version_id": str(delivery.submission_version_id),
            "artifact_version_id": str(delivery.artifact_version_id),
            "artifact_content_digest": delivery.artifact_content_digest,
            "review_iteration_id": str(delivery.review_iteration_id),
            "review_revision_id": str(delivery.review_revision_id),
            "contract_version": delivery.contract_version,
        },
        "error": delivery.sanitized_error,
    }


async def _reserve(
    runtime: FoundationRuntime,
    transaction: object,
    command: WireCommand,
    actor: RequestActor,
) -> UUID | None:
    reservation = await IdempotencyCoordinator(
        SqlIdempotencyReceiptRepository(),
        receipt_id_factory=runtime.id_factory,
        result_reference_factory=lambda: {"kind": "pending", "id": str(runtime.id_factory())},
    ).reserve(
        organization_id=actor.organization_id,
        idempotency_key=command.idempotency_key,
        request_id=command.request_id,
        command_name=str(command.command_name),
        target_id=command.target_id,
        expected_revision=command.expected_revision,
        payload=command.payload,
        transaction=transaction,
    )
    if reservation.disposition == "reserved":
        return None
    raw = reservation.receipt.result_reference.get("id")
    if not isinstance(raw, str) or reservation.receipt.result_reference.get("kind") != "operation":
        raise DeliveryRouteError("delivery retry receipt is incomplete")
    return UUID(raw)


async def _complete(
    transaction: object,
    organization_id: UUID,
    command: WireCommand,
    operation_id: UUID,
    actor: RequestActor,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(transaction, AsyncSession):
        raise TypeError("delivery receipt requires AsyncSession")
    repository = CommandReceiptRepository(transaction)
    receipt = await repository.get_by_idempotency_key(
        organization_id, command.idempotency_key, for_update=True
    )
    if receipt is None:
        raise DeliveryRouteError("delivery receipt disappeared")
    receipt.actor_snapshot = {
        "type": actor.actor_type,
        "user_id": str(actor.user_id),
        "roles": sorted(actor.roles),
        "membership_revision": actor.membership_revision,
        "auth_epoch": actor.auth_epoch,
    }
    if not await repository.compare_and_set_status(
        organization_id,
        receipt.id,
        expected_status="reserved",
        new_status="succeeded",
        result_reference={"kind": "operation", "id": str(operation_id)},
    ):
        raise DeliveryRouteError("delivery receipt completion lost CAS")


def _operation(operation: Any) -> dict[str, Any]:
    return {
        "id": operation.operation_id,
        "kind": operation.kind,
        "input_version": operation.input_version,
        "state": operation.state,
        "attempts": list(operation.attempts),
        "created_at": operation.created_at.isoformat()
        if hasattr(operation.created_at, "isoformat")
        else operation.created_at,
        "updated_at": operation.updated_at.isoformat()
        if hasattr(operation.updated_at, "isoformat")
        else operation.updated_at,
        "finished_at": operation.finished_at.isoformat()
        if hasattr(operation.finished_at, "isoformat")
        else operation.finished_at,
        "error": operation.error,
    }


def _context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(runtime, FoundationRuntime) or not isinstance(actor, RequestActor):
        raise AuthorizationError("authenticated runtime context is required")
    return runtime, actor


def _error(error: BaseException) -> JSONResponse:
    status = 403 if isinstance(error, AuthorizationError) else 409
    return JSONResponse(
        {"code": "request_rejected", "message": str(error)[:2048], "action": None},
        status_code=status,
    )


_ERRORS = (
    AuthorizationError,
    DeliveryRouteError,
    DeliveryServiceError,
    IdempotencyError,
    ValidationError,
    ValueError,
)

__all__ = ["router"]
