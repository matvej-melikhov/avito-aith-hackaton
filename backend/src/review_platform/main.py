"""FastAPI process entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from review_platform.api.middleware import (
    RequestBodyLimitMiddleware,
    SanitizedExceptionMiddleware,
)
from review_platform.api.routes import build_api_router
from review_platform.application.foundation_runtime import (
    FoundationRuntime,
    RuntimeConfigurationError,
    build_foundation_runtime,
)
from review_platform.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    runtime: FoundationRuntime | None = None,
) -> FastAPI:
    selected = settings or get_settings()
    configuration_error: str | None = None
    if runtime is None:
        try:
            runtime = build_foundation_runtime(selected)
        except RuntimeConfigurationError as error:
            configuration_error = str(error)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if runtime is not None:
                await runtime.close()

    app = FastAPI(
        title="Review Platform Backend Core",
        version=selected.contract_version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.foundation_runtime = runtime
    app.state.worker_auth_revalidator = (
        runtime.worker_auth_revalidator if runtime is not None else None
    )
    app.state.configuration_error = configuration_error
    app.add_middleware(SanitizedExceptionMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=selected.command_body_limit_bytes)
    app.include_router(build_api_router())

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        status = "ok" if runtime is not None else "configuration_required"
        return {"status": status, "contract_version": selected.contract_version}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        status = "ready" if runtime is not None else "configuration_required"
        return {"status": status, "contract_version": selected.contract_version}

    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()


__all__ = ["app", "create_app", "main"]
