"""HTTP safety middleware and the shared sanitized sink adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from review_platform.application.audit import SanitizedValue, sanitize_shared_value


class Redactor:
    """One shared fail-closed redaction boundary for logs and persisted errors."""

    _KNOWN_SINKS = frozenset(
        {"log", "api_error", "operation_attempt", "outbox_message", "audit_event"}
    )

    def sanitize(
        self,
        *,
        sink: str,
        organization_id: str,
        details: Mapping[str, object],
    ) -> dict[str, SanitizedValue]:
        if sink not in self._KNOWN_SINKS:
            raise ValueError(f"unknown shared sink: {sink}")
        if not organization_id:
            raise ValueError("organization identity is required")
        return sanitize_shared_value(details)


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject declared oversized request bodies before application parsing."""

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        super().__init__(app)
        if max_bytes < 1:
            raise ValueError("request body limit must be positive")
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                return _error_response(400, "invalid_content_length", "Invalid request length")
            effective_limit = 13_500_000 if request.url.path == "/api/v2/uploads" else self._max_bytes
            if content_length > effective_limit:
                return _error_response(413, "request_too_large", "Request body is too large")
        return await call_next(request)


class SanitizedExceptionMiddleware(BaseHTTPMiddleware):
    """Keep unexpected exception text and provider bodies out of HTTP responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception:
            return _error_response(500, "internal_error", "The request could not be completed")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "action": None},
    )


__all__ = ["Redactor", "RequestBodyLimitMiddleware", "SanitizedExceptionMiddleware"]
