"""Frozen MCP read tools over the same application/query logic as REST."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.projections.review_detail import (
    ArtifactDownloadGrant,
    read_review_detail,
)
from review_platform.application.request_context import AgentScope, RequestActor, Role
from review_platform.application.services.courses import CourseService
from review_platform.application.services.homeworks import (
    HomeworkHistory,
)
from review_platform.application.services.recommendations import RecommendationService
from review_platform.domain.primitives import require_utc
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.homework import Criterion
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.repositories.homeworks import SqlHomeworkRepository
from review_platform.infrastructure.db.repositories.operations import OperationRepository
from review_platform.infrastructure.db.repositories.review_work import SqlReviewWorkRepository
from review_platform.mcp.server import MCPServerError, MCPToolHandler

READ_TOOL_NAMES = frozenset(
    {
        "list_courses",
        "recommend_next_review",
        "get_review_iteration",
        "get_operation",
        "get_homework_history",
    }
)

type ReadResult = Mapping[str, Any]
type ReadCall = Callable[[], Awaitable[ReadResult]]


@dataclass(frozen=True, slots=True)
class ReadToolContext:
    runtime: FoundationRuntime
    audit: AuditRecorder
    monotonic: Callable[[], float]


def build_read_tool_registry(
    runtime: FoundationRuntime,
    *,
    audit: AuditRecorder | None = None,
    monotonic: Callable[[], float] = time.perf_counter,
) -> Mapping[str, MCPToolHandler]:
    """Bind runtime dependencies while preserving T155's stateless call shape."""

    context = ReadToolContext(
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
        monotonic=monotonic,
    )
    return {
        "list_courses": cast(MCPToolHandler, partial(list_courses, context=context)),
        "recommend_next_review": cast(
            MCPToolHandler,
            partial(recommend_next_review, context=context),
        ),
        "get_review_iteration": cast(
            MCPToolHandler,
            partial(get_review_iteration, context=context),
        ),
        "get_operation": cast(MCPToolHandler, partial(get_operation, context=context)),
        "get_homework_history": cast(
            MCPToolHandler,
            partial(get_homework_history, context=context),
        ),
    }


async def list_courses(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReadToolContext,
) -> ReadResult:
    _empty_arguments(arguments, tool_name="list_courses")
    session = _session(transaction)

    async def read() -> ReadResult:
        service = _course_service(context, session)
        courses = await service.list_courses(
            organization_id=actor.organization_id,
            actor=actor,
            statuses={"active", "archived"},
        )
        runs: list[Any] = []
        for course in courses:
            runs.extend(
                await service.list_course_runs(
                    organization_id=actor.organization_id,
                    actor=actor,
                    course_id=course.id,
                    statuses={"draft", "active", "archived"},
                )
            )
        return {
            "items": [_course_json(course) for course in courses],
            "course_runs": [_course_run_json(run) for run in runs],
        }

    return await _execute_read(
        context,
        actor=actor,
        tool_name="list_courses",
        allowed_roles={"reviewer", "methodologist"},
        required_scope="courses:read",
        entity_type="organization",
        entity_id=actor.organization_id,
        transaction=session,
        read=read,
    )


async def recommend_next_review(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReadToolContext,
) -> ReadResult:
    course_run_id = _uuid_argument(
        arguments,
        name="course_run_id",
        tool_name="recommend_next_review",
    )
    session = _session(transaction)

    async def read() -> ReadResult:
        result = await RecommendationService(
            repository=SqlReviewWorkRepository(id_factory=context.runtime.id_factory),
            authorizer=Authorizer(
                context.runtime.user_auth_guard,
                clock=context.runtime.clock,
            ),
        ).recommend_next(
            organization_id=actor.organization_id,
            course_run_id=course_run_id,
            actor=actor,
            now=context.runtime.clock(),
            transaction=session,
        )
        if result is None:
            return {
                "ok": True,
                "review_case_id": None,
                "review_case_revision": None,
                "submission_version_id": None,
                "reason": [],
            }
        return {
            "ok": True,
            "review_case_id": str(result.review_case_id),
            "review_case_revision": result.review_case_revision,
            "submission_version_id": str(result.submission_version_id),
            "reason": list(result.reason),
        }

    return await _execute_read(
        context,
        actor=actor,
        tool_name="recommend_next_review",
        allowed_roles={"reviewer"},
        required_scope="review_queue:read",
        entity_type="course_run",
        entity_id=course_run_id,
        transaction=session,
        read=read,
    )


