"""MCP AI-start and harmless publication-request mutation bindings."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import (
    AuthorizationDenied,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyError
from review_platform.application.request_context import AgentScope, RequestActor, Role
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewStartError,
    AIReviewStartService,
    StartAIReviewCommand,
)
from review_platform.application.services.publication_requests import (
    PublicationRequestError,
    PublicationRequestService,
)
from review_platform.contracts.commands import (
    RequestReviewPublicationPayload,
    WireCommand,
)
from review_platform.domain.primitives import require_utc
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.repositories.operations import OperationRepository
from review_platform.infrastructure.db.repositories.publications import (
    SqlPublicationRepository,
)
from review_platform.mcp.server import MCPServerError, MCPToolHandler
from review_platform.mcp.tools.review import (
    ReviewMutationReceiptBoundary,
    SqlReviewMutationReceiptBoundary,
)

AI_PUBLICATION_TOOL_NAMES = frozenset({"start_ai_review", "request_review_publication"})

_TOOL_COMMAND_NAMES = {
    "start_ai_review": "start_ai_review",
    "request_review_publication": "request_review_publication",
}
_TOOL_POLICIES: dict[str, tuple[frozenset[Role], AgentScope]] = {
    "start_ai_review": (
        frozenset({"reviewer", "methodologist"}),
        "ai_reviews:start",
    ),
    "request_review_publication": (
        frozenset({"reviewer", "methodologist"}),
        "publication_requests:write",
    ),
}

type MutationResult = Mapping[str, Any]
type AIPublicationDispatcher = Callable[
    [
        RequestActor,
        WireCommand,
        object,
        UUID,
        AIComponentCredentialBinding,
    ],
    Awaitable[MutationResult],
]


class AIReviewStartServiceFactory(Protocol):
    """T160 composition port matching the existing REST service factory."""

    def __call__(self, transaction: AsyncSession) -> AIReviewStartService: ...


@dataclass(frozen=True, slots=True)
class AIPublicationToolContext:
    runtime: FoundationRuntime
    credential_binding: AIComponentCredentialBinding
    audit: AuditRecorder
    receipts: ReviewMutationReceiptBoundary
    dispatch: AIPublicationDispatcher
    monotonic: Callable[[], float]


def build_ai_publication_tool_registry(
    runtime: FoundationRuntime,
    *,
    credential_binding: AIComponentCredentialBinding,
    ai_review_start_service_factory: AIReviewStartServiceFactory,
    audit: AuditRecorder | None = None,
    receipts: ReviewMutationReceiptBoundary | None = None,
    dispatch: AIPublicationDispatcher | None = None,
    monotonic: Callable[[], float] = time.perf_counter,
) -> Mapping[str, MCPToolHandler]:
    """Bind two exact tools; service and credential identities are server-owned."""

    if credential_binding.provider != "ai_review":
        raise ValueError("MCP AI credential binding must target ai_review")
    context = AIPublicationToolContext(
        runtime=runtime,
        credential_binding=credential_binding,
        audit=(
            audit
            if audit is not None
            else AuditRecorder(
                SqlAppendOnlyAuditRepository(),
                event_id_factory=runtime.id_factory,
                clock=runtime.clock,
            )
        ),
        receipts=(
            receipts
            if receipts is not None
            else SqlReviewMutationReceiptBoundary(id_factory=runtime.id_factory)
        ),
        dispatch=(
            dispatch
            if dispatch is not None
            else partial(
                _dispatch_application_mutation,
                runtime,
                ai_review_start_service_factory,
            )
        ),
        monotonic=monotonic,
    )
    return {
        "start_ai_review": cast(
            MCPToolHandler,
            partial(start_ai_review, context=context),
        ),
        "request_review_publication": cast(
            MCPToolHandler,
            partial(request_review_publication, context=context),
        ),
    }


async def start_ai_review(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: AIPublicationToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="start_ai_review",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def request_review_publication(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: AIPublicationToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="request_review_publication",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def _execute(
    context: AIPublicationToolContext,
    *,
    tool_name: str,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
) -> Mapping[str, Any]:
    command = _command(arguments, tool_name=tool_name)
    roles, scope = _TOOL_POLICIES[tool_name]
    if actor.actor_type != "agent":
        raise _denied("MCP AI/publication tools require an authorized agent")
    authorizer = Authorizer(
        context.runtime.user_auth_guard,
        clock=context.runtime.clock,
    )
    try:
        grant = await authorizer.authorize(
            actor=actor,
            organization_id=actor.organization_id,
            policy=AuthorizationPolicy(
                required_roles=roles,
                required_scopes=frozenset({scope}),
                allowed_actor_types=frozenset({"agent"}),
            ),
        )
        disposition, replay = await context.receipts.reserve(
            command=command,
            actor=actor,
            transaction=transaction,
        )
        trace_id = context.runtime.id_factory()
        started = context.monotonic()
        if replay is None:
            raw = await context.dispatch(
                actor,
                command,
                transaction,
                trace_id,
                context.credential_binding,
            )
            result = _typed_output(tool_name, raw)
            await context.receipts.complete(
                command=command,
                actor=actor,
                payload=result,
                transaction=transaction,
            )
        else:
            result = _typed_output(tool_name, replay)
        duration_ms = max(0, round((context.monotonic() - started) * 1000))
        await context.audit.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="mcp_tool_call",
                entity_type=str(command.revision_target),
                entity_id=command.target_id,
                before_revision=command.expected_revision,
                after_revision=command.expected_revision,
                request_id=command.request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "tool_name": tool_name,
                    "duration_ms": duration_ms,
                    "idempotency_disposition": disposition,
                },
            ),
            transaction=transaction,
        )
        await authorizer.revalidate_for_commit(grant, transaction=transaction)
        return result
    except AuthorizationDenied as error:
        raise _denied(str(error)) from error
    except (AIReviewStartError, IdempotencyError, PublicationRequestError) as error:
        raise MCPServerError(
            409,
            "mcp_command_conflict",
            str(error),
            action="refresh_and_retry",
        ) from error


def _command(arguments: Mapping[str, Any], *, tool_name: str) -> WireCommand:
    try:
        command = WireCommand.model_validate(dict(arguments))
    except ValidationError as error:
        raise MCPServerError(
            400,
            "invalid_mcp_arguments",
            "tool arguments must be one exact WireCommand",
            action="fix_arguments",
        ) from error
    if str(command.command_name) != _TOOL_COMMAND_NAMES[tool_name]:
        raise MCPServerError(
            400,
            "invalid_mcp_arguments",
            "WireCommand does not match the requested MCP tool",
            action="fix_arguments",
        )
    return command


def _typed_output(tool_name: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    if tool_name == "start_ai_review":
        kind = _text(raw, "kind")
        state = _text(raw, "state")
        if kind != "ai_review" or state not in {
            "pending",
            "processing",
            "partial",
            "succeeded",
            "retryable_failed",
            "unknown_outcome",
            "reconciling",
            "action_required",
            "stale",
        }:
            raise _invalid_result("AI Operation kind or state")
        return {
            "id": _uuid_text(raw, "id"),
            "kind": kind,
            "input_version": _text(raw, "input_version"),
            "state": state,
            "attempts": _attempts(raw.get("attempts")),
            "created_at": _text(raw, "created_at"),
            "updated_at": _text(raw, "updated_at"),
            "finished_at": _optional_text(raw, "finished_at"),
            "error": _error_object(raw.get("error")),
        }
    if tool_name == "request_review_publication":
        status = _text(raw, "status")
        if status != "pending":
            raise _invalid_result("publication request status")
        return {
            "id": _uuid_text(raw, "id"),
            "review_revision_id": _uuid_text(raw, "review_revision_id"),
            "status": status,
            "expires_at": _text(raw, "expires_at"),
            "revision": _nonnegative_int(raw, "revision"),
        }
    raise _invalid_result("tool_name")


async def _dispatch_application_mutation(
    runtime: FoundationRuntime,
    ai_review_start_service_factory: AIReviewStartServiceFactory,
    actor: RequestActor,
    command: WireCommand,
    transaction: object,
    trace_id: UUID,
    credential_binding: AIComponentCredentialBinding,
) -> MutationResult:
    session = _session(transaction)
    if str(command.command_name) == "start_ai_review":
        service = ai_review_start_service_factory(session)
        if not isinstance(service, AIReviewStartService):
            raise AIReviewStartError("AI review start service factory returned invalid service")
        result = await service.start(
            StartAIReviewCommand(
                organization_id=actor.organization_id,
                review_iteration_id=command.target_id,
                expected_review_iteration_revision=command.expected_revision,
                request_id=command.request_id,
                trace_id=trace_id,
            ),
            actor=actor,
            credential_binding=credential_binding,
            transaction=session,
        )
        operation = await OperationRepository(session).get(
            actor.organization_id,
            result.operation_id,
        )
        if operation is None:
            raise AIReviewStartError("AI review Operation is missing after scheduling")
        return _operation_json(operation)

    payload = cast(RequestReviewPublicationPayload, command.payload)
    requested = await PublicationRequestService(
        repository=SqlPublicationRepository(),
        authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
        audit=AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        ),
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    ).request(
        transaction=session,
        organization_id=actor.organization_id,
        review_iteration_id=command.target_id,
        expected_iteration_revision=command.expected_revision,
        review_revision_id=payload.review_revision_id,
        expires_at=payload.expires_at,
        idempotency_key=command.idempotency_key,
        actor=actor,
        request_id=command.request_id,
        trace_id=trace_id,
    )
    record = requested.request
    return {
        "id": str(record.publication_request_id),
        "review_revision_id": str(record.review_revision_id),
        "status": record.status,
        "expires_at": require_utc(record.expires_at).isoformat(),
        "revision": record.revision,
    }


def _operation_json(operation: Operation) -> dict[str, Any]:
    attempts = sorted(operation.attempts, key=lambda item: (item.attempt_number, item.id))
    return {
        "id": str(operation.id),
        "kind": operation.kind,
        "input_version": operation.input_version,
        "state": operation.state,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "state": attempt.outcome,
                "started_at": require_utc(attempt.started_at).isoformat(),
                "finished_at": (
                    require_utc(attempt.finished_at).isoformat()
                    if attempt.finished_at is not None
                    else None
                ),
                "error": attempt.sanitized_error,
            }
            for attempt in attempts
        ],
        "created_at": require_utc(operation.created_at).isoformat(),
        "updated_at": require_utc(operation.updated_at).isoformat(),
        "finished_at": (
            require_utc(operation.finished_at).isoformat()
            if operation.finished_at is not None
            else None
        ),
        "error": operation.sanitized_error,
    }


def _uuid_text(value: Mapping[str, Any], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str):
        raise _invalid_result(name)
    try:
        return str(UUID(raw))
    except ValueError as error:
        raise _invalid_result(name) from error


def _text(value: Mapping[str, Any], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str):
        raise _invalid_result(name)
    return raw


def _optional_text(value: Mapping[str, Any], name: str) -> str | None:
    raw = value.get(name)
    if raw is not None and not isinstance(raw, str):
        raise _invalid_result(name)
    return raw


def _nonnegative_int(value: Mapping[str, Any], name: str) -> int:
    raw = value.get(name)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise _invalid_result(name)
    return raw


def _attempts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _invalid_result("attempts")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _invalid_result("attempt")
        state = _text(item, "state")
        if state not in {
            "processing",
            "succeeded",
            "retryable_failed",
            "unknown_outcome",
            "action_required",
        }:
            raise _invalid_result("attempt state")
        attempt_number = _nonnegative_int(item, "attempt_number")
        if attempt_number < 1:
            raise _invalid_result("attempt_number")
        result.append(
            {
                "attempt_number": attempt_number,
                "state": state,
                "started_at": _text(item, "started_at"),
                "finished_at": _optional_text(item, "finished_at"),
                "error": _error_object(item.get("error")),
            }
        )
    return result


def _error_object(value: object) -> dict[str, str | None] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _invalid_result("error")
    action = value.get("action")
    if action is not None and not isinstance(action, str):
        raise _invalid_result("error action")
    return {
        "code": _text(value, "code"),
        "message": _text(value, "message"),
        "action": action,
    }


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise TypeError("MCP AI/publication tools require caller-owned AsyncSession")
    return transaction


def _invalid_result(field: str) -> MCPServerError:
    return MCPServerError(
        500,
        "mcp_result_invalid",
        f"application result has invalid {field}",
        action="contact_operator",
    )


def _denied(message: str) -> MCPServerError:
    return MCPServerError(
        403,
        "mcp_scope_denied",
        message,
        action="request_scope",
    )


__all__ = [
    "AI_PUBLICATION_TOOL_NAMES",
    "AIPublicationDispatcher",
    "AIPublicationToolContext",
    "AIReviewStartServiceFactory",
    "build_ai_publication_tool_registry",
    "request_review_publication",
    "start_ai_review",
]
