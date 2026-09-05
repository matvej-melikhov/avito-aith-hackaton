"""Frozen AI review and component protocol routes."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import sanitize_shared_value
from review_platform.application.authorization import AuthorizationError
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.application.services.ai_review_events import (
    AIComponentAuthorizationPort,
    AIEventAuditPort,
    AIEventIngestionError,
    AIReviewEventService,
)
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewStartError,
    AIReviewStartService,
    StartAIReviewCommand,
)
from review_platform.contracts.ai_review import AIReviewEvent
from review_platform.contracts.commands import WireCommand
from review_platform.domain.primitives import require_utc
from review_platform.infrastructure.db.models.operations import AuditEvent
from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewRepository
from review_platform.infrastructure.db.repositories.operations import AuditEventRepository
from review_platform.infrastructure.tasks.ai_review import (
    AIReviewTaskError,
    SqlAIEventContextResolver,
    SqlAIEventOperationRecorder,
)

router = APIRouter(tags=["ai-review"])


class AIRouteError(RuntimeError):
    pass


class ComponentContextResolver(Protocol):
    def __call__(self, bearer: str) -> tuple[str, UUID]: ...


class ArtifactContentService(Protocol):
    async def read(self, *, bearer: str, artifact_version_id: UUID) -> tuple[bytes, str]: ...


class _RouteComponentAuthorization(AIComponentAuthorizationPort):
    """Repeat the server-owned bearer resolution before the transaction commits."""

    def __init__(
        self,
        *,
        resolve: Callable[[], tuple[str, UUID]],
        component_id: str,
        organization_id: UUID,
    ) -> None:
        self._resolve = resolve
        self._component_id = component_id
        self._organization_id = organization_id

    async def authorize_component(
        self,
        *,
        component_id: str,
        organization_id: UUID,
        transaction: object,
    ) -> None:
        _require_session(transaction)
        self._check(component_id, organization_id)

    async def revalidate_component(
        self,
        *,
        component_id: str,
        organization_id: UUID,
        transaction: object,
    ) -> None:
        _require_session(transaction)
        self._check(component_id, organization_id)

    def _check(self, component_id: str, organization_id: UUID) -> None:
        resolved_component, resolved_organization = self._resolve()
        if (
            not _constant_time_text_equal(component_id, self._component_id)
            or not _constant_time_text_equal(resolved_component, self._component_id)
            or organization_id != self._organization_id
            or resolved_organization != self._organization_id
        ):
            raise AIRouteError("AI component authorization changed or mismatched")


class _SqlAIEventAudit(AIEventAuditPort):
    """Append a sanitized component audit event to the caller's SQL transaction."""

    def __init__(self, runtime: FoundationRuntime) -> None:
        self._id_factory = runtime.id_factory
        self._clock = runtime.clock

    async def record_component_event(
        self,
        *,
        organization_id: UUID,
        component_id: str,
        run_id: UUID,
        attempt_id: UUID,
        event_id: UUID,
        sequence: int,
        disposition: str,
        transaction: object,
    ) -> None:
        session = _require_session(transaction)
        details = sanitize_shared_value(
            {
                "component_id": component_id,
                "attempt_id": str(attempt_id),
                "event_id": str(event_id),
                "sequence": sequence,
                "disposition": disposition,
            }
        )
        occurred_at = require_utc(self._clock())
        await AuditEventRepository(session).append(
            AuditEvent(
                id=self._id_factory(),
                organization_id=organization_id,
                actor_type="ai_component",
                actor_user_id=None,
                installation_operator_id=None,
                agent_id=None,
                agent_authorization_id=None,
                action="accept_ai_review_event",
                entity_type="ai_review_run",
                entity_id=run_id,
                before_revision=None,
                after_revision=None,
                request_id=event_id,
                trace_id=self._id_factory(),
                outcome=disposition,
                sanitized_details=dict(details),
                occurred_at=occurred_at,
            )
        )