async def get_review_iteration(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReadToolContext,
) -> ReadResult:
    review_iteration_id = _uuid_argument(
        arguments,
        name="review_iteration_id",
        tool_name="get_review_iteration",
    )
    session = _session(transaction)

    async def read() -> ReadResult:
        now = context.runtime.clock()

        def sign(
            organization_id: UUID,
            artifact_version_id: UUID,
        ) -> ArtifactDownloadGrant:
            return ArtifactDownloadGrant(
                organization_id=organization_id,
                artifact_version_id=artifact_version_id,
                url=context.runtime.sign_artifact_read(
                    organization_id=str(organization_id),
                    artifact_version_id=str(artifact_version_id),
                    requested_by_organization_id=str(actor.organization_id),
                ),
                expires_at=now
                + timedelta(
                    seconds=context.runtime.settings.ai_signed_url_ttl_seconds
                ),
            )

        result = await read_review_detail(
            session,
            organization_id=actor.organization_id,
            review_iteration_id=review_iteration_id,
            sign_artifact_download=sign,
        )
        if result is None:
            raise _not_found("tenant ReviewIteration was not found")
        return result

    return await _execute_read(
        context,
        actor=actor,
        tool_name="get_review_iteration",
        allowed_roles={"reviewer", "methodologist"},
        required_scope="reviews:read",
        entity_type="review_iteration",
        entity_id=review_iteration_id,
        transaction=session,
        read=read,
    )


async def get_operation(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReadToolContext,
) -> ReadResult:
    operation_id = _uuid_argument(
        arguments,
        name="operation_id",
        tool_name="get_operation",
    )
    session = _session(transaction)

    async def read() -> ReadResult:
        operation = await OperationRepository(session).get(
            actor.organization_id,
            operation_id,
        )
        if operation is None:
            raise _not_found("tenant Operation was not found")
        return _operation_json(operation)

    return await _execute_read(
        context,
        actor=actor,
        tool_name="get_operation",
        allowed_roles={"reviewer", "methodologist"},
        required_scope="operations:read",
        entity_type="operation",
        entity_id=operation_id,
        transaction=session,
        read=read,
    )


async def get_homework_history(
    *,
    actor: RequestActor,
    arguments: Mapping[str, Any],
    transaction: object,
    context: ReadToolContext,
) -> ReadResult:
    homework_id = _uuid_argument(
        arguments,
        name="homework_id",
        tool_name="get_homework_history",
    )
    session = _session(transaction)

    async def read() -> ReadResult:
        history = await SqlHomeworkRepository(
            id_factory=context.runtime.id_factory
        ).history(
            actor.organization_id,
            homework_id,
            transaction=session,
        )
        if history is None:
            raise _not_found("tenant Homework was not found")
        await _require_homework_visible(context, session, actor, history)
        criterion_ids = await _criterion_identity_map(session, history)
        return _history_json(history, criterion_ids)

    return await _execute_read(
        context,
        actor=actor,
        tool_name="get_homework_history",
        allowed_roles={"reviewer", "methodologist"},
        required_scope="courses:read",
        entity_type="homework",
        entity_id=homework_id,
        transaction=session,
        read=read,
    )


async def _execute_read(
    context: ReadToolContext,
    *,
    actor: RequestActor,
    tool_name: str,
    allowed_roles: set[Role],
    required_scope: AgentScope,
    entity_type: str,
    entity_id: UUID,
    transaction: AsyncSession,
    read: ReadCall,
) -> ReadResult:
    await _authorize(context, actor, allowed_roles, required_scope)
    started = context.monotonic()
    result = await read()
    duration_ms = max(0, round((context.monotonic() - started) * 1000))
    request_id = context.runtime.id_factory()
    trace_id = context.runtime.id_factory()
    await context.audit.record(
        AuditEventDraft(
            organization_id=actor.organization_id,
            actor=actor,
            action="mcp_tool_call",
            entity_type=entity_type,
            entity_id=entity_id,
            before_revision=None,
            after_revision=None,
            request_id=request_id,
            trace_id=trace_id,
            outcome="succeeded",
            details={
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "idempotency_disposition": "read",
            },
        ),
        transaction=transaction,
    )
    return result


async def _authorize(
    context: ReadToolContext,
    actor: RequestActor,
    allowed_roles: set[Role],
    required_scope: AgentScope,
) -> None:
    if (
        actor.actor_type != "agent"
        or actor.roles.isdisjoint(allowed_roles)
        or required_scope not in actor.scopes
    ):
        raise MCPServerError(
            403,
            "mcp_scope_denied",
            "agent is not authorized for this MCP tool",
            action="request_scope",
        )
    await context.runtime.user_auth_guard.revalidate(actor=actor)


def _course_service(context: ReadToolContext, session: AsyncSession) -> CourseService:
    return CourseService(
        session,
        authorizer=Authorizer(
            context.runtime.user_auth_guard,
            clock=context.runtime.clock,
        ),
        audit=context.audit,
    )


