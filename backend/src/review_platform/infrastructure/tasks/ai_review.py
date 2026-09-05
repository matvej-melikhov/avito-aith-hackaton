"""Durable AI review dispatch and event-ingestion task adapters."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    AIReviewProvider,
    JsonValue,
    ProviderPayload,
)
from review_platform.application.services.ai_review_events import (
    AIComponentAuthorizationPort,
    AIEventAuditPort,
    AIEventContext,
    AIEventContextPort,
    AIEventIngestionResult,
    AIEventOperationPort,
    AIReviewEventService,
)
from review_platform.application.services.ai_review_start import (
    AIReviewDispatch,
    AIReviewScheduler,
)
from review_platform.contracts.ai_review import AIReviewRequest
from review_platform.domain.primitives import require_utc, sanitize_error, utc_now, uuid7
from review_platform.infrastructure.db.models.ai_review import AIReviewAttempt, AIReviewRun
from review_platform.infrastructure.db.models.homework import Criterion, Homework, HomeworkVersion
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.operations import (
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.submission import ArtifactReference, ArtifactVersion
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewRepository
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

from .broker import RetryableTaskError
from .registry import task_handler

AI_REVIEW_TASK_RUNTIME_FACTORY_ENV = "REVIEW_PLATFORM_AI_REVIEW_TASK_RUNTIME_FACTORY"

type DispatchOutcome = Literal["accepted", "retryable_failed", "action_required"]


class AIReviewTaskError(RuntimeError):
    """An AI task lacks safe tenant, attempt, or provider provenance."""


@dataclass(frozen=True, slots=True)
class AIReviewTaskRuntime:
    """Explicit fail-closed runtime supplied by one module:factory hook."""

    session_factory: AsyncSessionFactory
    provider: AIReviewProvider
    component_authorization: AIComponentAuthorizationPort
    event_audit: AIEventAuditPort
    id_factory: Callable[[], UUID] = uuid7
    clock: Callable[[], datetime] = utc_now
    dispatch_max_attempts: int = 5

    def __post_init__(self) -> None:
        if (
            self.provider.contract_version != CONTRACT_VERSION
            or self.provider.schema_name != "ai-review.schema.json"
        ):
            raise ValueError("AI provider must use frozen ai-review 1.1.0 schema")
        if not 1 <= self.dispatch_max_attempts <= 10:
            raise ValueError("AI dispatch attempts must be between 1 and 10")


class SqlAIReviewScheduler(AIReviewScheduler):
    """Create the Operation and AIReviewRequested outbox row in one UoW."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("AI outbox attempts must be between 1 and 10")
        self._id_factory = id_factory
        self._clock = clock
        self._max_attempts = max_attempts

    async def schedule(self, dispatch: AIReviewDispatch, *, transaction: object) -> None:
        session = _session(transaction)
        _validate_dispatch(dispatch)
        payload = _dispatch_payload(dispatch)
        existing = await session.scalar(
            select(Operation)
            .where(
                Operation.organization_id == dispatch.organization_id,
                Operation.id == dispatch.operation_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if (
                existing.kind != "ai_review"
                or existing.input_version != dispatch.input_version
            ):
                raise AIReviewTaskError("AI Operation replay provenance mismatched")
            messages = (
                await session.scalars(
                    select(OutboxMessage).where(
                        OutboxMessage.organization_id == dispatch.organization_id,
                        OutboxMessage.aggregate_type == "ai_review_run",
                        OutboxMessage.aggregate_id == dispatch.run_id,
                        OutboxMessage.event_type == dispatch.event_type,
                    )
                )
            ).all()
            if len(messages) != 1 or messages[0].payload != payload:
                raise AIReviewTaskError("AI Operation exists without exact dispatch outbox")
            return
        operation = Operation(
            id=dispatch.operation_id,
            organization_id=dispatch.organization_id,
            kind="ai_review",
            input_version=dispatch.input_version,
            state="pending",
            revision=0,
            created_at=dispatch.requested_at,
            updated_at=dispatch.requested_at,
            finished_at=None,
            error_code=None,
            sanitized_error=None,
        )
        session.add(operation)
        await session.flush([operation])
        await OutboxService(
            OutboxMessageRepository(session),
            token_factory=self._id_factory,
            clock=self._clock,
        ).create(
            OutboxDraft(
                organization_id=dispatch.organization_id,
                message_id=self._id_factory(),
                aggregate_type="ai_review_run",
                aggregate_id=dispatch.run_id,
                event_type=dispatch.event_type,
                payload_version=dispatch.payload_version,
                payload=payload,
                available_at=dispatch.requested_at,
                max_attempts=self._max_attempts,
            )
        )


@dataclass(frozen=True, slots=True)
class _DispatchContext:
    message_id: UUID
    organization_id: UUID
    run_id: UUID
    attempt_id: UUID
    attempt_number: int
    operation_id: UUID
    input_version: str
    credential_binding_id: UUID
    credential_binding_version: int
    request: AIReviewRequest


@dataclass(frozen=True, slots=True)
class _ProviderOutcome:
    outcome: DispatchOutcome
    error: Mapping[str, JsonValue] | None


class AIReviewDispatchTaskHandler:
    def __init__(self, runtime: AIReviewTaskRuntime) -> None:
        self._runtime = runtime

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with self._runtime.session_factory() as session:
            context = await _load_dispatch_context(
                session,
                organization_id=organization_uuid,
                message_id=message_uuid,
            )
            replay = await _dispatch_replay(session, context)
        if replay is not None:
            return replay

        try:
            provider_result = await self._runtime.provider.start_review(
                cast(ProviderPayload, context.request.model_dump(mode="json"))
            )
            outcome = _provider_outcome(provider_result, context=context)
        except AIReviewTaskError as error:
            outcome = _ProviderOutcome(
                "action_required",
                cast(
                    Mapping[str, JsonValue],
                    sanitize_error(
                        {
                            "code": "ai_dispatch_contract_violation",
                            "message": str(error),
                            "retryable": False,
                            "action": "inspect_ai_component",
                        },
                        max_bytes=1900,
                    ),
                ),
            )
        except Exception as error:
            outcome = _ProviderOutcome(
                "retryable_failed",
                cast(
                    Mapping[str, JsonValue],
                    sanitize_error(
                        {
                            "code": "ai_dispatch_failed",
                            "message": str(error),
                            "retryable": True,
                            "action": "retry",
                        },
                        max_bytes=1900,
                    ),
                ),
            )

        retryable = False
        async with session_scope(self._runtime.session_factory) as session:
            current = await _lock_dispatch_context(session, context)
            prior_dispatches = await _dispatch_attempt_count(
                session,
                current.organization_id,
                current.operation_id,
            )
            exhausted = (
                outcome.outcome == "retryable_failed"
                and prior_dispatches + 1 >= self._runtime.dispatch_max_attempts
            )
            if exhausted:
                outcome = _ProviderOutcome(
                    "action_required",
                    {
                        "code": "ai_dispatch_attempts_exhausted",
                        "message": "AI dispatch exhausted its retry budget",
                        "retryable": False,
                        "action": "inspect_ai_component",
                    },
                )
            await _record_dispatch_outcome(
                session,
                current,
                outcome,
                id_factory=self._runtime.id_factory,
                now=require_utc(self._runtime.clock()),
            )
            retryable = outcome.outcome == "retryable_failed"
            response = _dispatch_response(current, outcome, replayed=False)
        if retryable:
            raise RetryableTaskError("AI review dispatch failed retryably")
        return response


class SqlAIEventContextResolver(AIEventContextPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        transaction: object,
    ) -> AIEventContext | None:
        if _session(transaction) is not self._session:
            raise AIReviewTaskError("AI event resolver transaction mismatched")
        run = await self._session.scalar(
            select(AIReviewRun)
            .where(
                AIReviewRun.organization_id == organization_id,
                AIReviewRun.id == run_id,
            )
            .with_for_update()
        )
        attempt = await self._session.scalar(
            select(AIReviewAttempt)
            .where(
                AIReviewAttempt.organization_id == organization_id,
                AIReviewAttempt.ai_review_run_id == run_id,
                AIReviewAttempt.id == attempt_id,
            )
            .with_for_update()
        )
        if run is None or attempt is None:
            return None
        request = await _request_for_run(
            self._session,
            run,
            attempt,
        )
        if request is None:
            return None
        return AIEventContext(
            run=run,
            attempt=attempt,
            request=request,
            immutable_inputs_current=await _immutable_inputs_current(
                self._session,
                run,
            ),
        )


class SqlAIEventOperationRecorder(AIEventOperationPort):
    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock

    async def record_event(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        sequence: int,
        state: str,
        error: Mapping[str, Any] | None,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        operation = await session.scalar(
            select(Operation)
            .where(
                Operation.organization_id == organization_id,
                Operation.id == run_id,
                Operation.kind == "ai_review",
            )
            .with_for_update()
        )
        if operation is None:
            raise AIReviewTaskError("AI event Operation was not found")
        ai_attempt = await session.scalar(
            select(AIReviewAttempt).where(
                AIReviewAttempt.organization_id == organization_id,
                AIReviewAttempt.ai_review_run_id == run_id,
                AIReviewAttempt.id == attempt_id,
            )
        )
        if ai_attempt is None:
            raise AIReviewTaskError("AI event attempt provenance was not found")
        worker_identity = _event_worker_identity(
            attempt_id,
            sequence,
            ai_attempt.credential_binding_id,
            ai_attempt.credential_binding_version,
        )
        duplicate = await session.scalar(
            select(OperationAttempt.id).where(
                OperationAttempt.organization_id == organization_id,
                OperationAttempt.operation_id == run_id,
                OperationAttempt.worker_identity
                == worker_identity,
            )
        )
        if duplicate is not None:
            return
        attempt_number = await _next_operation_attempt(session, organization_id, run_id)
        now = require_utc(self._clock())
        sanitized = sanitize_error(error) if error is not None else None
        operation_state, attempt_outcome = _operation_state(state)
        session.add(
            OperationAttempt(
                id=self._id_factory(),
                organization_id=organization_id,
                operation_id=run_id,
                attempt_number=attempt_number,
                worker_identity=worker_identity,
                started_at=now,
                finished_at=now,
                outcome=attempt_outcome,
                error_code=(
                    str(sanitized.get("code")) if sanitized is not None else None
                ),
                sanitized_error=sanitized,
            )
        )
        operation.state = operation_state
        operation.updated_at = now
        operation.finished_at = (
            now if operation_state in {"succeeded", "action_required", "stale"} else None
        )
        operation.error_code = (
            str(sanitized.get("code")) if sanitized is not None else None
        )
        operation.sanitized_error = sanitized
        operation.revision += 1
        await session.flush()


class AIReviewEventTaskHandler:
    def __init__(self, runtime: AIReviewTaskRuntime) -> None:
        self._runtime = runtime

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with session_scope(self._runtime.session_factory) as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
            )
            payload = _event_payload(message, organization_id=organization_uuid)
            event_payload = payload.get("event")
            component_id = payload.get("component_id")
            if not isinstance(event_payload, Mapping) or not isinstance(component_id, str):
                raise AIReviewTaskError("AI event task payload is incomplete")
            result = await AIReviewEventService(
                repository=AIReviewRepository(session),
                contexts=SqlAIEventContextResolver(session),
                authorization=self._runtime.component_authorization,
                operations=SqlAIEventOperationRecorder(
                    id_factory=self._runtime.id_factory,
                    clock=self._runtime.clock,
                ),
                audit=self._runtime.event_audit,
                id_factory=self._runtime.id_factory,
                clock=self._runtime.clock,
            ).ingest(
                cast(Mapping[str, Any], event_payload),
                organization_id=organization_uuid,
                component_id=component_id,
                transaction=session,
            )
        return _event_response(result, organization_id=organization_uuid, message_id=message_uuid)


def _validate_dispatch(dispatch: AIReviewDispatch) -> None:
    request = dispatch.request
    if (
        dispatch.event_type != "AIReviewRequested"
        or dispatch.payload_version != CONTRACT_VERSION
        or dispatch.organization_id != request.organization_id
        or dispatch.run_id != request.run_id
        or dispatch.operation_id != dispatch.run_id
        or dispatch.attempt_number < 1
        or dispatch.input_version
        != f"ai-review:{CONTRACT_VERSION}:{request.input_fingerprint}"
    ):
        raise AIReviewTaskError("AI dispatch identity or contract provenance mismatched")


def _dispatch_payload(dispatch: AIReviewDispatch) -> dict[str, Any]:
    return {
        "organization_id": str(dispatch.organization_id),
        "operation_id": str(dispatch.operation_id),
        "run_id": str(dispatch.run_id),
        "attempt_id": str(dispatch.attempt_id),
        "attempt_number": dispatch.attempt_number,
        "input_version": dispatch.input_version,
        "credential_binding_id": str(dispatch.request.credential_binding_id),
        "credential_binding_version": dispatch.request.credential_binding_version,
        "request_id": str(dispatch.request_id),
        "trace_id": str(dispatch.trace_id),
        "request": dispatch.request.model_dump(mode="json"),
    }


async def _load_dispatch_context(
    session: AsyncSession,
    *,
    organization_id: UUID,
    message_id: UUID,
) -> _DispatchContext:
    message = await OutboxMessageRepository(session).get(organization_id, message_id)
    if message is None:
        raise AIReviewTaskError("tenant AI dispatch outbox message was not found")
    if (
        message.event_type != "AIReviewRequested"
        or message.payload_version != CONTRACT_VERSION
        or message.aggregate_type != "ai_review_run"
    ):
        raise AIReviewTaskError("outbox row is not an AIReviewRequested dispatch")
    payload = cast(Mapping[str, object], message.payload)
    try:
        request_value = payload.get("request")
        if not isinstance(request_value, Mapping):
            raise AIReviewTaskError("AI dispatch request payload is missing")
        request = AIReviewRequest.model_validate(dict(request_value))
    except ValidationError as error:
        raise AIReviewTaskError("AI dispatch request violates frozen schema") from error
    context = _DispatchContext(
        message_id=message_id,
        organization_id=organization_id,
        run_id=_required_uuid(payload, "run_id"),
        attempt_id=_required_uuid(payload, "attempt_id"),
        attempt_number=_required_int(payload, "attempt_number"),
        operation_id=_required_uuid(payload, "operation_id"),
        input_version=_required_string(payload, "input_version"),
        credential_binding_id=_required_uuid(payload, "credential_binding_id"),
        credential_binding_version=_required_int(payload, "credential_binding_version"),
        request=request,
    )
    if (
        message.organization_id != organization_id
        or message.aggregate_id != context.run_id
        or context.operation_id != context.run_id
        or request.organization_id != organization_id
        or request.run_id != context.run_id
        or request.credential_binding_id != context.credential_binding_id
        or request.credential_binding_version != context.credential_binding_version
    ):
        raise AIReviewTaskError("AI dispatch outbox/request provenance mismatched")
    await _validate_dispatch_rows(session, context, for_update=False)
    return context


async def _lock_dispatch_context(
    session: AsyncSession,
    context: _DispatchContext,
) -> _DispatchContext:
    await _validate_dispatch_rows(session, context, for_update=True)
    return context


async def _validate_dispatch_rows(
    session: AsyncSession,
    context: _DispatchContext,
    *,
    for_update: bool,
) -> None:
    operation_statement = select(Operation).where(
        Operation.organization_id == context.organization_id,
        Operation.id == context.operation_id,
        Operation.kind == "ai_review",
        Operation.input_version == context.input_version,
    )
    run_statement = select(AIReviewRun).where(
        AIReviewRun.organization_id == context.organization_id,
        AIReviewRun.id == context.run_id,
        AIReviewRun.input_fingerprint == context.request.input_fingerprint,
        AIReviewRun.current_attempt_no == context.attempt_number,
    )
    attempt_statement = select(AIReviewAttempt).where(
        AIReviewAttempt.organization_id == context.organization_id,
        AIReviewAttempt.ai_review_run_id == context.run_id,
        AIReviewAttempt.id == context.attempt_id,
        AIReviewAttempt.attempt_number == context.attempt_number,
        AIReviewAttempt.credential_binding_id == context.credential_binding_id,
        AIReviewAttempt.credential_binding_version == context.credential_binding_version,
    )
    credential_statement = select(ExternalCredential).where(
        ExternalCredential.organization_id == context.organization_id,
        ExternalCredential.id == context.credential_binding_id,
        ExternalCredential.binding_version == context.credential_binding_version,
        ExternalCredential.provider == "ai_review",
        ExternalCredential.status == "active",
    )
    if for_update:
        operation_statement = operation_statement.with_for_update()
        run_statement = run_statement.with_for_update()
        attempt_statement = attempt_statement.with_for_update()
        credential_statement = credential_statement.with_for_update()
    rows = (
        await session.scalar(operation_statement),
        await session.scalar(run_statement),
        await session.scalar(attempt_statement),
        await session.scalar(credential_statement),
    )
    if any(row is None for row in rows):
        raise AIReviewTaskError(
            "AI dispatch Operation/run/attempt/exact credential provenance mismatched"
        )


async def _dispatch_replay(
    session: AsyncSession,
    context: _DispatchContext,
) -> dict[str, Any] | None:
    attempt = await session.scalar(
        select(AIReviewAttempt).where(
            AIReviewAttempt.organization_id == context.organization_id,
            AIReviewAttempt.id == context.attempt_id,
        )
    )
    operation = await session.scalar(
        select(Operation).where(
            Operation.organization_id == context.organization_id,
            Operation.id == context.operation_id,
        )
    )
    dispatched = await session.scalar(
        select(OperationAttempt.id).where(
            OperationAttempt.organization_id == context.organization_id,
            OperationAttempt.operation_id == context.operation_id,
            OperationAttempt.worker_identity.like(
                f"taskiq-ai-dispatch:{context.attempt_id}:%"
            ),
            OperationAttempt.outcome.in_(("processing", "succeeded")),
        )
    )
    if attempt is None or operation is None:
        raise AIReviewTaskError("AI dispatch replay rows disappeared")
    if dispatched is None:
        return None
    return {
        "organization_id": str(context.organization_id),
        "message_id": str(context.message_id),
        "run_id": str(context.run_id),
        "attempt_id": str(context.attempt_id),
        "attempt_number": context.attempt_number,
        "state": operation.state,
        "replayed": True,
        "error": operation.sanitized_error,
    }


def _provider_outcome(
    payload: ProviderPayload,
    *,
    context: _DispatchContext,
) -> _ProviderOutcome:
    outcome = payload.get("outcome")
    if outcome not in {"accepted", "retryable_failed", "action_required"}:
        raise AIReviewTaskError("AI provider returned an unknown dispatch outcome")
    for field, expected in (
        ("contract_version", CONTRACT_VERSION),
        ("organization_id", str(context.organization_id)),
        ("run_id", str(context.run_id)),
        ("attempt_id", str(context.attempt_id)),
        ("attempt_number", context.attempt_number),
    ):
        if payload.get(field) != expected:
            raise AIReviewTaskError(f"AI provider dispatch {field} provenance mismatched")
    error_value = payload.get("error")
    if outcome == "accepted":
        if error_value is not None:
            raise AIReviewTaskError("accepted AI dispatch must not include an error")
        return _ProviderOutcome("accepted", None)
    if not isinstance(error_value, Mapping):
        raise AIReviewTaskError("failed AI dispatch omitted a typed error")
    sanitized = cast(Mapping[str, JsonValue], sanitize_error(error_value, max_bytes=1900))
    return _ProviderOutcome(cast(DispatchOutcome, outcome), sanitized)


async def _record_dispatch_outcome(
    session: AsyncSession,
    context: _DispatchContext,
    outcome: _ProviderOutcome,
    *,
    id_factory: Callable[[], UUID],
    now: datetime,
) -> None:
    operation = await session.scalar(
        select(Operation)
        .where(
            Operation.organization_id == context.organization_id,
            Operation.id == context.operation_id,
        )
        .with_for_update()
    )
    run = await session.scalar(
        select(AIReviewRun)
        .where(
            AIReviewRun.organization_id == context.organization_id,
            AIReviewRun.id == context.run_id,
        )
        .with_for_update()
    )
    attempt = await session.scalar(
        select(AIReviewAttempt)
        .where(
            AIReviewAttempt.organization_id == context.organization_id,
            AIReviewAttempt.id == context.attempt_id,
        )
        .with_for_update()
    )
    if operation is None or run is None or attempt is None:
        raise AIReviewTaskError("AI dispatch terminal rows disappeared")
    attempt_number = await _next_operation_attempt(
        session,
        context.organization_id,
        context.operation_id,
    )
    error_code = (
        str(outcome.error.get("code")) if outcome.error is not None else None
    )
    if outcome.outcome == "accepted":
        operation_state, attempt_outcome, ai_state, finished_at = (
            "processing",
            "processing",
            "running",
            None,
        )
    elif outcome.outcome == "retryable_failed":
        operation_state, attempt_outcome, ai_state, finished_at = (
            "retryable_failed",
            "retryable_failed",
            "retryable_failed",
            None,
        )
    else:
        operation_state, attempt_outcome, ai_state, finished_at = (
            "action_required",
            "action_required",
            "action_required",
            now,
        )
    session.add(
        OperationAttempt(
            id=id_factory(),
            organization_id=context.organization_id,
            operation_id=context.operation_id,
            attempt_number=attempt_number,
            worker_identity=_dispatch_worker_identity(context),
            started_at=now,
            finished_at=now,
            outcome=attempt_outcome,
            error_code=error_code,
            sanitized_error=(dict(outcome.error) if outcome.error is not None else None),
        )
    )
    operation.state = operation_state
    operation.updated_at = now
    operation.finished_at = finished_at
    operation.error_code = error_code
    operation.sanitized_error = (
        dict(outcome.error) if outcome.error is not None else None
    )
    operation.revision += 1
    attempt.status = ai_state
    attempt.finished_at = finished_at
    attempt.error_code = error_code
    attempt.sanitized_error = (
        dict(outcome.error) if outcome.error is not None else None
    )
    run.status = ai_state
    run.finished_at = finished_at
    run.revision += 1
    await session.flush()


async def _dispatch_attempt_count(
    session: AsyncSession,
    organization_id: UUID,
    operation_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(OperationAttempt)
        .where(
            OperationAttempt.organization_id == organization_id,
            OperationAttempt.operation_id == operation_id,
            OperationAttempt.worker_identity.like("taskiq-ai-dispatch:%"),
        )
    )
    return int(value or 0)


async def _next_operation_attempt(
    session: AsyncSession,
    organization_id: UUID,
    operation_id: UUID,
) -> int:
    latest = await session.scalar(
        select(func.max(OperationAttempt.attempt_number))
        .where(
            OperationAttempt.organization_id == organization_id,
            OperationAttempt.operation_id == operation_id,
        )
        .with_for_update()
    )
    return int(latest or 0) + 1


async def _request_for_run(
    session: AsyncSession,
    run: AIReviewRun,
    attempt: AIReviewAttempt,
) -> AIReviewRequest | None:
    messages = (
        await session.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.organization_id == run.organization_id,
                OutboxMessage.aggregate_type == "ai_review_run",
                OutboxMessage.aggregate_id == run.id,
                OutboxMessage.event_type == "AIReviewRequested",
                OutboxMessage.payload_version == CONTRACT_VERSION,
            )
            .order_by(OutboxMessage.created_at, OutboxMessage.message_id)
        )
    ).all()
    if len(messages) == 1:
        request = messages[0].payload.get("request")
        if isinstance(request, Mapping):
            try:
                return AIReviewRequest.model_validate(dict(request))
            except ValidationError:
                return None
    if len(messages) > 1:
        return None
    return await _reconstruct_request(session, run, attempt)


