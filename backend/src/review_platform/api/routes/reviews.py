"""Frozen US5 review route registry and server-owned dispatch boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.api.routes.review_composition import (
    ReviewCompositionError,
    dispatch_review_mutation,
    read_recommendation,
    read_review_detail_response,
)
from review_platform.application.authorization import (
    AuthorizationError,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyCoordinator, IdempotencyError
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import WireCommand
from review_platform.infrastructure.db.adapters import SqlIdempotencyReceiptRepository
from review_platform.infrastructure.db.repositories.operations import CommandReceiptRepository

router = APIRouter(tags=["reviews"])

_REVIEW_POLICIES = {
    "set_reviewer_course_selection": AuthorizationPolicy(
        required_roles=frozenset({"reviewer"}),
        required_scopes=frozenset({"review_preferences:write"}),
    ),
    "set_reviewer_availability": AuthorizationPolicy(
        required_roles=frozenset({"reviewer"}),
        required_scopes=frozenset({"review_preferences:write"}),
    ),
    "save_review_revision": AuthorizationPolicy(
        required_roles=frozenset({"reviewer", "methodologist"}),
        required_scopes=frozenset({"reviews:write"}),
    ),
    "record_review_responsibility": AuthorizationPolicy(
        required_roles=frozenset({"reviewer", "methodologist"}),
        required_scopes=frozenset({"reviews:write"}),
    ),
    "request_review_publication": AuthorizationPolicy(
        required_roles=frozenset({"reviewer", "methodologist"}),
        required_scopes=frozenset({"publication_requests:write"}),
    ),
    "migrate_review_requirements": AuthorizationPolicy(
        required_roles=frozenset({"reviewer", "methodologist"}),
        allowed_actor_types=frozenset({"user"}),
    ),
    "create_review_correction": AuthorizationPolicy(
        required_roles=frozenset({"reviewer", "methodologist"}),
        allowed_actor_types=frozenset({"user"}),
    ),
    "publish_review": AuthorizationPolicy(
        required_roles=frozenset({"reviewer", "methodologist"}),
        allowed_actor_types=frozenset({"user"}),
    ),
}


class ReviewRouteError(RuntimeError):
    pass


class ReviewRouteHandler(Protocol):
    async def __call__(
        self, *, actor: RequestActor, command: WireCommand, transaction: object
    ) -> Mapping[str, Any] | None: ...


class ReviewReadHandler(Protocol):
    async def __call__(
        self, *, actor: RequestActor, identity: UUID | None, transaction: object
    ) -> Mapping[str, Any] | None: ...


@router.post("/v1/reviewer/course-selections", operation_id="setReviewerCourseSelection")
async def set_reviewer_course_selection(request: Request, body: Mapping[str, Any]) -> Response:
    return await _mutation(request, body, "set_reviewer_course_selection", None, 204)


@router.put("/v1/reviewer/availability", operation_id="setReviewerAvailability")
async def set_reviewer_availability(request: Request, body: Mapping[str, Any]) -> Response:
    return await _mutation(request, body, "set_reviewer_availability", None, 200)


@router.get("/v1/review-queue/next", operation_id="recommendNextReview")
async def recommend_next_review(request: Request) -> Response:
    raw_course_run_id = request.query_params.get("course_run_id")
    try:
        course_run_id = UUID(raw_course_run_id or "")
    except ValueError:
        return _error(409, "course_run_id query parameter must be a UUID")
    return await _read(request, "recommend_next_review", course_run_id)


@router.post(
    "/v1/review-iterations/{reviewIterationId}/revisions",
    operation_id="saveReviewRevision",
)
async def save_review_revision(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _mutation(request, body, "save_review_revision", reviewIterationId, 201)


@router.post(
    "/v1/review-iterations/{reviewIterationId}/requirements-migrations",
    operation_id="migrateReviewRequirements",
)
async def migrate_review_requirements(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _mutation(request, body, "migrate_review_requirements", reviewIterationId, 201)


@router.post(
    "/v1/review-iterations/{reviewIterationId}/corrections",
    operation_id="createReviewCorrection",
)
async def create_review_correction(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _mutation(request, body, "create_review_correction", reviewIterationId, 201)


@router.post(
    "/v1/review-iterations/{reviewIterationId}/responsibility-events",
    operation_id="recordReviewResponsibility",
)
async def record_review_responsibility(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _mutation(request, body, "record_review_responsibility", reviewIterationId, 201)


@router.get("/v1/review-iterations/{reviewIterationId}", operation_id="getReviewIteration")
async def get_review_iteration(reviewIterationId: UUID, request: Request) -> Response:
    return await _read(request, "get_review_iteration", reviewIterationId)


@router.post(
    "/v1/review-iterations/{reviewIterationId}/publication-requests",
    operation_id="requestReviewPublication",
)
async def request_review_publication(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _mutation(request, body, "request_review_publication", reviewIterationId, 201)


@router.post(
    "/v1/review-iterations/{reviewIterationId}/publish",
    operation_id="publishReview",
    status_code=202,
)
async def publish_review(
    reviewIterationId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(actor, RequestActor) or actor.actor_type != "user":
        return _error(403, "interactive human REST session is required")
    return await _mutation(request, body, "publish_review", reviewIterationId, 202)


async def _mutation(
    request: Request,
    body: Mapping[str, Any],
    name: str,
    target: UUID | None,
    status: int,
) -> Response:
    try:
        runtime, actor = _context(request)
        command = WireCommand.model_validate(dict(body))
        if command.command_name != name or (target is not None and command.target_id != target):
            raise ReviewRouteError("route command or path target mismatched")
        handlers = getattr(request.app.state, "review_route_handlers", None)
        if handlers is not None and not isinstance(handlers, Mapping):
            raise ReviewRouteError("review route handler registry is invalid")
        authorizer = Authorizer(runtime.user_auth_guard, clock=runtime.clock)
        policy = _REVIEW_POLICIES.get(name)
        if policy is None:
            raise ReviewRouteError(f"review command has no authorization policy: {name}")
        grant = await authorizer.authorize(
            actor=actor,
            organization_id=actor.organization_id,
            policy=policy,
        )
        async with runtime.transaction() as transaction:
            replay = await _reserve(
                runtime,
                transaction,
                command,
                actor,
            )
            if replay is None:
                if isinstance(handlers, Mapping) and name in handlers:
                    handler = cast(ReviewRouteHandler, handlers[name])
                    result = await handler(
                        actor=actor,
                        command=command,
                        transaction=transaction,
                    )
                else:
                    result = await dispatch_review_mutation(
                        runtime=runtime,
                        actor=actor,
                        command=command,
                        transaction=transaction,
                    )
                response_payload = dict(result or {})
                await _complete(
                    transaction,
                    actor.organization_id,
                    command,
                    response_payload,
                )
            else:
                response_payload = replay
            await authorizer.revalidate_for_commit(grant, transaction=transaction)
        if status == 204:
            return Response(status_code=204)
        return JSONResponse(response_payload, status_code=status)
    except AuthorizationError as error:
        return _error(403, str(error))
    except (
        IdempotencyError,
        ReviewCompositionError,
        ReviewRouteError,
        ValidationError,
        ValueError,
    ) as error:
        return _error(409, str(error))


async def _read(request: Request, name: str, identity: UUID | None) -> Response:
    try:
        runtime, actor = _context(request)
        handlers = getattr(request.app.state, "review_read_handlers", None)
        if handlers is not None and not isinstance(handlers, Mapping):
            raise ReviewRouteError("review read handler registry is invalid")
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            if isinstance(handlers, Mapping) and name in handlers:
                result = await cast(ReviewReadHandler, handlers[name])(
                    actor=actor,
                    identity=identity,
                    transaction=transaction,
                )
            elif name == "recommend_next_review" and identity is not None:
                result = await read_recommendation(
                    runtime=runtime,
                    actor=actor,
                    course_run_id=identity,
                    transaction=transaction,
                )
            elif name == "get_review_iteration" and identity is not None:
                result = await read_review_detail_response(
                    runtime=runtime,
                    actor=actor,
                    review_iteration_id=identity,
                    transaction=transaction,
                )
            else:
                raise ReviewRouteError(f"review read handler is not composed: {name}")
            await runtime.user_auth_guard.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
        return JSONResponse(dict(result) if result is not None else None)
    except AuthorizationError as error:
        return _error(403, str(error))
    except (ReviewCompositionError, ReviewRouteError, ValueError) as error:
        return _error(409, str(error))


async def _reserve(
    runtime: FoundationRuntime,
    transaction: AsyncSession,
    command: WireCommand,
    actor: RequestActor,
) -> dict[str, Any] | None:
    reservation = await IdempotencyCoordinator(
        SqlIdempotencyReceiptRepository(),
        receipt_id_factory=runtime.id_factory,
        result_reference_factory=lambda: {"kind": "pending", "payload": {}},
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
    repository = CommandReceiptRepository(transaction)
    receipt = await repository.get_by_idempotency_key(
        actor.organization_id,
        command.idempotency_key,
        for_update=True,
    )
    if receipt is None:
        raise ReviewRouteError("idempotency receipt disappeared after reservation")
    expected_actor = _actor_snapshot(actor)
    if reservation.disposition == "reserved":
        if receipt.actor_snapshot:
            raise ReviewRouteError("new idempotency receipt is unexpectedly actor-bound")
        receipt.actor_snapshot = expected_actor
        await transaction.flush([receipt])
    elif receipt.actor_snapshot != expected_actor:
        raise IdempotencyError(
            "idempotency replay actor or authority snapshot does not match"
        )
    if reservation.disposition == "reserved":
        return None
    payload = reservation.receipt.result_reference.get("payload")
    if not isinstance(payload, dict):
        raise ReviewRouteError("idempotency replay payload is incomplete")
    return cast(dict[str, Any], payload)


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
            str(actor.agent_authorization_id)
            if actor.agent_authorization_id is not None
            else None
        ),
        "agent_authorization_revision": actor.agent_authorization_revision,
        "scopes": sorted(str(scope) for scope in actor.scopes),
        "expires_at": actor.expires_at.isoformat() if actor.expires_at is not None else None,
    }


async def _complete(
    transaction: AsyncSession,
    organization_id: UUID,
    command: WireCommand,
    payload: Mapping[str, Any],
) -> None:
    repository = CommandReceiptRepository(transaction)
    receipt = await repository.get_by_idempotency_key(
        organization_id,
        command.idempotency_key,
        for_update=True,
    )
    if receipt is None or not await repository.compare_and_set_status(
        organization_id,
        receipt.id,
        expected_status="reserved",
        new_status="succeeded",
        result_reference={"kind": "response", "payload": dict(payload)},
    ):
        raise ReviewRouteError("review command receipt completion failed")


def _context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(runtime, FoundationRuntime):
        raise ReviewRouteError("Foundation runtime is not configured")
    if not isinstance(actor, RequestActor):
        raise AuthorizationError("authenticated request actor is required")
    return runtime, actor


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"code": "request_rejected", "message": message[:2048], "action": None},
        status_code=status,
    )


__all__ = ["ReviewReadHandler", "ReviewRouteHandler", "router"]
