"""Stateless MCP 2026-07-28 ASGI transport and dispatch boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from starlette.types import Receive, Scope, Send

from review_platform.application.auth_guards.agent import AgentGuardError
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.mcp.authentication import (
    MCPAuthenticationError,
    MCPBearerAuthenticator,
)
from review_platform.settings import Settings

if TYPE_CHECKING:
    from review_platform.application.services.ai_review_start import (
        AIComponentCredentialBinding,
    )
    from review_platform.mcp.tools.ai_publication import AIReviewStartServiceFactory

MCP_PATH = "/mcp"
MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_METHOD = "tools/call"
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_METHOD_HEADER = "Mcp-Method"
_NAME_HEADER = "Mcp-Name"


class MCPServerError(RuntimeError):
    """A bounded error that is safe to expose through the MCP transport."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.action = action


class MCPToolHandler(Protocol):
    async def __call__(
        self,
        *,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]: ...


class MCPToolDispatcher(Protocol):
    async def dispatch(
        self,
        *,
        name: str,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]: ...


class MCPAuthenticator(Protocol):
    async def resolve(
        self,
        authorization_header: str | None,
        *,
        transaction: object,
    ) -> RequestActor: ...


@dataclass(frozen=True, slots=True)
class MappingToolDispatcher:
    """Small immutable adapter used by T156-T159 registries and focused tests."""

    tools: Mapping[str, MCPToolHandler]

    def __post_init__(self) -> None:
        names = tuple(self.tools)
        if not names or any(not _valid_tool_name(name) for name in names):
            raise ValueError("MCP tool registry contains an invalid name")
        if len(names) != len(set(names)):
            raise ValueError("MCP tool registry contains duplicate names")

    async def dispatch(
        self,
        *,
        name: str,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]:
        handler = self.tools.get(name)
        if handler is None:
            raise MCPServerError(
                404,
                "mcp_tool_not_found",
                "requested MCP tool is not registered",
                action="use_registered_tool",
            )
        return await handler(
            actor=actor,
            arguments=arguments,
            transaction=transaction,
        )


class _ListCoursesHandler:
    """Minimal T155 binding retained only for transport acceptance."""

    async def __call__(
        self,
        *,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]:
        from sqlalchemy.ext.asyncio import AsyncSession

        if arguments:
            raise MCPServerError(
                400,
                "invalid_mcp_arguments",
                "list_courses accepts an empty object",
                action="fix_arguments",
            )
        if (
            actor.roles.isdisjoint({"reviewer", "methodologist"})
            or "courses:read" not in actor.scopes
        ):
            raise MCPServerError(
                403,
                "mcp_scope_denied",
                "agent is not authorized for this MCP tool",
                action="request_scope",
            )
        if not isinstance(transaction, AsyncSession):
            raise TypeError("MCP list_courses requires caller-owned AsyncSession")
        courses = (
            await transaction.scalars(
                select(Course)
                .where(
                    Course.organization_id == actor.organization_id,
                    Course.status == "active",
                )
                .order_by(Course.title, Course.id)
            )
        ).all()
        course_ids = tuple(course.id for course in courses)
        runs = []
        if course_ids:
            runs = list(
                (
                    await transaction.scalars(
                        select(CourseRun)
                        .where(
                            CourseRun.organization_id == actor.organization_id,
                            CourseRun.course_id.in_(course_ids),
                            CourseRun.status == "active",
                        )
                        .order_by(CourseRun.title, CourseRun.id)
                    )
                ).all()
            )
        return {
            "items": [
                {
                    "id": str(course.id),
                    "title": course.title,
                    "status": course.status,
                    "revision": course.revision,
                }
                for course in courses
            ],
            "course_runs": [
                {
                    "id": str(run.id),
                    "course_id": str(run.course_id),
                    "title": run.title,
                    "timezone": run.timezone,
                    "status": run.status,
                    "revision": run.revision,
                }
                for run in runs
            ],
        }


