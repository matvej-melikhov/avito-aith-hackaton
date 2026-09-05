from __future__ import annotations

from copy import deepcopy
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.support.contracts import CONTRACT_ROOT, load_json

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewStartService,
)
from review_platform.mcp.server import MappingToolDispatcher, build_mcp_server
from review_platform.mcp.tools import (
    REGISTERED_TOOL_NAMES,
    TOOL_SPECS,
    MCPToolRegistryError,
    build_mcp_tool_registry,
    validate_frozen_mcp_manifest,
)

ORG = UUID("00000000-0000-7000-8000-000000194001")
BINDING = AIComponentCredentialBinding(
    organization_id=ORG,
    credential_binding_id=UUID("00000000-0000-7000-8000-000000194002"),
    credential_binding_version=3,
)


def _unused_ai_factory(_transaction: AsyncSession) -> AIReviewStartService:
    raise AssertionError("registry assembly must not instantiate the AI service")


def test_frozen_manifest_metadata_matches_registry_contract_exactly() -> None:
    manifest = load_json(CONTRACT_ROOT / "mcp-tools.json")

    validate_frozen_mcp_manifest(manifest)

    assert tuple(TOOL_SPECS) == REGISTERED_TOOL_NAMES
    assert len(REGISTERED_TOOL_NAMES) == len(set(REGISTERED_TOOL_NAMES)) == 12
    assert "publish_review" not in REGISTERED_TOOL_NAMES
    assert {spec.mode for spec in TOOL_SPECS.values()} == {"read", "write"}


@pytest.mark.anyio
async def test_registry_composes_all_t156_t158_handlers_in_manifest_order(
    foundation_runtime: FoundationRuntime,
) -> None:
    registry = build_mcp_tool_registry(
        foundation_runtime,
        credential_binding=BINDING,
        ai_review_start_service_factory=_unused_ai_factory,
    )

    assert tuple(registry) == REGISTERED_TOOL_NAMES
    assert all(callable(registry[name]) for name in REGISTERED_TOOL_NAMES)
    with pytest.raises(TypeError):
        cast(dict[str, object], registry)["publish_review"] = object()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("mode", "admin"),
        ("requiredRoles", ["student"]),
        ("requiredScopes", ["reviews:write"]),
        ("outputSchema", {"$ref": "#/wrong"}),
    ],
)
def test_manifest_metadata_drift_is_rejected(field: str, replacement: object) -> None:
    manifest = deepcopy(load_json(CONTRACT_ROOT / "mcp-tools.json"))
    manifest["tools"][0][field] = replacement

    with pytest.raises(MCPToolRegistryError, match="metadata mismatched"):
        validate_frozen_mcp_manifest(manifest)


def test_manifest_version_order_and_direct_publication_are_fail_closed() -> None:
    for mutation in ("version", "order", "publication"):
        manifest = deepcopy(load_json(CONTRACT_ROOT / "mcp-tools.json"))
        if mutation == "version":
            manifest["contractVersion"] = "1.0.0"
        elif mutation == "order":
            manifest["tools"][0], manifest["tools"][1] = (
                manifest["tools"][1],
                manifest["tools"][0],
            )
        else:
            manifest["rules"]["directPublicationAllowed"] = True
        with pytest.raises(MCPToolRegistryError):
            validate_frozen_mcp_manifest(manifest)


@pytest.mark.anyio
async def test_t155_default_is_lazy_full_registry_and_di_override_remains_independent(
    foundation_runtime: FoundationRuntime,
) -> None:
    server = build_mcp_server(runtime=foundation_runtime)
    dispatcher = cast(MappingToolDispatcher, server.dispatcher)
    assert isinstance(dispatcher, MappingToolDispatcher)
    assert tuple(dispatcher.tools) == REGISTERED_TOOL_NAMES

    explicit = MappingToolDispatcher({"injected_tool": _InjectedHandler()})
    overridden = build_mcp_server(
        runtime=foundation_runtime,
        dispatcher=explicit,
        credential_binding=BINDING,
    )
    assert overridden.dispatcher is explicit
    with pytest.raises(ValueError, match="invalid name"):
        build_mcp_server(runtime=foundation_runtime, tool_registry={})


@pytest.mark.anyio
async def test_partial_ai_composition_is_rejected(
    foundation_runtime: FoundationRuntime,
) -> None:
    with pytest.raises(MCPToolRegistryError, match="binding and service factory"):
        build_mcp_tool_registry(
            foundation_runtime,
            credential_binding=BINDING,
        )


class _InjectedHandler:
    async def __call__(
        self,
        *,
        actor: object,
        arguments: object,
        transaction: object,
    ) -> dict[str, bool]:
        del actor, arguments, transaction
        return {"ok": True}
