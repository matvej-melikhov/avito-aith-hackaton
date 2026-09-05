"""Frozen REST/MCP parity contract for the twelve agent tools."""

from __future__ import annotations

import re
from importlib import import_module
from importlib.util import find_spec
from typing import Any, NamedTuple

from fastapi.routing import APIRoute
from jsonschema import Draft202012Validator
from tests.support.contracts import (
    CONTRACT_ROOT,
    load_json,
    load_openapi,
    operation_index,
    resolve_fragment,
)

from review_platform.api.routes import build_api_router


class ToolContract(NamedTuple):
    operation_id: str
    method: str
    path: str
    mode: str
    roles: tuple[str, ...]
    scope: str
    input_ref: str | None
    output_ref: str
    revision_target: str | None


TOOLS: dict[str, ToolContract] = {
    "list_courses": ToolContract(
        "listCourses",
        "get",
        "/v1/courses",
        "read",
        ("reviewer", "methodologist"),
        "courses:read",
        None,
        "openapi.yaml#/components/schemas/CourseList",
        None,
    ),
    "select_reviewer_courses": ToolContract(
        "setReviewerCourseSelection",
        "post",
        "/v1/reviewer/course-selections",
        "write",
        ("reviewer",),
        "review_preferences:write",
        "command.schema.json#/$defs/set_reviewer_course_selection",
        "#/$defs/ack",
        "membership",
    ),
    "set_availability": ToolContract(
        "setReviewerAvailability",
        "put",
        "/v1/reviewer/availability",
        "write",
        ("reviewer",),
        "review_preferences:write",
        "command.schema.json#/$defs/set_reviewer_availability",
        "#/$defs/ack",
        "membership",
    ),
    "recommend_next_review": ToolContract(
        "recommendNextReview",
        "get",
        "/v1/review-queue/next",
        "read",
        ("reviewer",),
        "review_queue:read",
        None,
        "#/$defs/recommendation",
        None,
    ),
    "get_review_iteration": ToolContract(
        "getReviewIteration",
        "get",
        "/v1/review-iterations/{reviewIterationId}",
        "read",
        ("reviewer", "methodologist"),
        "reviews:read",
        None,
        "openapi.yaml#/components/schemas/ReviewDetail",
        None,
    ),
    "open_review_iteration": ToolContract(
        "openReviewIteration",
        "post",
        "/v1/review-cases/{reviewCaseId}/iterations",
        "write",
        ("reviewer", "methodologist"),
        "reviews:write",
        "command.schema.json#/$defs/open_review_iteration",
        "openapi.yaml#/components/schemas/ReviewIterationCreated",
        "review_case",
    ),
    "record_review_responsibility": ToolContract(
        "recordReviewResponsibility",
        "post",
        "/v1/review-iterations/{reviewIterationId}/responsibility-events",
        "write",
        ("reviewer", "methodologist"),
        "reviews:write",
        "command.schema.json#/$defs/record_review_responsibility",
        "#/$defs/ack",
        "review_iteration",
    ),
    "save_review_revision": ToolContract(
        "saveReviewRevision",
        "post",
        "/v1/review-iterations/{reviewIterationId}/revisions",
        "write",
        ("reviewer", "methodologist"),
        "reviews:write",
        "command.schema.json#/$defs/save_review_revision",
        "openapi.yaml#/components/schemas/ReviewRevisionSaved",
        "review_iteration",
    ),
    "start_ai_review": ToolContract(
        "startAIReview",
        "post",
        "/v1/review-iterations/{reviewIterationId}/ai-review",
        "write",
        ("reviewer", "methodologist"),
        "ai_reviews:start",
        "command.schema.json#/$defs/start_ai_review",
        "openapi.yaml#/components/schemas/Operation",
        "review_iteration",
    ),
    "get_operation": ToolContract(
        "getOperation",
        "get",
        "/v1/operations/{operationId}",
        "read",
        ("reviewer", "methodologist"),
        "operations:read",
        None,
        "openapi.yaml#/components/schemas/Operation",
        None,
    ),
    "request_review_publication": ToolContract(
        "requestReviewPublication",
        "post",
        "/v1/review-iterations/{reviewIterationId}/publication-requests",
        "write",
        ("reviewer", "methodologist"),
        "publication_requests:write",
        "command.schema.json#/$defs/request_review_publication",
        "openapi.yaml#/components/schemas/PublicationRequest",
        "review_iteration",
    ),
    "get_homework_history": ToolContract(
        "getHomeworkHistory",
        "get",
        "/v1/homeworks/{homeworkId}",
        "read",
        ("reviewer", "methodologist"),
        "courses:read",
        None,
        "openapi.yaml#/components/schemas/HomeworkHistory",
        None,
    ),
}