class MCPServer:
    """One stateless ASGI application over an injected runtime and dispatcher."""

    def __init__(
        self,
        settings: Settings,
        *,
        runtime: FoundationRuntime,
        dispatcher: MCPToolDispatcher,
        authenticator: MCPAuthenticator,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._dispatcher = dispatcher
        self._authenticator = authenticator
        self._app = FastAPI(
            title="Review Platform MCP",
            version=settings.contract_version,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        self._app.add_api_route(
            MCP_PATH,
            self._call,
            methods=["POST"],
            include_in_schema=False,
        )

    def streamable_http_app(self) -> FastAPI:
        """Compatibility surface for the standalone T160 entrypoint."""

        return self._app

    @property
    def dispatcher(self) -> MCPToolDispatcher:
        """Expose immutable dispatch composition for startup assertions."""

        return self._dispatcher

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await self._app(scope, receive, send)

    async def _call(self, request: Request) -> Response:
        try:
            name = _validate_headers(request)
            arguments = await _read_arguments(
                request,
                max_bytes=self._settings.command_body_limit_bytes,
            )
            async with self._runtime.transaction() as transaction:
                actor = await self._authenticator.resolve(
                    request.headers.get("authorization"),
                    transaction=transaction,
                )
                result = await self._dispatcher.dispatch(
                    name=name,
                    actor=actor,
                    arguments=arguments,
                    transaction=transaction,
                )
                await self._runtime.user_auth_guard.lock_and_revalidate(
                    actor=actor,
                    transaction=transaction,
                )
            return _json_response(result, status_code=200)
        except MCPServerError as error:
            return _error_response(error)
        except MCPAuthenticationError:
            return _error_response(
                MCPServerError(
                    401,
                    "invalid_agent_credentials",
                    "invalid agent bearer credentials",
                    action="reauthorize_agent",
                )
            )
        except AgentGuardError:
            return _error_response(
                MCPServerError(
                    401,
                    "agent_authority_changed",
                    "agent authorization is no longer active",
                    action="reauthorize_agent",
                )
            )
        except Exception:
            return _error_response(
                MCPServerError(
                    500,
                    "mcp_request_failed",
                    "MCP request could not be completed",
                    action="retry_later",
                )
            )


def build_mcp_server(
    settings: Settings | None = None,
    *,
    runtime: FoundationRuntime,
    dispatcher: MCPToolDispatcher | None = None,
    tool_registry: Mapping[str, MCPToolHandler] | None = None,
    authenticator: MCPAuthenticator | None = None,
    credential_binding: AIComponentCredentialBinding | None = None,
    ai_review_start_service_factory: AIReviewStartServiceFactory | None = None,
) -> MCPServer:
    """Build a server without process-global sessions or mutable tool state."""

    if dispatcher is not None and tool_registry is not None:
        raise ValueError("pass either dispatcher or tool_registry, not both")
    selected = settings or runtime.settings
    if dispatcher is not None:
        selected_dispatcher = dispatcher
    else:
        registry = (
            tool_registry
            if tool_registry is not None
            else _default_tool_registry(
                runtime,
                credential_binding=credential_binding,
                ai_review_start_service_factory=ai_review_start_service_factory,
            )
        )
        selected_dispatcher = MappingToolDispatcher(registry)
    selected_authenticator = (
        authenticator
        if authenticator is not None
        else MCPBearerAuthenticator(clock=runtime.clock)
    )
    return MCPServer(
        selected,
        runtime=runtime,
        dispatcher=selected_dispatcher,
        authenticator=selected_authenticator,
    )


def create_mcp_app(
    settings: Settings | None = None,
    *,
    runtime: FoundationRuntime,
    dispatcher: MCPToolDispatcher | None = None,
    tool_registry: Mapping[str, MCPToolHandler] | None = None,
    authenticator: MCPAuthenticator | None = None,
    credential_binding: AIComponentCredentialBinding | None = None,
    ai_review_start_service_factory: AIReviewStartServiceFactory | None = None,
) -> FastAPI:
    """Return the exact stateless ASGI application used by tests and T160."""

    return build_mcp_server(
        settings,
        runtime=runtime,
        dispatcher=dispatcher,
        tool_registry=tool_registry,
        authenticator=authenticator,
        credential_binding=credential_binding,
        ai_review_start_service_factory=ai_review_start_service_factory,
    ).streamable_http_app()


def _default_tool_registry(
    runtime: FoundationRuntime,
    *,
    credential_binding: AIComponentCredentialBinding | None,
    ai_review_start_service_factory: AIReviewStartServiceFactory | None,
) -> Mapping[str, MCPToolHandler]:
    from review_platform.mcp.tools import build_mcp_tool_registry

    registry = build_mcp_tool_registry(
        runtime,
        credential_binding=credential_binding,
        ai_review_start_service_factory=ai_review_start_service_factory,
    )
    if credential_binding is not None:
        return registry
    # Preserve the T155 transport acceptance semantics until the standalone
    # T160 entrypoint supplies complete AI composition. Names remain frozen.
    compatible = dict(registry)
    compatible["list_courses"] = _ListCoursesHandler()
    return compatible


def _validate_headers(request: Request) -> str:
    protocol = request.headers.get(_PROTOCOL_HEADER)
    method = request.headers.get(_METHOD_HEADER)
    name = request.headers.get(_NAME_HEADER)
    if protocol is None or method is None or name is None:
        raise MCPServerError(
            400,
            "invalid_mcp_headers",
            "required MCP headers are missing",
            action="supply_required_headers",
        )
    if protocol != MCP_PROTOCOL_VERSION:
        raise MCPServerError(
            400,
            "unsupported_mcp_protocol",
            "unsupported MCP protocol version",
            action="use_supported_protocol",
        )
    if method != MCP_METHOD:
        raise MCPServerError(
            400,
            "unsupported_mcp_method",
            "only stateless tools/call requests are supported",
            action="use_tools_call",
        )
    if not _valid_tool_name(name):
        raise MCPServerError(
            400,
            "invalid_mcp_tool_name",
            "MCP tool name is invalid",
            action="fix_tool_name",
        )
    return name


async def _read_arguments(request: Request, *, max_bytes: int) -> Mapping[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as error:
            raise MCPServerError(
                400,
                "invalid_content_length",
                "request Content-Length is invalid",
                action="fix_request",
            ) from error
        if declared < 0:
            raise MCPServerError(
                400,
                "invalid_content_length",
                "request Content-Length is invalid",
                action="fix_request",
            )
        if declared > max_bytes:
            raise _body_too_large()
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > max_bytes:
            raise _body_too_large()
        chunks.extend(chunk)
    try:
        value = json.loads(bytes(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MCPServerError(
            400,
            "invalid_mcp_body",
            "MCP request body must be one JSON object",
            action="fix_arguments",
        ) from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise MCPServerError(
            400,
            "invalid_mcp_body",
            "MCP request body must be one JSON object",
            action="fix_arguments",
        )
    return value


def _body_too_large() -> MCPServerError:
    return MCPServerError(
        413,
        "mcp_body_too_large",
        "MCP request body exceeds the configured limit",
        action="reduce_request_size",
    )


def _valid_tool_name(name: object) -> bool:
    if not isinstance(name, str) or not 1 <= len(name) <= 128:
        return False
    return all(character.islower() or character.isdigit() or character == "_" for character in name)


def _json_response(payload: Mapping[str, Any], *, status_code: int) -> JSONResponse:
    return JSONResponse(
        content=jsonable_encoder(dict(payload)),
        status_code=status_code,
        headers={_PROTOCOL_HEADER: MCP_PROTOCOL_VERSION},
    )


def _error_response(error: MCPServerError) -> JSONResponse:
    return _json_response(
        {
            "code": error.code[:128],
            "message": str(error)[:2048],
            "action": error.action[:512] if error.action is not None else None,
        },
        status_code=error.status_code,
    )


__all__ = [
    "MCP_METHOD",
    "MCP_PATH",
    "MCP_PROTOCOL_VERSION",
    "MCPAuthenticator",
    "MCPServer",
    "MCPServerError",
    "MCPToolDispatcher",
    "MCPToolHandler",
    "MappingToolDispatcher",
    "build_mcp_server",
    "create_mcp_app",
]
