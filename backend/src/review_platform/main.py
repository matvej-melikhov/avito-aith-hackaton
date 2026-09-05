"""FastAPI process entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
import asyncio
import os
import contextlib
import logging
from review_platform.application.workspace.worker import (
    SelfReviewWorker,
    FixtureSelfReviewProvider,
    SelfReviewProvider,
)
from review_platform.application.workspace.exports import ExportWorker
from fastapi import FastAPI

from review_platform.api.middleware import (
    RequestBodyLimitMiddleware,
    SanitizedExceptionMiddleware,
)
from review_platform.api.session_middleware import SessionActorMiddleware
from review_platform.api.upload_limit import UploadBodyLimitMiddleware
from review_platform.api.routes import build_api_router
from review_platform.application.foundation_runtime import (
    FoundationRuntime,
    RuntimeConfigurationError,
    build_foundation_runtime,
)
from review_platform.infrastructure.composition.ai_review import (
    build_sql_ai_review_start_service_factory,
)
from review_platform.mcp.__main__ import (
    build_embedded_mcp_app,
    configured_ai_binding_from_environment,
)
from review_platform.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    runtime: FoundationRuntime | None = None,
    workspace_provider: SelfReviewProvider | None = None,
) -> FastAPI:
    selected = settings or get_settings()
    configuration_error: str | None = None
    if runtime is None:
        try:
            runtime = build_foundation_runtime(selected)
        except RuntimeConfigurationError as error:
            configuration_error = str(error)

    async def workspace_loop() -> None:
        assert runtime is not None
        provider = workspace_provider
        if provider is None and os.environ.get("REVIEW_PLATFORM_WORKSPACE_FIXTURES") == "true":
            if selected.environment not in {"local", "test"}:
                raise RuntimeError("workspace fixtures require a local environment")
            provider = FixtureSelfReviewProvider()
        reviewer = SelfReviewWorker(runtime, provider, runtime.object_storage) if provider else None
        exporter = ExportWorker(runtime)
        while True:
            try:
                if reviewer:
                    await reviewer.tick()
                await exporter.tick()
            except Exception:
                logging.getLogger(__name__).error(
                    "workspace worker iteration failed; durable lease retained"
                )
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        worker = None
        if runtime is not None and os.environ.get("REVIEW_PLATFORM_WORKSPACE_ENABLED") == "true":
            worker = asyncio.create_task(workspace_loop())
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
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
    if runtime is not None:
        app.state.ai_review_start_service_factory = build_sql_ai_review_start_service_factory(
            runtime
        )
        configured_ai = configured_ai_binding_from_environment(required=False)
        if configured_ai is not None:
            app.state.ai_review_credential_binding_id = configured_ai.credential_binding_id
            app.state.ai_review_credential_binding_version = (
                configured_ai.credential_binding_version
            )
    app.add_middleware(SessionActorMiddleware)
    app.add_middleware(SanitizedExceptionMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=selected.command_body_limit_bytes)
    app.add_middleware(UploadBodyLimitMiddleware)
    app.include_router(build_api_router())

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        status = "ok" if runtime is not None else "configuration_required"
        return {"status": status, "contract_version": selected.contract_version}

    @app.get("/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        status = "ready" if runtime is not None else "configuration_required"
        return {"status": status, "contract_version": selected.contract_version}

    if runtime is not None:
        app.mount("", build_embedded_mcp_app(runtime, state=app.state))

    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()


__all__ = ["app", "create_app", "main"]