READ_HANDLER_NAMES = {
    "list_courses",
    "recommend_next_review",
    "get_review_iteration",
    "get_operation",
    "get_homework_history",
}
REVIEW_HANDLER_NAMES = {
    "select_reviewer_courses",
    "set_availability",
    "open_review_iteration",
    "record_review_responsibility",
    "save_review_revision",
}
AI_PUBLICATION_HANDLER_NAMES = {
    "start_ai_review",
    "request_review_publication",
}


def _manifest() -> dict[str, Any]:
    return load_json(CONTRACT_ROOT / "mcp-tools.json")


def _manifest_tools() -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in _manifest()["tools"]}


def _resolve_schema(reference: str) -> dict[str, Any]:
    file_name, _, fragment = reference.partition("#")
    if file_name == "openapi.yaml":
        document = load_openapi()
    elif file_name:
        document = load_json(CONTRACT_ROOT / file_name)
    else:
        document = _manifest()
    resolved = resolve_fragment(document, f"#{fragment}")
    assert isinstance(resolved, dict)
    return resolved


def _normalize_path_parameters(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_all_twelve_tools_have_exact_handler_role_scope_and_rest_mapping() -> None:
    manifest_tools = _manifest_tools()
    rest_operations = operation_index(load_openapi())
    rest_routes = {
        route.operation_id: route
        for route in build_api_router().routes
        if isinstance(route, APIRoute) and route.operation_id is not None
    }

    assert set(manifest_tools) == set(TOOLS)
    for name, expected in TOOLS.items():
        tool = manifest_tools[name]
        assert tool["restOperationId"] == expected.operation_id
        assert tool["mode"] == expected.mode
        assert tuple(tool["requiredRoles"]) == expected.roles
        assert tool["requiredScopes"] == [expected.scope]
        assert tool["outputSchema"] == {"$ref": expected.output_ref}

        method, path, operation = rest_operations[expected.operation_id]
        assert (method, path) == (expected.method, expected.path)
        route = rest_routes[expected.operation_id]
        assert _normalize_path_parameters(route.path.removeprefix("/api")) == (
            _normalize_path_parameters(expected.path)
        )
        assert expected.method.upper() in route.methods
        assert callable(route.endpoint)
        assert route.endpoint.__module__.startswith("review_platform.api.routes.")

        if expected.input_ref is None:
            assert "$ref" not in tool["inputSchema"]
            assert tool["inputSchema"]["additionalProperties"] is False
        else:
            assert tool["inputSchema"] == {"$ref": expected.input_ref}
            command_name = expected.input_ref.rsplit("/", maxsplit=1)[-1]
            assert operation["x-command-name"] == command_name


def test_course_run_discovery_and_recommend_open_get_flow_are_complete() -> None:
    tools = _manifest_tools()
    course_list = _resolve_schema(tools["list_courses"]["outputSchema"]["$ref"])

    assert {"items", "course_runs"} <= set(course_list["required"])
    assert course_list["properties"]["course_runs"]["items"] == {
        "$ref": "#/components/schemas/CourseRun"
    }
    assert [
        tools[name]["restOperationId"]
        for name in (
            "recommend_next_review",
            "open_review_iteration",
            "get_review_iteration",
        )
    ] == ["recommendNextReview", "openReviewIteration", "getReviewIteration"]
    recommendation = _resolve_schema(tools["recommend_next_review"]["outputSchema"]["$ref"])
    assert {
        "review_case_id",
        "review_case_revision",
        "submission_version_id",
        "reason",
    } <= set(recommendation["required"])


def test_write_tools_share_exact_command_cas_and_idempotency_boundary() -> None:
    command_schema = load_json(CONTRACT_ROOT / "command.schema.json")
    command_core = command_schema["$defs"]["command_core"]

    assert {
        "request_id",
        "idempotency_key",
        "command_name",
        "revision_target",
        "target_id",
        "expected_revision",
        "payload",
    } == set(command_core["required"])
    assert command_core["properties"]["expected_revision"]["minimum"] == 0
    assert command_core["properties"]["idempotency_key"] == {
        "type": "string",
        "minLength": 16,
        "maxLength": 128,
    }

    for expected in TOOLS.values():
        if expected.mode != "write":
            continue
        assert expected.input_ref is not None
        command_name = expected.input_ref.rsplit("/", maxsplit=1)[-1]
        command = _resolve_schema(expected.input_ref)
        assert command["allOf"][0] == {"$ref": "#/$defs/wire"}
        variant = command["allOf"][1]["properties"]
        assert variant["command_name"] == {"const": command_name}
        wire_variant = next(
            item["properties"]
            for item in command_schema["$defs"]["command_variant"]["oneOf"]
            if item["properties"]["command_name"] == {"const": command_name}
        )
        assert wire_variant["revision_target"] == {"const": expected.revision_target}


def test_outputs_and_audit_are_typed_for_every_transport() -> None:
    manifest = _manifest()

    for tool in manifest["tools"]:
        Draft202012Validator.check_schema(_resolve_schema(tool["outputSchema"]["$ref"]))
    assert set(manifest["audit"]["required"]) == {
        "request_id",
        "trace_id",
        "tool_name",
        "actor_user_id",
        "agent_id",
        "agent_authorization_id",
        "outcome",
        "duration_ms",
        "idempotency_disposition",
    }


def test_ai_can_start_and_request_publication_but_cannot_publish() -> None:
    manifest = _manifest()
    tools = _manifest_tools()

    assert tools["start_ai_review"]["requiredScopes"] == ["ai_reviews:start"]
    assert tools["request_review_publication"]["requiredScopes"] == ["publication_requests:write"]
    assert manifest["rules"]["directPublicationAllowed"] is False
    assert "publish_review" not in tools
    assert "publishReview" not in {tool["restOperationId"] for tool in manifest["tools"]}


def test_concrete_server_binds_each_manifest_tool_to_one_application_handler() -> None:
    package_spec = find_spec("review_platform.mcp")
    assert package_spec is not None, (
        "T155-T159 missing: review_platform.mcp server and tool bindings are not implemented"
    )

    expected_modules = {
        "review_platform.mcp.tools.read": READ_HANDLER_NAMES,
        "review_platform.mcp.tools.review": REVIEW_HANDLER_NAMES,
        "review_platform.mcp.tools.ai_publication": AI_PUBLICATION_HANDLER_NAMES,
    }
    assert find_spec("review_platform.mcp.server") is not None
    assert find_spec("review_platform.mcp.tools") is not None
    for module_name, handler_names in expected_modules.items():
        assert find_spec(module_name) is not None
        module = import_module(module_name)
        for handler_name in handler_names:
            assert callable(getattr(module, handler_name, None)), (
                f"{module_name} must bind {handler_name}"
            )

    tools_package = import_module("review_platform.mcp.tools")
    registered_names = getattr(tools_package, "REGISTERED_TOOL_NAMES", None)
    assert registered_names is not None, (
        "review_platform.mcp.tools must expose REGISTERED_TOOL_NAMES for registry parity"
    )
    assert set(registered_names) == set(TOOLS)

    server_module = import_module("review_platform.mcp.server")
    assert callable(getattr(server_module, "build_mcp_server", None))


def test_manifest_declares_server_owned_agent_context_and_closed_scopes() -> None:
    manifest = _manifest()
    command_schema = load_json(CONTRACT_ROOT / "command.schema.json")

    assert manifest["rules"]["actorFromAuthorization"] is True
    assert manifest["rules"]["organizationFromAuthorization"] is True
    assert manifest["rules"]["sameApplicationCommandsAsRest"] is True
    assert manifest["rules"]["wireCommandSchema"] == "command.schema.json#/$defs/wire"
    assert manifest["rules"]["unknownScopesRejected"] is True
    assert {contract.scope for contract in TOOLS.values()} == set(
        command_schema["$defs"]["agent_scope"]["enum"]
    )
