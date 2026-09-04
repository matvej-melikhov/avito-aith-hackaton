from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator, ValidationError
from tests.support.contracts import (
    FIXTURE_ROOT,
    load_fixture,
    load_openapi,
    load_schema,
    validate_definition,
    validator_for,
)

UUID = "00000000-0000-7000-8000-000000000001"
UUID_2 = "00000000-0000-7000-8000-000000000002"
DIGEST = "sha256:" + "0" * 64

FIXTURE_DEFINITIONS: dict[str, tuple[str, dict[str, str]]] = {
    "course-import-v1.1.0.json": (
        "course-import.schema.json",
        {"request": "request", "success_result": "result", "failure_result": "result"},
    ),
    "artifact-provider-v1.1.0.json": (
        "artifact-provider.schema.json",
        {
            "preflight_request": "preflight_request",
            "available_result": "preflight_result",
            "capture_request": "capture_request",
            "success_result": "capture_result",
            "failure_result": "capture_result",
        },
    ),
    "delivery-v1.1.0.json": (
        "delivery.schema.json",
        {
            "request": "deliver_request",
            "reconcile_request": "reconcile_request",
            "reconcile_found_result": "result",
            "reconcile_not_found_result": "result",
            "reconcile_action_required_result": "result",
            "success_result": "result",
            "unknown_result": "result",
        },
    ),
    "email-v1.1.0.json": (
        "email.schema.json",
        {
            "request": "send_request",
            "success_result": "send_result",
            "failure_result": "send_result",
        },
    ),
    "identity-provider-v1.1.0.json": (
        "identity-provider.schema.json",
        {
            "request": "verification_request",
            "success_assertion": "identity_assertion",
            "failure": "failure",
        },
    ),
}


@pytest.mark.parametrize(
    "schema_name",
    [
        "ai-review.schema.json",
        "artifact-provider.schema.json",
        "artifact.schema.json",
        "command.schema.json",
        "course-import.schema.json",
        "delivery.schema.json",
        "email.schema.json",
        "identity-provider.schema.json",
    ],
)
def test_every_json_schema_is_valid_draft_2020_12(schema_name: str) -> None:
    schema = load_schema(schema_name)
    manifest = load_schema("manifest.json")

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-contract-status"] == manifest["status"]
    assert schema["$id"].endswith(":1.1.0")
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("fixture_name", sorted(FIXTURE_DEFINITIONS))
def test_provider_success_and_failure_fixtures_validate(fixture_name: str) -> None:
    schema_name, definitions = FIXTURE_DEFINITIONS[fixture_name]
    schema = load_schema(schema_name)
    fixture = load_fixture(fixture_name)

    assert set(fixture) == set(definitions)
    for fixture_key, definition in definitions.items():
        validate_definition(schema, definition, fixture[fixture_key])


def test_artifact_envelope_is_tenant_scoped_immutable_and_closed() -> None:
    schema = load_schema("artifact.schema.json")
    envelope: dict[str, Any] = {
        "contract_version": "1.1.0",
        "organization_id": UUID,
        "artifact_reference_id": UUID,
        "artifact_version_id": UUID_2,
        "provider": "github",
        "provider_version": "commit:abc",
        "content_digest": DIGEST,
        "captured_at": "2026-09-04T12:00:00Z",
        "object": {
            "key": f"{UUID}/{UUID_2}/artifact.zip",
            "media_type": "application/zip",
            "byte_size": 128,
        },
        "metadata": {"source": "offline-fixture"},
    }
    validator_for(schema).validate(envelope)

    envelope["secret"] = "must not be accepted"
    with pytest.raises(ValidationError):
        validator_for(schema).validate(envelope)


def test_ai_event_vectors_are_typed_closed_and_criterion_complete() -> None:
    schema = load_schema("ai-review.schema.json")
    fixture = load_fixture("ai-events-v1.1.0.json")

    for event in fixture.values():
        validate_definition(schema, "event", event)

    succeeded = fixture["succeeded"]
    expected = succeeded["criterion_coverage"]["expected_criterion_ids"]
    reported = succeeded["criterion_coverage"]["reported_criterion_ids"]
    suggested = [item["criterion_id"] for item in succeeded["suggestions"]]
    assert succeeded["criterion_coverage"]["complete"] is True
    assert expected == reported == suggested
    assert len(suggested) == len(set(suggested))

    failed = fixture["retryable_failed"]
    assert failed["error"]["retryable"] is True
    assert failed["suggestions"] == []

    invalid = deepcopy(succeeded)
    invalid["unexpected"] = True
    with pytest.raises(ValidationError):
        validate_definition(schema, "event", invalid)


def test_ai_not_checked_suggestion_cannot_have_points() -> None:
    schema = load_schema("ai-review.schema.json")
    suggestion = deepcopy(load_fixture("ai-events-v1.1.0.json")["succeeded"]["suggestions"][0])
    suggestion.update({"status": "not_checked", "proposed_points": 1})

    with pytest.raises(ValidationError):
        validate_definition(schema, "suggestion", suggestion)


