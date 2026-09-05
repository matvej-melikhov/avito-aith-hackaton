"""Frozen MCP review mutations over the REST application composition."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.api.routes.review_composition import (
    ReviewCompositionError,
    dispatch_review_mutation,
)
from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import (
    AuthorizationDenied,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import (
    IdempotencyCoordinator,
    IdempotencyError,
)
from review_platform.application.request_context import AgentScope, RequestActor, Role
from review_platform.application.services.review_iterations import (
    OpenReviewIterationCommand,
    ReviewIterationAuditPort,
    ReviewIterationAuthorizationPort,
    ReviewIterationError,
    ReviewIterationService,
)
from review_platform.contracts.commands import OpenReviewIterationPayload, WireCommand
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.repositories.operations import CommandReceiptRepository
from review_platform.infrastructure.db.repositories.submissions import (
    SqlReviewIterationRepository,
)
from review_platform.mcp.server import MCPServerError, MCPToolHandler

REVIEW_TOOL_NAMES = frozenset(
    {
        "select_reviewer_courses",
        "set_availability",
        "open_review_iteration",
        "record_review_responsibility",
        "save_review_revision",
    }
)

_TOOL_COMMAND_NAMES = {
    "select_reviewer_courses": "set_reviewer_course_selection",
    "set_availability": "set_reviewer_availability",
    "open_review_iteration": "open_review_iteration",
    "record_review_responsibility": "record_review_responsibility",
    "save_review_revision": "save_review_revision",
}
_TOOL_POLICIES: dict[str, tuple[frozenset[Role], AgentScope]] = {
    "select_reviewer_courses": (frozenset({"reviewer"}), "review_preferences:write"),
    "set_availability": (frozenset({"reviewer"}), "review_preferences:write"),
    "open_review_iteration": (
        frozenset({"reviewer", "methodologist"}),
        "reviews:write",
    ),
    "record_review_responsibility": (
        frozenset({"reviewer", "methodologist"}),
        "reviews:write",
    ),
    "save_review_revision": (
        frozenset({"reviewer", "methodologist"}),
        "reviews:write",
    ),
}

type MutationResult = Mapping[str, Any] | None
type MutationDispatcher = Callable[
    [RequestActor, WireCommand, object, UUID], Awaitable[MutationResult]
]


class ReviewMutationReceiptBoundary(Protocol):
    async def reserve(
        self,
        *,
        command: WireCommand,
        actor: RequestActor,
        transaction: object,
    ) -> tuple[str, Mapping[str, Any] | None]: ...

    async def complete(
        self,
        *,
        command: WireCommand,
        actor: RequestActor,
        payload: Mapping[str, Any],
        transaction: object,
    ) -> None: ...


class SqlReviewMutationReceiptBoundary:
    """Persist command identity, exact agent snapshot, and stable typed result."""

    def __init__(self, *, id_factory: Callable[[], UUID]) -> None:
        self._id_factory = id_factory

    async def reserve(
        self,
        *,
        command: WireCommand,
        actor: RequestActor,
        transaction: object,
    ) -> tuple[str, Mapping[str, Any] | None]:
        session = _session(transaction)
        reservation = await IdempotencyCoordinator(
            SqlIdempotencyReceiptRepository(),
            receipt_id_factory=self._id_factory,
            result_reference_factory=lambda: {"kind": "pending", "payload": {}},
        ).reserve(
            organization_id=actor.organization_id,
            idempotency_key=command.idempotency_key,
            request_id=command.request_id,
            command_name=str(command.command_name),
            target_id=command.target_id,
            expected_revision=command.expected_revision,
            payload=command.payload,
            transaction=session,
        )
        repository = CommandReceiptRepository(session)
        receipt = await repository.get_by_idempotency_key(
            actor.organization_id,
            command.idempotency_key,
            for_update=True,
        )
        if receipt is None:
            raise IdempotencyError("MCP command receipt disappeared after reservation")
        snapshot = _actor_snapshot(actor)
        if reservation.disposition == "reserved":
            if receipt.actor_snapshot:
                raise IdempotencyError("new MCP receipt is unexpectedly actor-bound")
            receipt.actor_snapshot = snapshot
            await session.flush([receipt])
            return "reserved", None
        if receipt.actor_snapshot != snapshot:
            raise IdempotencyError(
                "MCP idempotency replay actor or authority snapshot does not match"
            )
        payload = reservation.receipt.result_reference.get("payload")
        if not isinstance(payload, dict):
            raise IdempotencyError("MCP idempotency replay payload is incomplete")
        return "replay", cast(dict[str, Any], payload)

    async def complete(
        self,
        *,
        command: WireCommand,
        actor: RequestActor,
        payload: Mapping[str, Any],
        transaction: object,
    ) -> None:
        session = _session(transaction)
        repository = CommandReceiptRepository(session)
        receipt = await repository.get_by_idempotency_key(
            actor.organization_id,
            command.idempotency_key,
            for_update=True,
        )
        if (
            receipt is None
            or receipt.actor_snapshot != _actor_snapshot(actor)
            or not await repository.compare_and_set_status(
                actor.organization_id,
                receipt.id,
                expected_status="reserved",
                new_status="succeeded",
                result_reference={"kind": "response", "payload": dict(payload)},
            )
        ):
            raise IdempotencyError("MCP command receipt completion failed")


@dataclass(frozen=True, slots=True)
class ReviewToolContext:
    runtime: FoundationRuntime
    audit: AuditRecorder
    receipts: ReviewMutationReceiptBoundary
    dispatch: MutationDispatcher
    monotonic: Callable[[], float]


def build_review_tool_registry(
    runtime: FoundationRuntime,
    *,
    audit: AuditRecorder | None = None,
    receipts: ReviewMutationReceiptBoundary | None = None,
    dispatch: MutationDispatcher | None = None,
    monotonic: Callable[[], float] = time.perf_counter,
) -> Mapping[str, MCPToolHandler]:
    """Bind five exact review mutations without publication or AI authority."""

    context = ReviewToolContext(
        runtime=runtime,
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
            dispatch if dispatch is not None else partial(_dispatch_application_mutation, runtime)
        ),
        monotonic=monotonic,
    )
    return {
        "select_reviewer_courses": cast(
            MCPToolHandler,
            partial(select_reviewer_courses, context=context),
        ),
        "set_availability": cast(
            MCPToolHandler,
            partial(set_availability, context=context),
        ),
        "open_review_iteration": cast(
            MCPToolHandler,
            partial(open_review_iteration, context=context),
        ),
        "record_review_responsibility": cast(
            MCPToolHandler,
            partial(record_review_responsibility, context=context),
        ),
        "save_review_revision": cast(
            MCPToolHandler,
            partial(save_review_revision, context=context),
        ),
    }


async def select_reviewer_courses(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReviewToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="select_reviewer_courses",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def set_availability(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReviewToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="set_availability",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def open_review_iteration(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReviewToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="open_review_iteration",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def record_review_responsibility(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReviewToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="record_review_responsibility",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def save_review_revision(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReviewToolContext,
) -> Mapping[str, Any]:
    return await _execute(
        context,
        tool_name="save_review_revision",
        actor=actor,
        arguments=arguments,
        transaction=transaction,
    )


async def _execute(
    context: ReviewToolContext,
    *,
    tool_name: str,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
) -> Mapping[str, Any]:
    command = _command(arguments, tool_name=tool_name)
    authorizer = Authorizer(
        context.runtime.user_auth_guard,
        clock=context.runtime.clock,
    )
    roles, scope = _TOOL_POLICIES[tool_name]
    if actor.actor_type != "agent":
        raise _denied("MCP review mutations require an authorized agent")
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
            raw_result = await context.dispatch(actor, command, transaction, trace_id)
            result = _typed_output(tool_name, command, raw_result)
            await context.receipts.complete(
                command=command,
                actor=actor,
                payload=result,
                transaction=transaction,
            )
        else:
            result = _typed_output(tool_name, command, replay)
        duration_ms = max(0, round((context.monotonic() - started) * 1000))
        before_revision, after_revision = _audit_revisions(
            tool_name,
            command,
            result,
        )
        await context.audit.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="mcp_tool_call",
                entity_type=str(command.revision_target),
                entity_id=command.target_id,
                before_revision=before_revision,
                after_revision=after_revision,
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
    except (IdempotencyError, ReviewCompositionError, ReviewIterationError) as error:
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


def _typed_output(
    tool_name: str,
    command: WireCommand,
    raw: MutationResult,
) -> dict[str, Any]:
    value = dict(raw or {})
    if tool_name == "select_reviewer_courses":
        return {
            "ok": True,
            "entity_id": str(command.target_id),
            "revision": command.expected_revision,
        }
    if tool_name in {"set_availability", "record_review_responsibility"}:
        return {
            "ok": True,
            "entity_id": _uuid_text(value, "id"),
            "revision": _nonnegative_int(value, "revision"),
        }
    if tool_name == "open_review_iteration":
        return {
            "review_iteration_id": _uuid_text(value, "review_iteration_id"),
            "review_case_id": _uuid_text(value, "review_case_id"),
            "revision": _nonnegative_int(value, "revision"),
        }
    if tool_name == "save_review_revision":
        return {
            "review_revision_id": _uuid_text(value, "review_revision_id"),
            "review_iteration_id": _uuid_text(value, "review_iteration_id"),
            "review_iteration_revision": _nonnegative_int(
                value,
                "review_iteration_revision",
            ),
        }
    raise MCPServerError(
        500,
        "mcp_result_invalid",
        "unrecognized review tool output",
        action="contact_operator",
    )


async def _dispatch_application_mutation(
    runtime: FoundationRuntime,
    actor: RequestActor,
    command: WireCommand,
    transaction: object,
    trace_id: UUID,
) -> MutationResult:
    session = _session(transaction)
    if str(command.command_name) != "open_review_iteration":
        return await dispatch_review_mutation(
            runtime=runtime,
            actor=actor,
            command=command,
            transaction=session,
        )
    payload = cast(OpenReviewIterationPayload, command.payload)
    result = await ReviewIterationService(
        repository=SqlReviewIterationRepository(session),
        authorization=_OpenAuthorization(runtime),
        audit=_OpenAudit(runtime),
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    ).open(
        OpenReviewIterationCommand(
            organization_id=actor.organization_id,
            review_case_id=command.target_id,
            submission_version_id=payload.submission_version_id,
            expected_review_case_revision=command.expected_revision,
            request_id=command.request_id,
            trace_id=trace_id,
        ),
        actor=actor,
        transaction=session,
    )
    return {
        "review_iteration_id": str(result.review_iteration_id),
        "review_case_id": str(result.review_case_id),
        "revision": result.review_case_revision,
    }


class _OpenAuthorization(ReviewIterationAuthorizationPort):
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._runtime = runtime

    async def authorize_open(
        self,
        *,
        actor: RequestActor,
        organization_id: UUID,
        transaction: object,
    ) -> None:
        await Authorizer(
            self._runtime.user_auth_guard,
            clock=self._runtime.clock,
        ).authorize(
            actor=actor,
            organization_id=organization_id,
            policy=AuthorizationPolicy(
                required_roles=frozenset({"reviewer", "methodologist"}),
                required_scopes=frozenset({"reviews:write"}),
                allowed_actor_types=frozenset({"agent"}),
            ),
        )

    async def revalidate_for_commit(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> None:
        await self._runtime.user_auth_guard.lock_and_revalidate(
            actor=actor,
            transaction=transaction,
        )


class _OpenAudit(ReviewIterationAuditPort):
    def __init__(self, runtime: FoundationRuntime) -> None:
        self._recorder = AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        )

    async def record_open(
        self,
        *,
        actor: RequestActor,
        iteration: ReviewIteration,
        before_revision: int,
        after_revision: int,
        request_id: UUID,
        trace_id: UUID,
        transaction: object,
    ) -> None:
        await self._recorder.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="open_review_iteration",
                entity_type="review_iteration",
                entity_id=iteration.id,
                before_revision=before_revision,
                after_revision=after_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "submission_version_id": str(iteration.submission_version_id),
                },
            ),
            transaction=transaction,
        )


def _actor_snapshot(actor: RequestActor) -> dict[str, Any]:
    return {
        "type": actor.actor_type,
        "organization_id": str(actor.organization_id),
        "user_id": str(actor.user_id) if actor.user_id is not None else None,
        "roles": sorted(str(role) for role in actor.roles),
        "membership_revision": actor.membership_revision,
        "auth_epoch": actor.auth_epoch,
        "agent_id": str(actor.agent_id) if actor.agent_id is not None else None,
        "agent_authorization_id": (
            str(actor.agent_authorization_id) if actor.agent_authorization_id is not None else None
        ),
        "agent_authorization_revision": actor.agent_authorization_revision,
        "scopes": sorted(str(scope) for scope in actor.scopes),
        "expires_at": (actor.expires_at.isoformat() if actor.expires_at is not None else None),
    }


def _uuid_text(value: Mapping[str, Any], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str):
        raise _invalid_result(name)
    try:
        return str(UUID(raw))
    except ValueError as error:
        raise _invalid_result(name) from error


def _nonnegative_int(value: Mapping[str, Any], name: str) -> int:
    raw = value.get(name)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise _invalid_result(name)
    return raw


def _audit_revisions(
    tool_name: str,
    command: WireCommand,
    value: Mapping[str, Any],
) -> tuple[int, int]:
    if tool_name in {
        "select_reviewer_courses",
        "set_availability",
        "record_review_responsibility",
    }:
        return command.expected_revision, command.expected_revision
    name = "review_iteration_revision" if tool_name == "save_review_revision" else "revision"
    return command.expected_revision, _nonnegative_int(value, name)


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise TypeError("MCP review mutations require caller-owned AsyncSession")
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
    "REVIEW_TOOL_NAMES",
    "ReviewMutationReceiptBoundary",
    "ReviewToolContext",
    "SqlReviewMutationReceiptBoundary",
    "build_review_tool_registry",
    "open_review_iteration",
    "record_review_responsibility",
    "save_review_revision",
    "select_reviewer_courses",
    "set_availability",
]
