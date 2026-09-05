"""Standalone and embedded composition for the stateless MCP service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import uvicorn
from fastapi import FastAPI

from review_platform.application.foundation_runtime import (
    FoundationRuntime,
    build_foundation_runtime,
)
from review_platform.application.request_context import RequestActor
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
)
from review_platform.infrastructure.composition.ai_review import (
    build_sql_ai_review_start_service_factory,
)
from review_platform.mcp.server import (
    MappingToolDispatcher,
    MCPServerError,
    create_mcp_app,
)
from review_platform.mcp.tools import build_mcp_tool_registry
from review_platform.mcp.tools.ai_publication import AIReviewStartServiceFactory
from review_platform.settings import Settings, get_settings

_CREDENTIAL_ID_ENV = "REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_ID"
_CREDENTIAL_VERSION_ENV = "REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_VERSION"


@dataclass(frozen=True, slots=True)
class ConfiguredAIBinding:
    credential_binding_id: UUID
    credential_binding_version: int

    def __post_init__(self) -> None:
        if self.credential_binding_version < 1:
            raise ValueError("AI credential binding version must be positive")


class DynamicMCPToolDispatcher:
    """Resolve configured binding after bearer authentication, never from input."""

    def __init__(
        self,
        runtime: FoundationRuntime,
        *,
        state: object,
        default_binding: ConfiguredAIBinding | None = None,
    ) -> None:
        self._runtime = runtime
        self._state = state
        self._default_binding = default_binding
        self._ai_factory = build_sql_ai_review_start_service_factory(runtime)

    async def dispatch(
        self,
        *,
        name: str,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]:
        configured = self._state_binding() or self._default_binding
        if configured is None:
            registry = build_mcp_tool_registry(self._runtime)
        else:
            registry = build_mcp_tool_registry(
                self._runtime,
                credential_binding=AIComponentCredentialBinding(
                    organization_id=actor.organization_id,
                    credential_binding_id=configured.credential_binding_id,
                    credential_binding_version=configured.credential_binding_version,
                ),
                ai_review_start_service_factory=cast(
                    AIReviewStartServiceFactory,
                    self._ai_factory,
                ),
            )
        return dict(
            await MappingToolDispatcher(registry).dispatch(
                name=name,
                actor=actor,
                arguments=arguments,
                transaction=transaction,
            )
        )

    def _state_binding(self) -> ConfiguredAIBinding | None:
        identity = getattr(self._state, "ai_review_credential_binding_id", None)
        version = getattr(self._state, "ai_review_credential_binding_version", None)
        if identity is None and version is None:
            return None
        if (
            not isinstance(identity, UUID)
            or not isinstance(version, int)
            or isinstance(version, bool)
            or version < 1
        ):
            raise MCPServerError(
                500,
                "mcp_ai_composition_invalid",
                "configured AI credential binding is invalid",
                action="contact_operator",
            )
        return ConfiguredAIBinding(identity, version)


def build_embedded_mcp_app(
    runtime: FoundationRuntime,
    *,
    state: object,
) -> FastAPI:
    return create_mcp_app(
        runtime=runtime,
        dispatcher=DynamicMCPToolDispatcher(runtime, state=state),
    )


def build_standalone_mcp_app(
    settings: Settings | None = None,
    *,
    runtime: FoundationRuntime | None = None,
    configured_binding: ConfiguredAIBinding | None = None,
) -> FastAPI:
    selected = settings or get_settings()
    selected_runtime = runtime or build_foundation_runtime(selected)
    binding = configured_binding or configured_ai_binding_from_environment(required=True)
    app = create_mcp_app(
        selected,
        runtime=selected_runtime,
        dispatcher=DynamicMCPToolDispatcher(
            selected_runtime,
            state=object(),
            default_binding=binding,
        ),
    )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "contract_version": selected.contract_version}

    return app


def configured_ai_binding_from_environment(
    *,
    required: bool,
) -> ConfiguredAIBinding | None:
    raw_identity = os.getenv(_CREDENTIAL_ID_ENV)
    raw_version = os.getenv(_CREDENTIAL_VERSION_ENV)
    if raw_identity is None and raw_version is None and not required:
        return None
    if raw_identity is None or raw_version is None:
        raise RuntimeError("both AI review credential binding ID and version must be configured")
    try:
        identity = UUID(raw_identity)
        version = int(raw_version)
    except ValueError as error:
        raise RuntimeError("AI review credential binding configuration is invalid") from error
    try:
        return ConfiguredAIBinding(identity, version)
    except ValueError as error:
        raise RuntimeError(str(error)) from error


def main() -> None:
    uvicorn.run(build_standalone_mcp_app(), host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()


__all__ = [
    "ConfiguredAIBinding",
    "DynamicMCPToolDispatcher",
    "build_embedded_mcp_app",
    "build_standalone_mcp_app",
    "configured_ai_binding_from_environment",
    "main",
]
