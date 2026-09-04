from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from tests.support.contracts import (
    CONTRACT_ROOT,
    load_json,
    load_openapi,
    operation_index,
    resolve_fragment,
)

EXPECTED_TOOLS = {
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
}


def _manifest() -> dict[str, Any]:
    return load_json(CONTRACT_ROOT / "mcp-tools.json")


def _resolve_schema_ref(manifest: dict[str, Any], reference: str) -> object:
    file_name, separator, fragment = reference.partition("#")
    if not file_name:
        target = manifest
    elif file_name == "openapi.yaml":
        target = load_openapi()
    else:
        target = load_json(CONTRACT_ROOT / file_name)
    return resolve_fragment(target, f"#{fragment}" if separator else "#")


def test_mcp_2026_07_28_manifest_contains_exactly_twelve_tools() -> None:
    manifest = _manifest()
    contract_manifest = load_json(CONTRACT_ROOT / "manifest.json")
    names = [tool["name"] for tool in manifest["tools"]]

    assert manifest["contractVersion"] == "1.1.0"
    assert manifest["contractStatus"] == contract_manifest["status"]
    assert manifest["protocolVersion"] == "2026-07-28"
    assert manifest["transport"] == {
        "kind": "stateless-http",
        "path": "/mcp",
        "requiredHeaders": ["MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"],
        "authorization": "bearer",
    }
    assert len(names) == len(set(names)) == 12
    assert set(names) == EXPECTED_TOOLS


def test_tool_roles_and_scopes_are_closed_and_map_to_real_rest_operations() -> None:
    manifest = _manifest()
    openapi_operations = operation_index(load_openapi())
    command_schema = load_json(CONTRACT_ROOT / "command.schema.json")
    closed_scopes = set(command_schema["$defs"]["agent_scope"]["enum"])

    used_scopes: set[str] = set()
    for tool in manifest["tools"]:
        assert tool["mode"] in {"read", "write"}
        assert 1 <= len(tool["requiredRoles"]) <= 2
        assert set(tool["requiredRoles"]) <= {"reviewer", "methodologist"}
        assert len(tool["requiredScopes"]) == 1
        assert set(tool["requiredScopes"]) <= closed_scopes
        assert tool["restOperationId"] in openapi_operations
        used_scopes.update(tool["requiredScopes"])

    assert used_scopes == closed_scopes
    assert manifest["rules"]["unknownScopesRejected"] is True


def test_every_tool_has_a_closed_typed_input_and_resolvable_output() -> None:
    manifest = _manifest()

    for tool in manifest["tools"]:
        input_schema = tool["inputSchema"]
        if "$ref" in input_schema:
            resolved = _resolve_schema_ref(manifest, input_schema["$ref"])
            assert isinstance(resolved, dict)
            assert input_schema["$ref"].startswith("command.schema.json#/$defs/")
        else:
            Draft202012Validator.check_schema(input_schema)
            assert input_schema["type"] == "object"
            assert input_schema["additionalProperties"] is False

        output_schema = tool["outputSchema"]
        assert "$ref" in output_schema
        resolved_output = _resolve_schema_ref(manifest, output_schema["$ref"])
        assert isinstance(resolved_output, dict)
        Draft202012Validator.check_schema(resolved_output)


def test_recommendation_to_iteration_flow_is_complete() -> None:
    tools = {tool["name"]: tool for tool in _manifest()["tools"]}

    assert tools["recommend_next_review"]["restOperationId"] == "recommendNextReview"
    recommendation = _resolve_schema_ref(
        _manifest(), tools["recommend_next_review"]["outputSchema"]["$ref"]
    )
    assert isinstance(recommendation, dict)
    assert {"review_case_id", "review_case_revision", "submission_version_id"} <= set(
        recommendation["required"]
    )
    assert tools["open_review_iteration"]["restOperationId"] == "openReviewIteration"
    assert tools["get_review_iteration"]["restOperationId"] == "getReviewIteration"


def test_agent_can_request_but_cannot_directly_publish() -> None:
    manifest = _manifest()
    operation_ids = {tool["restOperationId"] for tool in manifest["tools"]}
    names = {tool["name"] for tool in manifest["tools"]}

    assert manifest["rules"]["directPublicationAllowed"] is False
    assert "request_review_publication" in names
    assert "requestReviewPublication" in operation_ids
    assert "publish_review" not in names
    assert "publishReview" not in operation_ids


def test_audit_contract_identifies_user_agent_authorization_and_idempotency() -> None:
    required = set(_manifest()["audit"]["required"])

    assert {
        "request_id",
        "trace_id",
        "tool_name",
        "actor_user_id",
        "agent_id",
        "agent_authorization_id",
        "outcome",
        "duration_ms",
        "idempotency_disposition",
    } == required
