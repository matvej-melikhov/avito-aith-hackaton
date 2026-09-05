"""Exact frozen registry for all twelve MCP 2026-07-28 tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import AgentScope, Role
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewStartError,
    AIReviewStartService,
)
from review_platform.mcp.server import MCPToolHandler
from review_platform.mcp.tools.ai_publication import (
    AI_PUBLICATION_TOOL_NAMES,
    AIReviewStartServiceFactory,
    build_ai_publication_tool_registry,
)
from review_platform.mcp.tools.read import READ_TOOL_NAMES, build_read_tool_registry
from review_platform.mcp.tools.review import REVIEW_TOOL_NAMES, build_review_tool_registry

type ToolMode = Literal["read", "write"]


class MCPToolRegistryError(RuntimeError):
    """The composed registry differs from the frozen MCP contract."""


@dataclass(frozen=True, slots=True)
class MCPToolSpec:
    mode: ToolMode
    roles: tuple[Role, ...]
    scopes: tuple[AgentScope, ...]
    output_schema_ref: str


REGISTERED_TOOL_NAMES = (
    "list_courses",
    "select_reviewer_courses",
    "set_availability",
    "recommend_next_review",
    "get_review_iteration",
    "open_review_iteration",
    "record_review_responsibility",
    "save_review_revision",
    "start_ai_review",
    "get_operation",
    "request_review_publication",
    "get_homework_history",
)

TOOL_SPECS: Mapping[str, MCPToolSpec] = MappingProxyType(
    {
        "list_courses": MCPToolSpec(
            "read",
            ("reviewer", "methodologist"),
            ("courses:read",),
            "openapi.yaml#/components/schemas/CourseList",
        ),
        "select_reviewer_courses": MCPToolSpec(
            "write",
            ("reviewer",),
            ("review_preferences:write",),
            "#/$defs/ack",
        ),
        "set_availability": MCPToolSpec(
            "write",
            ("reviewer",),
            ("review_preferences:write",),
            "#/$defs/ack",
        ),
        "recommend_next_review": MCPToolSpec(
            "read",
            ("reviewer",),
            ("review_queue:read",),
            "#/$defs/recommendation",
        ),
        "get_review_iteration": MCPToolSpec(
            "read",
            ("reviewer", "methodologist"),
            ("reviews:read",),
            "openapi.yaml#/components/schemas/ReviewDetail",
        ),
        "open_review_iteration": MCPToolSpec(
            "write",
            ("reviewer", "methodologist"),
            ("reviews:write",),
            "openapi.yaml#/components/schemas/ReviewIterationCreated",
        ),
        "record_review_responsibility": MCPToolSpec(
            "write",
            ("reviewer", "methodologist"),
            ("reviews:write",),
            "#/$defs/ack",
        ),
        "save_review_revision": MCPToolSpec(
            "write",
            ("reviewer", "methodologist"),
            ("reviews:write",),
            "openapi.yaml#/components/schemas/ReviewRevisionSaved",
        ),
        "start_ai_review": MCPToolSpec(
            "write",
            ("reviewer", "methodologist"),
            ("ai_reviews:start",),
            "openapi.yaml#/components/schemas/Operation",
        ),
        "get_operation": MCPToolSpec(
            "read",
            ("reviewer", "methodologist"),
            ("operations:read",),
            "openapi.yaml#/components/schemas/Operation",
        ),
        "request_review_publication": MCPToolSpec(
            "write",
            ("reviewer", "methodologist"),
            ("publication_requests:write",),
            "openapi.yaml#/components/schemas/PublicationRequest",
        ),
        "get_homework_history": MCPToolSpec(
            "read",
            ("reviewer", "methodologist"),
            ("courses:read",),
            "openapi.yaml#/components/schemas/HomeworkHistory",
        ),
    }
)


def build_mcp_tool_registry(
    runtime: FoundationRuntime,
    *,
    credential_binding: AIComponentCredentialBinding | None = None,
    ai_review_start_service_factory: AIReviewStartServiceFactory | None = None,
) -> Mapping[str, MCPToolHandler]:
    """Compose all T156-T158 handlers and fail closed on drift or overlap."""

    binding, ai_factory = _ai_composition(
        credential_binding,
        ai_review_start_service_factory,
    )
    registries = (
        build_read_tool_registry(runtime),
        build_review_tool_registry(runtime),
        build_ai_publication_tool_registry(
            runtime,
            credential_binding=binding,
            ai_review_start_service_factory=ai_factory,
        ),
    )
    combined: dict[str, MCPToolHandler] = {}
    for registry in registries:
        overlap = set(combined).intersection(registry)
        if overlap:
            raise MCPToolRegistryError(
                f"duplicate MCP tool binding(s): {sorted(overlap)!r}"
            )
        combined.update(registry)
    _validate_registry_names(combined)
    ordered = {name: combined[name] for name in REGISTERED_TOOL_NAMES}
    return MappingProxyType(ordered)


def _ai_composition(
    credential_binding: AIComponentCredentialBinding | None,
    ai_review_start_service_factory: AIReviewStartServiceFactory | None,
) -> tuple[AIComponentCredentialBinding, AIReviewStartServiceFactory]:
    if credential_binding is None and ai_review_start_service_factory is None:
        return (
            AIComponentCredentialBinding(
                organization_id=UUID(int=0),
                credential_binding_id=UUID(int=0),
                credential_binding_version=1,
            ),
            cast(AIReviewStartServiceFactory, _unconfigured_ai_start_service),
        )
    if credential_binding is None or ai_review_start_service_factory is None:
        raise MCPToolRegistryError(
            "complete MCP AI composition requires binding and service factory"
        )
    return credential_binding, ai_review_start_service_factory


def _unconfigured_ai_start_service(_transaction: AsyncSession) -> AIReviewStartService:
    raise AIReviewStartError("MCP AI review start service is not composed")


def validate_frozen_mcp_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate registry metadata against an already loaded frozen manifest."""

    if (
        manifest.get("contractVersion") != "1.1.0"
        or manifest.get("contractStatus") != "frozen"
    ):
        raise MCPToolRegistryError("MCP manifest version or status is unsupported")
    rules = manifest.get("rules")
    if not isinstance(rules, Mapping) or rules.get("directPublicationAllowed") is not False:
        raise MCPToolRegistryError("MCP manifest must prohibit direct publication")
    raw_tools = manifest.get("tools")
    if not isinstance(raw_tools, list) or len(raw_tools) != len(REGISTERED_TOOL_NAMES):
        raise MCPToolRegistryError("MCP manifest must contain exactly twelve tools")
    manifest_names: list[str] = []
    for raw in raw_tools:
        if not isinstance(raw, Mapping):
            raise MCPToolRegistryError("MCP manifest tool entry must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or name not in TOOL_SPECS:
            raise MCPToolRegistryError("MCP manifest contains an unknown tool")
        manifest_names.append(name)
        spec = TOOL_SPECS[name]
        roles = _string_tuple(raw, "requiredRoles")
        scopes = _string_tuple(raw, "requiredScopes")
        if (
            raw.get("mode") != spec.mode
            or roles != spec.roles
            or scopes != spec.scopes
            or raw.get("outputSchema") != {"$ref": spec.output_schema_ref}
        ):
            raise MCPToolRegistryError(f"MCP manifest metadata mismatched for {name}")
    if tuple(manifest_names) != REGISTERED_TOOL_NAMES:
        raise MCPToolRegistryError("MCP manifest tool order or identities mismatched")
    _validate_registry_names(TOOL_SPECS)


def _string_tuple(value: Mapping[str, Any], field: str) -> tuple[str, ...]:
    raw = value.get(field)
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise MCPToolRegistryError(f"MCP manifest {field} must be a string array")
    return tuple(raw)


def _validate_registry_names(registry: Mapping[str, object]) -> None:
    names = tuple(registry)
    if (
        len(names) != 12
        or len(set(names)) != 12
        or set(names) != set(REGISTERED_TOOL_NAMES)
        or "publish_review" in names
    ):
        raise MCPToolRegistryError("MCP registry differs from the frozen twelve-tool set")
    expected_partitions = READ_TOOL_NAMES | REVIEW_TOOL_NAMES | AI_PUBLICATION_TOOL_NAMES
    if set(names) != expected_partitions:
        raise MCPToolRegistryError("T156-T158 MCP tool partitions are incomplete or overlap")


__all__ = [
    "AI_PUBLICATION_TOOL_NAMES",
    "READ_TOOL_NAMES",
    "REGISTERED_TOOL_NAMES",
    "REVIEW_TOOL_NAMES",
    "TOOL_SPECS",
    "MCPToolRegistryError",
    "MCPToolSpec",
    "build_ai_publication_tool_registry",
    "build_mcp_tool_registry",
    "build_read_tool_registry",
    "build_review_tool_registry",
    "validate_frozen_mcp_manifest",
]