@router.post(
    "/v1/review-iterations/{reviewIterationId}/ai-review",
    operation_id="startAIReview",
    status_code=202,
)
async def start_ai_review(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    try:
        runtime, actor = _context(request)
        command = WireCommand.model_validate(dict(body))
        if command.command_name != "start_ai_review" or command.target_id != reviewIterationId:
            raise AIRouteError("route command or path target mismatched")
        factory = getattr(request.app.state, "ai_review_start_service_factory", None)
        if not callable(factory):
            raise AIRouteError("AI review start service is not composed")
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            service = cast(Callable[[AsyncSession], AIReviewStartService], factory)(transaction)
            result = await service.start(
                StartAIReviewCommand(
                    organization_id=actor.organization_id,
                    review_iteration_id=reviewIterationId,
                    expected_review_iteration_revision=command.expected_revision,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                ),
                actor=actor,
                credential_binding=AIComponentCredentialBinding(
                    organization_id=actor.organization_id,
                    credential_binding_id=_state_uuid(request, "ai_review_credential_binding_id"),
                    credential_binding_version=_state_int(
                        request, "ai_review_credential_binding_version"
                    ),
                ),
                transaction=transaction,
            )
            await runtime.user_auth_guard.lock_and_revalidate(actor=actor, transaction=transaction)
        operation = await runtime.get_operation(
            organization_id=str(actor.organization_id), operation_id=str(result.operation_id)
        )
        if operation is None:
            raise AIRouteError("AI review Operation is missing")
        return JSONResponse(
            {
                "id": operation.operation_id,
                "kind": operation.kind,
                "input_version": operation.input_version,
                "state": operation.state,
                "attempts": list(operation.attempts),
                "created_at": _json_time(operation.created_at),
                "updated_at": _json_time(operation.updated_at),
                "finished_at": _json_time(operation.finished_at),
                "error": operation.error,
            },
            status_code=202,
        )
    except _ERRORS as error:
        return _error(error)


@router.post("/v1/internal/ai-review/events", operation_id="acceptAIReviewEvent", status_code=202)
async def accept_ai_review_event(request: Request, body: Mapping[str, Any]) -> Response:
    try:
        bearer = _bearer(request)
        runtime = _runtime(request)
        resolver_value = getattr(
            request.app.state,
            "ai_component_context_resolver",
            None,
        )
        factory_value = getattr(request.app.state, "ai_event_service_factory", None)
        if resolver_value is not None and not callable(resolver_value):
            raise AIRouteError("AI component context resolver is invalid")
        if factory_value is not None and not callable(factory_value):
            raise AIRouteError("AI event service factory is invalid")

        if callable(resolver_value):
            resolver = cast(ComponentContextResolver, resolver_value)
            component_id, organization_id = _resolve_component(resolver, bearer)
            authorization = _RouteComponentAuthorization(
                resolve=lambda: _resolve_component(resolver, bearer),
                component_id=component_id,
                organization_id=organization_id,
            )
        else:
            component_id = _state_component_id(request, bearer)
            AIReviewEvent.model_validate(dict(body))
            organization_id = _state_component_organization(request)
            authorization = _RouteComponentAuthorization(
                resolve=lambda: (
                    _state_component_id(request, bearer),
                    _state_component_organization(request),
                ),
                component_id=component_id,
                organization_id=organization_id,
            )

        async with runtime.transaction() as transaction:
            if callable(factory_value):
                service = cast(
                    Callable[[AsyncSession], AIReviewEventService],
                    factory_value,
                )(transaction)
                fallback_service = False
            else:
                service = AIReviewEventService(
                    repository=AIReviewRepository(transaction),
                    contexts=SqlAIEventContextResolver(transaction),
                    authorization=authorization,
                    operations=SqlAIEventOperationRecorder(
                        id_factory=runtime.id_factory,
                        clock=runtime.clock,
                    ),
                    audit=_SqlAIEventAudit(runtime),
                    id_factory=runtime.id_factory,
                    clock=runtime.clock,
                )
                fallback_service = True
            result = await service.ingest(
                body,
                organization_id=organization_id,
                component_id=component_id,
                transaction=transaction,
            )
            if fallback_service and result.disposition == "replay":
                await authorization.revalidate_component(
                    component_id=component_id,
                    organization_id=organization_id,
                    transaction=transaction,
                )
        return Response(status_code=202)
    except _ERRORS as error:
        return _error(error, component=True)


@router.get(
    "/v1/internal/artifacts/{artifactVersionId}/content",
    operation_id="downloadArtifactVersion",
)
async def download_artifact_version(artifactVersionId: UUID, request: Request) -> Response:
    try:
        bearer = _bearer(request)
        service = getattr(request.app.state, "ai_artifact_content_service", None)
        if service is None:
            raise AIRouteError("AI artifact content boundary is not composed")
        content, digest = await cast(ArtifactContentService, service).read(
            bearer=bearer,
            artifact_version_id=artifactVersionId,
        )
        return Response(
            content=content, media_type="application/octet-stream", headers={"Digest": digest}
        )
    except _ERRORS as error:
        return _error(error, component=True)


def _context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = _runtime(request)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(actor, RequestActor):
        raise AuthorizationError("authenticated request actor is required")
    return runtime, actor


def _runtime(request: Request) -> FoundationRuntime:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    if not isinstance(runtime, FoundationRuntime):
        raise AIRouteError("Foundation runtime is not configured")
    return runtime


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, separator, secret = value.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not secret:
        raise AuthorizationError("component bearer is required")
    return secret


def _state_component_id(request: Request, bearer: str) -> str:
    expected = getattr(request.app.state, "ai_component_token", None)
    if not isinstance(expected, str) or not expected:
        raise AIRouteError("AI component token is not configured")
    if not _constant_time_text_equal(bearer, expected):
        raise AIRouteError("AI component bearer is invalid")
    return f"ai-component:sha256:{hashlib.sha256(expected.encode()).hexdigest()}"


def _state_component_organization(request: Request) -> UUID:
    organization_id = getattr(
        request.app.state,
        "ai_component_organization_id",
        None,
    )
    if not isinstance(organization_id, UUID):
        raise AIRouteError("AI component organization is not configured")
    return organization_id


def _resolve_component(
    resolver: ComponentContextResolver,
    bearer: str,
) -> tuple[str, UUID]:
    try:
        component_id, organization_id = resolver(bearer)
    except Exception as error:
        raise AIRouteError("AI component bearer is invalid") from error
    if (
        not isinstance(component_id, str)
        or not component_id
        or len(component_id) > 255
        or not isinstance(organization_id, UUID)
    ):
        raise AIRouteError("AI component resolver returned invalid context")
    return component_id, organization_id


def _constant_time_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def _require_session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise AIRouteError("AI event boundary requires caller-owned AsyncSession")
    return transaction


def _state_uuid(request: Request, name: str) -> UUID:
    value = getattr(request.app.state, name, None)
    if not isinstance(value, UUID):
        raise AIRouteError(f"server state {name} is missing")
    return value


def _state_int(request: Request, name: str) -> int:
    value = getattr(request.app.state, name, None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AIRouteError(f"server state {name} is invalid")
    return value


def _json_time(value: object) -> object:
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    return value


def _error(error: BaseException, *, component: bool = False) -> JSONResponse:
    if isinstance(error, AIEventIngestionError | AIReviewTaskError):
        status = 409
    elif isinstance(error, ValidationError):
        status = 422
    elif isinstance(error, AuthorizationError):
        status = 401 if component else 403
    elif component:
        status = 403
    else:
        status = 409
    return JSONResponse(
        {"code": "request_rejected", "message": str(error)[:2048], "action": None},
        status_code=status,
    )


_ERRORS = (
    AIEventIngestionError,
    AIReviewStartError,
    AIReviewTaskError,
    AIRouteError,
    AuthorizationError,
    ValidationError,
    ValueError,
)

__all__ = ["router"]