async def _require_homework_visible(
    context: ReadToolContext,
    session: AsyncSession,
    actor: RequestActor,
    history: HomeworkHistory,
) -> None:
    if "methodologist" in actor.roles:
        return
    visible = await _course_service(context, session).list_courses(
        organization_id=actor.organization_id,
        actor=actor,
        statuses={"active", "archived"},
    )
    if history.homework.course_id not in {course.id for course in visible}:
        raise MCPServerError(
            403,
            "mcp_scope_denied",
            "Homework is not visible to the current actor",
            action="request_access",
        )


async def _criterion_identity_map(
    session: AsyncSession,
    history: HomeworkHistory,
) -> dict[tuple[UUID, str], UUID]:
    criterion_set_ids = [version.criterion_set_id for version in history.versions]
    if not criterion_set_ids:
        return {}
    rows = (
        await session.execute(
            select(Criterion.criterion_set_id, Criterion.stable_key, Criterion.id).where(
                Criterion.organization_id == history.homework.organization_id,
                Criterion.criterion_set_id.in_(criterion_set_ids),
            )
        )
    ).all()
    return {
        (criterion_set_id, stable_key): criterion_id
        for criterion_set_id, stable_key, criterion_id in rows
    }


def _history_json(
    history: HomeworkHistory,
    criterion_ids: Mapping[tuple[UUID, str], UUID],
) -> dict[str, Any]:
    return {
        "homework_id": str(history.homework.homework_id),
        "homework_revision": history.homework.revision,
        "versions": [_version_json(version, criterion_ids) for version in history.versions],
        "course_run_publications": [
            {
                "id": str(publication.publication_id),
                "course_run_homework_id": str(publication.course_run_homework_id),
                "course_run_id": str(publication.course_run_id),
                "homework_version_id": str(publication.homework_version_id),
                "publication_sequence": publication.publication_sequence,
                "submission_deadline": require_utc(
                    publication.submission_deadline
                ).isoformat(),
                "review_deadline": require_utc(publication.review_deadline).isoformat(),
                "published_at": require_utc(publication.published_at).isoformat(),
                "is_current": bool(getattr(publication, "is_current", False)),
            }
            for publication in history.publications
        ],
    }


def _version_json(
    version: Any,
    criterion_ids: Mapping[tuple[UUID, str], UUID],
) -> dict[str, Any]:
    return {
        "id": str(version.version_id),
        "revision": version.revision,
        "version_number": version.version_number,
        "criterion_set_id": str(version.criterion_set_id),
        "student_text": version.requirements.student_text,
        "max_score": float(version.requirements.max_score),
        "artifact_kinds": list(version.requirements.artifact_kinds),
        "estimated_review_minutes": version.requirements.estimated_review_minutes,
        "criteria": [
            {
                "id": str(criterion_ids[(version.criterion_set_id, criterion.key)]),
                "key": criterion.key,
                "title": criterion.title,
                "description": criterion.description,
                "max_points": float(criterion.max_points),
                "position": criterion.position,
            }
            for criterion in version.requirements.criteria
        ],
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


def _course_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
    }


def _course_run_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "course_id": str(row.course_id),
        "title": row.title,
        "timezone": row.timezone,
        "status": row.status,
        "revision": row.revision,
    }


def _empty_arguments(arguments: Mapping[str, Any], *, tool_name: str) -> None:
    if arguments:
        raise _invalid_arguments(f"{tool_name} accepts an empty object")


def _uuid_argument(
    arguments: Mapping[str, Any],
    *,
    name: str,
    tool_name: str,
) -> UUID:
    if set(arguments) != {name}:
        raise _invalid_arguments(f"{tool_name} requires exactly {name}")
    raw = arguments.get(name)
    if not isinstance(raw, str):
        raise _invalid_arguments(f"{name} must be a UUID string")
    try:
        return UUID(raw)
    except ValueError as error:
        raise _invalid_arguments(f"{name} must be a UUID string") from error


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise TypeError("MCP read tools require caller-owned AsyncSession")
    return transaction


def _invalid_arguments(message: str) -> MCPServerError:
    return MCPServerError(
        400,
        "invalid_mcp_arguments",
        message,
        action="fix_arguments",
    )


def _not_found(message: str) -> MCPServerError:
    return MCPServerError(
        404,
        "mcp_resource_not_found",
        message,
        action="refresh_resource",
    )


__all__ = [
    "READ_TOOL_NAMES",
    "ReadToolContext",
    "build_read_tool_registry",
    "get_homework_history",
    "get_operation",
    "get_review_iteration",
    "list_courses",
    "recommend_next_review",
]
