"""Resolve HttpOnly sessions into the server-owned actor used by REST routes."""

from __future__ import annotations

import os
from typing import cast
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor, Role
from review_platform.application.services.authentication import (
    AuthenticationError,
    AuthenticationService,
)
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.models.identity import Session


class SessionActorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        token = request.cookies.get("review_session")
        runtime = getattr(request.app.state, "foundation_runtime", None)
        if not token or not isinstance(runtime, FoundationRuntime):
            return await call_next(request)
        origin = request.headers.get("origin")
        if origin and request.method not in {"GET", "HEAD", "OPTIONS"}:
            allowed = set(
                filter(None, os.environ.get("REVIEW_PLATFORM_BROWSER_ORIGINS", "").split(","))
            )
            if origin not in allowed and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(
                    {
                        "code": "origin_mismatch",
                        "message": "Request origin is not allowed",
                        "action": None,
                    },
                    status_code=403,
                )
        try:
            async with runtime.transaction() as transaction:
                organization_id = await transaction.scalar(
                    select(Session.organization_id).where(
                        Session.token_digest == sha256_digest(token)
                    )
                )
                if organization_id is None:
                    raise AuthenticationError("session was not found")
                view = await AuthenticationService(
                    transaction, clock=runtime.clock
                ).current_session(organization_id=organization_id, session_secret=token)
                request.state.request_actor = RequestActor(
                    organization_id=view.organization_id,
                    actor_type="user",
                    user_id=view.user_id,
                    roles=frozenset(cast(tuple[Role, ...], view.roles)),
                    membership_revision=view.membership_revision,
                    auth_epoch=view.auth_epoch,
                )
        except AuthenticationError:
            # Login protocols must be able to replace an expired cookie.
            if request.url.path.startswith("/api/v1/auth/"):
                return await call_next(request)
            response = JSONResponse(
                {
                    "code": "session_expired",
                    "message": "Session is no longer active",
                    "action": None,
                },
                status_code=401,
            )
            response.delete_cookie("review_session", path="/api")
            return response
        return await call_next(request)