async def _reconstruct_request(
    session: AsyncSession,
    run: AIReviewRun,
    attempt: AIReviewAttempt,
) -> AIReviewRequest | None:
    artifact_row = (
        await session.execute(
            select(ArtifactVersion, ArtifactReference)
            .join(
                ArtifactReference,
                (ArtifactReference.organization_id == ArtifactVersion.organization_id)
                & (ArtifactReference.id == ArtifactVersion.artifact_reference_id),
            )
            .where(
                ArtifactVersion.organization_id == run.organization_id,
                ArtifactVersion.id == run.artifact_version_id,
            )
        )
    ).one_or_none()
    homework_row = (
        await session.execute(
            select(HomeworkVersion, Homework)
            .join(
                Homework,
                (Homework.organization_id == HomeworkVersion.organization_id)
                & (Homework.id == HomeworkVersion.homework_id),
            )
            .where(
                HomeworkVersion.organization_id == run.organization_id,
                HomeworkVersion.id == run.homework_version_id,
            )
        )
    ).one_or_none()
    criteria = (
        await session.scalars(
            select(Criterion)
            .where(
                Criterion.organization_id == run.organization_id,
                Criterion.criterion_set_id == run.criterion_set_id,
                Criterion.active.is_(True),
            )
            .order_by(Criterion.position, Criterion.id)
        )
    ).all()
    if artifact_row is None or homework_row is None or not criteria:
        return None
    artifact, reference = artifact_row
    homework_version, homework = homework_row
    try:
        return AIReviewRequest.model_validate(
            {
                "contract_version": run.contract_version,
                "run_id": run.id,
                "input_fingerprint": run.input_fingerprint,
                "fingerprint_algorithm": run.fingerprint_algorithm,
                "organization_id": run.organization_id,
                "course_run_id": run.course_run_id,
                "submission_version_id": run.submission_version_id,
                "review_iteration_id": run.review_iteration_id,
                "credential_binding_id": attempt.credential_binding_id,
                "credential_binding_version": attempt.credential_binding_version,
                "artifact": {
                    "contract_version": run.contract_version,
                    "organization_id": run.organization_id,
                    "artifact_reference_id": artifact.artifact_reference_id,
                    "artifact_version_id": artifact.id,
                    "provider": reference.provider,
                    "provider_version": artifact.provider_version,
                    "content_digest": artifact.content_digest,
                    "captured_at": artifact.captured_at,
                    "object": {
                        "key": artifact.object_key,
                        "media_type": artifact.media_type,
                        "byte_size": artifact.byte_size,
                    },
                    "metadata": dict(artifact.artifact_metadata),
                },
                "artifact_download": {
                    "url": (
                        "https://reconstructed.invalid/"
                        f"{run.organization_id}/{artifact.id}"
                    ),
                    "expires_at": run.created_at,
                },
                "homework": {
                    "version_id": homework_version.id,
                    "title": homework.title,
                    "student_text": homework_version.student_text,
                    "digest": run.homework_digest,
                },
                "criteria": {
                    "set_id": run.criterion_set_id,
                    "digest": run.criteria_digest,
                    "items": [
                        {
                            "id": criterion.id,
                            "key": criterion.stable_key,
                            "title": criterion.title,
                            "description": criterion.description,
                            "max_points": criterion.max_points,
                        }
                        for criterion in criteria
                    ],
                },
            }
        )
    except ValidationError:
        return None


