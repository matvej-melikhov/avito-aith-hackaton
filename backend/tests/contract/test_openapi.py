from __future__ import annotations

import re
from collections.abc import Hashable, Mapping
from datetime import date
from typing import Any, cast

from jsonschema import Draft202012Validator
from openapi_spec_validator import validate
from tests.support.contracts import (
    CONTRACT_ROOT,
    external_refs,
    load_json,
    load_openapi,
    operation_index,
    resolve_fragment,
    walk_json,
)

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
TARGET_PATH_PARAMETERS = {
    "invitation": "invitationId",
    "course": "courseId",
    "course_run": "courseRunId",
    "membership": "membershipId",
    "homework": "homeworkId",
    "homework_version": "homeworkVersionId",
    "course_run_homework": "courseRunHomeworkId",
    "submission": "submissionId",
    "review_case": "reviewCaseId",
    "review_iteration": "reviewIterationId",
    "agent_authorization": "agentAuthorizationId",
    "external_delivery": "deliveryId",
}
PROTOCOL_MUTATIONS = {
    "startStepikAuthentication": "oauth-state-issuance",
    "completeStepikAuthentication": "oauth-state",
    "revokeCurrentSession": "current-session-identity",
    "consumeReviewerMagicLink": "invitation-token-and-state",
    "acceptAIReviewEvent": "event-attempt-sequence-fingerprint",
}


def _operations(openapi: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, operation)
        for path, item in openapi["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def _command_targets() -> dict[str, str]:
    command_schema = load_json(CONTRACT_ROOT / "command.schema.json")
    variants = command_schema["$defs"]["command_variant"]["oneOf"]
    return {
        item["properties"]["command_name"]["const"]: item["properties"]["revision_target"]["const"]
        for item in variants
    }


def _json_compatible(value: object) -> object:
    """Convert YAML timestamp scalars to their JSON/OpenAPI string representation."""

    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_compatible(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_compatible(child) for child in value]
    return value


def test_openapi_has_exact_path_operation_and_rest_command_sets() -> None:
    openapi = load_openapi()
    manifest = load_json(CONTRACT_ROOT / "manifest.json")
    operations = _operations(openapi)
    command_targets = _command_targets()
    mutations = {
        operation["x-command-name"]
        for _, _, operation in operations
        if "x-command-name" in operation
    }

    assert openapi["openapi"] == "3.1.0"
    assert openapi["info"]["version"] == "1.1.0"
    assert openapi["x-contract-status"] == manifest["status"]
    assert len(openapi["paths"]) == 43
    assert len(operations) == 46
    assert len(operation_index(openapi)) == 46
    assert len(mutations) == 26
    assert mutations == set(command_targets) - {"activate_bootstrap", "recover_methodologist"}
    validate(cast(Mapping[Hashable, Any], openapi))


def test_every_rest_mutation_references_its_exact_command_variant() -> None:
    openapi = load_openapi()
    command_schema = load_json(CONTRACT_ROOT / "command.schema.json")
    command_targets = _command_targets()

    for _method, path, operation in _operations(openapi):
        command_name = operation.get("x-command-name")
        if command_name is None:
            continue
        schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert schema_ref == f"command.schema.json#/$defs/{command_name}"
        resolve_fragment(command_schema, "#" + schema_ref.split("#", maxsplit=1)[1])

        path_parameters = set(re.findall(r"\{([^}]+)\}", path))
        target = command_targets[command_name]
        if path_parameters:
            assert path_parameters == {TARGET_PATH_PARAMETERS[target]}, (path, command_name, target)
        assert operation["responses"]["409"]["$ref"] == "#/components/responses/Conflict"


def test_non_command_protocol_mutations_have_closed_replay_protection() -> None:
    operations = operation_index(load_openapi())

    for operation_id, replay_protection in PROTOCOL_MUTATIONS.items():
        operation = operations[operation_id][2]
        assert "x-command-name" not in operation
        assert operation["x-mutation-kind"] == "protocol"
        assert operation["x-replay-protection"] == replay_protection

    declared_protocols = {
        operation_id
        for operation_id, (_method, _path, operation) in operations.items()
        if operation.get("x-mutation-kind") == "protocol"
    }
    assert declared_protocols == set(PROTOCOL_MUTATIONS)


def test_json_responses_are_typed_and_conflicts_share_one_error_contract() -> None:
    openapi = load_openapi()

    for _method, _path, operation in _operations(openapi):
        assert operation["responses"]
        for status, response in operation["responses"].items():
            if "$ref" in response:
                assert response["$ref"] == "#/components/responses/Conflict"
                assert status == "409"
                continue
            if "content" not in response:
                assert status in {"204", "302"} or (
                    status == "202" and operation["operationId"] == "acceptAIReviewEvent"
                ), (operation["operationId"], status)
            for media_type, media in response.get("content", {}).items():
                assert media_type in {"application/json", "application/octet-stream"}
                assert "schema" in media

    conflict = openapi["components"]["responses"]["Conflict"]
    assert conflict["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/ErrorObject"
    )
    error_schema = openapi["components"]["schemas"]["ErrorObject"]
    assert error_schema["additionalProperties"] is False
    assert set(error_schema["required"]) == {"code", "message", "action"}


def test_component_examples_validate_against_their_declared_result_types() -> None:
    openapi = load_openapi()
    example_types = {
        "AvailableArtifact": "ArtifactCapability",
        "PendingPublicationRequest": "PublicationRequest",
        "RecoverableOperation": "Operation",
    }
    assert set(openapi["components"]["examples"]) == set(example_types)

    for example_name, schema_name in example_types.items():
        wrapper = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{schema_name}",
            "components": openapi["components"],
        }
        Draft202012Validator(wrapper).validate(
            _json_compatible(openapi["components"]["examples"][example_name]["value"])
        )


def test_all_external_references_resolve_to_checked_in_contract_fragments() -> None:
    openapi = load_openapi()
    references = set(external_refs(openapi))

    assert references
    for reference in references:
        relative_path, separator, fragment = reference.partition("#")
        target_path = (CONTRACT_ROOT / relative_path).resolve()
        assert target_path.is_file(), reference
        if target_path.suffix == ".json":
            target = load_json(target_path)
            resolve_fragment(target, f"#{fragment}" if separator else "#")

    for node in walk_json(openapi):
        if isinstance(node, dict) and isinstance(node.get("$ref"), str):
            reference = node["$ref"]
            if reference.startswith("#"):
                resolve_fragment(openapi, reference)


def test_openapi_does_not_expose_operator_bootstrap_or_recovery_routes() -> None:
    openapi_text = (CONTRACT_ROOT / "openapi.yaml").read_text(encoding="utf-8")

    assert "activate_bootstrap" not in openapi_text
    assert "recover_methodologist" not in openapi_text
    assert all(
        "bootstrap" not in path and "recover-methodologist" not in path
        for path in load_openapi()["paths"]
    )