def test_minimal_ai_event_fields_map_to_review_detail_without_invented_values() -> None:
    ai_defs = load_schema("ai-review.schema.json")["$defs"]
    api_schemas = load_openapi()["components"]["schemas"]

    assert set(api_schemas["AIEvidence"]["required"]) == set(ai_defs["evidence"]["required"])
    assert set(api_schemas["AISuggestion"]["required"]) == {
        "id",
        *ai_defs["suggestion"]["required"],
    }
    assert set(api_schemas["AISignal"]["required"]) == set(ai_defs["aiSignal"]["required"])


def test_ai_fingerprint_golden_vector_uses_rfc_8785_and_sha256() -> None:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    canonical = rfc8785.dumps(vector["input"])
    actual = "sha256:" + hashlib.sha256(canonical).hexdigest()

    assert vector["algorithm"] == "jcs-sha256-v1"
    assert actual == vector["expected_fingerprint"]


def test_ai_request_requires_exact_component_credential_binding_and_artifact_envelope() -> None:
    ai_schema = load_schema("ai-review.schema.json")
    artifact_schema = load_schema("artifact.schema.json")
    request_schema = deepcopy(ai_schema["$defs"]["request"])
    assert request_schema["properties"]["artifact"] == {"$ref": "artifact.schema.json"}
    request_schema["properties"]["artifact"] = artifact_schema
    wrapped = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **request_schema,
        "$defs": ai_schema["$defs"],
    }
    request: dict[str, Any] = {
        "contract_version": "1.1.0",
        "run_id": UUID,
        "input_fingerprint": DIGEST,
        "fingerprint_algorithm": "jcs-sha256-v1",
        "organization_id": UUID,
        "course_run_id": UUID,
        "submission_version_id": UUID,
        "review_iteration_id": UUID,
        "credential_binding_id": UUID_2,
        "credential_binding_version": 3,
        "artifact": {
            "contract_version": "1.1.0",
            "organization_id": UUID,
            "artifact_reference_id": UUID,
            "artifact_version_id": UUID_2,
            "provider": "github",
            "provider_version": "commit:abc",
            "content_digest": DIGEST,
            "captured_at": "2026-09-04T12:00:00Z",
            "object": {"key": "tenant/artifact", "media_type": "text/plain", "byte_size": 1},
        },
        "artifact_download": {
            "url": "https://objects.example.test/signed",
            "expires_at": "2026-09-04T12:15:00Z",
        },
        "homework": {
            "version_id": UUID,
            "title": "Homework",
            "student_text": "Do it",
            "digest": DIGEST,
        },
        "criteria": {
            "set_id": UUID,
            "digest": DIGEST,
            "items": [
                {
                    "id": UUID,
                    "key": "correct",
                    "title": "Correct",
                    "description": "Works",
                    "max_points": 5,
                }
            ],
        },
    }
    validator_for(wrapped).validate(request)

    del request["credential_binding_version"]
    with pytest.raises(ValidationError):
        validator_for(wrapped).validate(request)


def test_every_provider_request_binds_an_exact_credential_version() -> None:
    direct_requests = [
        ("identity-provider.schema.json", "verification_request"),
        ("course-import.schema.json", "request"),
        ("artifact-provider.schema.json", "preflight_request"),
        ("artifact-provider.schema.json", "capture_request"),
        ("delivery.schema.json", "reconcile_request"),
        ("email.schema.json", "send_request"),
        ("ai-review.schema.json", "request"),
    ]
    for schema_name, definition in direct_requests:
        request_schema = load_schema(schema_name)["$defs"][definition]
        required = set(request_schema["required"])
        assert {"credential_binding_id", "credential_binding_version"} <= required

    delivery = load_schema("delivery.schema.json")["$defs"]["deliver_request"]
    destination = delivery["properties"]["destination"]
    assert {"credential_binding_id", "credential_binding_version"} <= set(destination["required"])


def test_delivery_reconciliation_is_typed_and_bound_to_the_original_delivery() -> None:
    schema = load_schema("delivery.schema.json")
    fixture = load_fixture("delivery-v1.1.0.json")
    reconcile = deepcopy(fixture["reconcile_request"])
    validate_definition(schema, "reconcile_request", reconcile)

    for key in (
        "reconcile_found_result",
        "reconcile_not_found_result",
        "reconcile_action_required_result",
    ):
        validate_definition(schema, "result", fixture[key])

    invalid = deepcopy(reconcile)
    invalid["delivery_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        validate_definition(schema, "reconcile_request", invalid)


def test_ai_and_human_score_contracts_declare_nonnegative_contextual_upper_bounds() -> None:
    ai_points = load_schema("ai-review.schema.json")["$defs"]["suggestion"]["properties"][
        "proposed_points"
    ]
    human_points = load_schema("command.schema.json")["$defs"]["review_decision"]["properties"][
        "points"
    ]

    assert ai_points["minimum"] == human_points["minimum"] == 0
    assert "MUST NOT exceed max_points" in ai_points["description"]
    assert "MUST NOT exceed max_points" in human_points["description"]


def test_fixture_directory_contains_only_manifested_non_secret_vectors() -> None:
    expected = {
        "ai-events-v1.1.0.json",
        "ai-fingerprint-v1.1.0.json",
        *FIXTURE_DEFINITIONS,
    }
    assert {path.name for path in FIXTURE_ROOT.glob("*.json")} == expected
    forbidden = {"password", "access_token", "refresh_token", "client_secret", "private_key"}
    for path in FIXTURE_ROOT.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        assert not any(key in text for key in forbidden), path.name