async def _immutable_inputs_current(
    session: AsyncSession,
    run: AIReviewRun,
) -> bool:
    iteration = await session.scalar(
        select(ReviewIteration).where(
            ReviewIteration.organization_id == run.organization_id,
            ReviewIteration.id == run.review_iteration_id,
        )
    )
    if iteration is None:
        return False
    review_case = await session.scalar(
        select(ReviewCase).where(
            ReviewCase.organization_id == run.organization_id,
            ReviewCase.id == iteration.review_case_id,
        )
    )
    return bool(
        review_case is not None
        and review_case.current_iteration_id == iteration.id
        and iteration.course_run_id == run.course_run_id
        and iteration.submission_version_id == run.submission_version_id
        and iteration.artifact_version_id == run.artifact_version_id
        and iteration.homework_version_id == run.homework_version_id
        and iteration.criterion_set_id == run.criterion_set_id
    )


def _event_payload(
    message: OutboxMessage | None,
    *,
    organization_id: UUID,
) -> Mapping[str, object]:
    if message is None:
        raise AIReviewTaskError("tenant AI event outbox message was not found")
    if (
        message.organization_id != organization_id
        or message.event_type != "AIReviewEventReceived"
        or message.payload_version != CONTRACT_VERSION
        or message.aggregate_type != "ai_review_run"
    ):
        raise AIReviewTaskError("outbox row is not an AIReviewEventReceived message")
    payload = cast(Mapping[str, object], message.payload)
    if _required_uuid(payload, "organization_id") != organization_id:
        raise AIReviewTaskError("AI event message tenant mismatched")
    if _required_uuid(payload, "run_id") != message.aggregate_id:
        raise AIReviewTaskError("AI event message aggregate mismatched")
    return payload


