"""Tenant-scoped observable operation history."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor

router = APIRouter(tags=["operations"])


@router.get("/v1/operations/{operation_id}", operation_id="getOperation")
async def get_operation(
    operation_id: str,
    request: Request,
) -> dict[str, Any]:
    runtime = _runtime(request)
    actor = _request_actor(request)
    operation = await runtime.get_operation(
        organization_id=str(actor.organization_id),
        operation_id=operation_id,
    )
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "operation_not_found",
                "message": "Operation not found",
                "action": None,
            },
        )
    return asdict(operation)


def _runtime(request: Request) -> FoundationRuntime:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    if not isinstance(runtime, FoundationRuntime):
        raise RuntimeError("Foundation runtime is not configured")
    return runtime


def _request_actor(request: Request) -> RequestActor:
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(actor, RequestActor):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "authentication_required",
                "message": "Authentication required",
                "action": None,
            },
        )
    return actor


__all__ = ["router"]