def _operation_state(state: str) -> tuple[str, str]:
    if state == "running":
        return "processing", "processing"
    if state == "partial":
        return "partial", "processing"
    if state == "succeeded":
        return "succeeded", "succeeded"
    if state == "retryable_failed":
        return "retryable_failed", "retryable_failed"
    if state in {"action_required", "stale"}:
        return state, "action_required"
    raise AIReviewTaskError(f"unsupported AI event Operation state {state!r}")


def _dispatch_worker_identity(context: _DispatchContext) -> str:
    value = (
        f"taskiq-ai-dispatch:{context.attempt_id}:"
        f"credential={context.credential_binding_id}@{context.credential_binding_version}"
    )
    if len(value) > 255:
        raise AIReviewTaskError("AI dispatch worker provenance exceeds storage bound")
    return value


def _event_worker_identity(
    attempt_id: UUID,
    sequence: int,
    credential_binding_id: UUID,
    credential_binding_version: int,
) -> str:
    value = (
        f"ai-component-event:{attempt_id}:sequence={sequence}:"
        f"credential={credential_binding_id}@{credential_binding_version}"
    )
    if len(value) > 255:
        raise AIReviewTaskError("AI event worker provenance exceeds storage bound")
    return value


def _dispatch_response(
    context: _DispatchContext,
    outcome: _ProviderOutcome,
    *,
    replayed: bool,
) -> dict[str, Any]:
    return {
        "organization_id": str(context.organization_id),
        "message_id": str(context.message_id),
        "run_id": str(context.run_id),
        "attempt_id": str(context.attempt_id),
        "attempt_number": context.attempt_number,
        "state": (
            "processing" if outcome.outcome == "accepted" else outcome.outcome
        ),
        "replayed": replayed,
        "error": dict(outcome.error) if outcome.error is not None else None,
    }


def _event_response(
    result: AIEventIngestionResult,
    *,
    organization_id: UUID,
    message_id: UUID,
) -> dict[str, Any]:
    return {
        "organization_id": str(organization_id),
        "message_id": str(message_id),
        "event_id": str(result.event_id),
        "run_id": str(result.run_id),
        "attempt_id": str(result.attempt_id),
        "sequence": result.sequence,
        "disposition": result.disposition,
        "run_state": result.run_state,
    }


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise AIReviewTaskError(f"AI task {field} must be a non-empty string")
    return value


def _required_uuid(payload: Mapping[str, object], field: str) -> UUID:
    return _uuid(_required_string(payload, field), field=field)


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise AIReviewTaskError(f"AI task {field} must be a UUID") from error


def _required_int(payload: Mapping[str, object], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AIReviewTaskError(f"AI task {field} must be a positive integer")
    return value


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise AIReviewTaskError("AI task requires caller-owned AsyncSession")
    return transaction


def _load_runtime() -> AIReviewTaskRuntime:
    specification = os.environ.get(AI_REVIEW_TASK_RUNTIME_FACTORY_ENV)
    if not specification:
        raise AIReviewTaskError(f"{AI_REVIEW_TASK_RUNTIME_FACTORY_ENV} is required")
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise AIReviewTaskError(
            f"{AI_REVIEW_TASK_RUNTIME_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise AIReviewTaskError("AI task runtime factory is not callable")
    runtime = factory()
    if not isinstance(runtime, AIReviewTaskRuntime):
        raise AIReviewTaskError("AI task factory did not return AIReviewTaskRuntime")
    return runtime


@task_handler(
    name="review_platform.ai_review_dispatch",
    kind="ai_review",
    event_type="AIReviewRequested",
    requires_auth_revalidation=False,
)
async def handle_ai_review_dispatch(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    return await AIReviewDispatchTaskHandler(_load_runtime())(
        organization_id=organization_id,
        message_id=message_id,
    )


@task_handler(
    name="review_platform.ai_review_event",
    kind="ai_review",
    event_type="AIReviewEventReceived",
    requires_auth_revalidation=False,
)
async def handle_ai_review_event(
    *,
    organization_id: str,
    message_id: str,
) -> Mapping[str, Any]:
    return await AIReviewEventTaskHandler(_load_runtime())(
        organization_id=organization_id,
        message_id=message_id,
    )


_scheduler_protocol: AIReviewScheduler = SqlAIReviewScheduler()
_context_protocol: Callable[[AsyncSession], AIEventContextPort] = SqlAIEventContextResolver
_operation_protocol: AIEventOperationPort = SqlAIEventOperationRecorder()


__all__ = [
    "AI_REVIEW_TASK_RUNTIME_FACTORY_ENV",
    "AIReviewDispatchTaskHandler",
    "AIReviewEventTaskHandler",
    "AIReviewTaskError",
    "AIReviewTaskRuntime",
    "SqlAIEventContextResolver",
    "SqlAIEventOperationRecorder",
    "SqlAIReviewScheduler",
    "handle_ai_review_dispatch",
    "handle_ai_review_event",
]
