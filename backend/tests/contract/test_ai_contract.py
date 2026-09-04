"""Executable contract for frozen AI request/event models and semantics."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
from copy import deepcopy
from types import ModuleType
from typing import Any, Protocol, cast

import pytest
import rfc8785
from jsonschema import ValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from tests.support.contracts import (
    load_fixture,
    load_openapi,
    load_schema,
    validate_definition,
    validator_for,
)

from review_platform.contracts.registry import ContractRegistry

UUID_1 = "00000000-0000-7000-8000-000000000001"
UUID_2 = "00000000-0000-7000-8000-000000000002"
UUID_3 = "00000000-0000-7000-8000-000000000003"
UUID_4 = "00000000-0000-7000-8000-000000000004"
UUID_5 = "00000000-0000-7000-8000-000000000005"
UUID_6 = "00000000-0000-7000-8000-000000000006"
UUID_7 = "00000000-0000-7000-8000-000000000007"
CRITERION_A = "00000000-0000-7000-8000-000000000063"
CRITERION_B = "00000000-0000-7000-8000-000000000065"
DIGEST_0 = "sha256:" + "0" * 64
DIGEST_1 = "sha256:" + "1" * 64
DIGEST_2 = "sha256:" + "2" * 64


class GeneratedRequestModel(Protocol):
    @classmethod
    def model_validate(cls, value: object) -> BaseModel: ...


class GeneratedEventModel(Protocol):
    @classmethod
    def model_validate(cls, value: object) -> BaseModel: ...


class SemanticValidator(Protocol):
    def __call__(self, event: BaseModel, request: BaseModel) -> BaseModel: ...


def _request() -> dict[str, Any]:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    return {
        "contract_version": "1.1.0",
        "run_id": "00000000-0000-7000-8000-000000000060",
        "input_fingerprint": vector["expected_fingerprint"],
        "fingerprint_algorithm": "jcs-sha256-v1",
        "organization_id": UUID_1,
        "course_run_id": UUID_2,
        "submission_version_id": UUID_3,
        "review_iteration_id": UUID_4,
        "credential_binding_id": "00000000-0000-7000-8000-000000000051",
        "credential_binding_version": 7,
        "artifact": {
            "contract_version": "1.1.0",
            "organization_id": UUID_1,
            "artifact_reference_id": "00000000-0000-7000-8000-000000000008",
            "artifact_version_id": UUID_5,
            "provider": "github",
            "provider_version": "commit:" + "a" * 40,
            "content_digest": DIGEST_0,
            "captured_at": "2026-09-05T11:55:00Z",
            "object": {
                "key": f"{UUID_1}/{UUID_5}/artifact.zip",
                "media_type": "application/zip",
                "byte_size": 128,
            },
            "metadata": {"source": "offline-fixture"},
        },
        "artifact_download": {
            "url": "https://objects.example.test/signed-artifact",
            "expires_at": "2026-09-05T12:15:00Z",
        },
        "homework": {
            "version_id": UUID_6,
            "title": "Contract homework",
            "student_text": "Submit the solution",
            "digest": DIGEST_1,
        },
        "criteria": {
            "set_id": UUID_7,
            "digest": DIGEST_2,
            "items": [
                {
                    "id": CRITERION_A,
                    "key": "correctness",
                    "title": "Correctness",
                    "description": "The solution is correct",
                    "max_points": 5,
                },
                {
                    "id": CRITERION_B,
                    "key": "safety",
                    "title": "Safety",
                    "description": "The solution is safe",
                    "max_points": 2,
                },
            ],
        },
    }


def _suggestion(
    criterion_id: str,
    *,
    points: float | None,
    status: str = "suggested",
    reason: str = "Checked",
) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "status": status,
        "proposed_points": points,
        "reason": reason,
        "evidence": [
            {
                "locator": "file:README.md#L1",
                "quote": "Evidence",
                "verified": True,
            }
        ],
        "confidence": "high",
        "reviewer_note": None,
        "student_feedback": "Feedback",
        "flags": [],
    }


def _succeeded_event() -> dict[str, Any]:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    return {
        "contract_version": "1.1.0",
        "run_id": "00000000-0000-7000-8000-000000000060",
        "attempt_id": "00000000-0000-7000-8000-000000000061",
        "attempt_number": 1,
        "event_id": "00000000-0000-7000-8000-000000000062",
        "sequence": 1,
        "input_fingerprint": vector["expected_fingerprint"],
        "status": "succeeded",
        "is_final": True,
        "suggestions": [
            _suggestion(CRITERION_A, points=5),
            _suggestion(CRITERION_B, points=2),
        ],
        "criterion_coverage": {
            "expected_criterion_ids": [CRITERION_A, CRITERION_B],
            "reported_criterion_ids": [CRITERION_A, CRITERION_B],
            "complete": True,
        },
        "ai_signal": {
            "level": "none",
            "evidence": [],
            "limitations": [],
            "questions": [],
        },
        "error": None,
    }


def _validate_component(name: str, value: object) -> None:
    openapi = load_openapi()
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{name}",
        "components": openapi["components"],
    }
    validator_for(wrapper).validate(value)


def test_ai_request_requires_exact_binding_and_full_immutable_provenance() -> None:
    registry = ContractRegistry()
    request = _request()
    registry.validate(request, "ai-review.schema.json", definition="request")

    assert request["credential_binding_id"] == (
        "00000000-0000-7000-8000-000000000051"
    )
    assert request["credential_binding_version"] == 7
    assert request["artifact"]["artifact_version_id"] == UUID_5
    assert request["artifact"]["content_digest"] == DIGEST_0
    assert request["homework"]["version_id"] == UUID_6
    assert request["homework"]["digest"] == DIGEST_1
    assert request["criteria"]["set_id"] == UUID_7
    assert request["criteria"]["digest"] == DIGEST_2

    for required in (
        "credential_binding_id",
        "credential_binding_version",
        "course_run_id",
        "submission_version_id",
        "review_iteration_id",
        "artifact",
        "artifact_download",
        "homework",
        "criteria",
    ):
        invalid = deepcopy(request)
        del invalid[required]
        with pytest.raises(ValidationError):
            registry.validate(invalid, "ai-review.schema.json", definition="request")


def test_ai_contract_accepts_only_frozen_1_1_0() -> None:
    schema = load_schema("ai-review.schema.json")
    registry = ContractRegistry()
    request = _request()
    event = _succeeded_event()
    for value, definition in ((request, "request"), (event, "event")):
        invalid = deepcopy(value)
        invalid["contract_version"] = "1.0.0"
        with pytest.raises(ValidationError):
            if definition == "request":
                registry.validate(
                    invalid,
                    "ai-review.schema.json",
                    definition="request",
                )
            else:
                validate_definition(schema, definition, invalid)


def test_shared_fingerprint_vector_is_rfc8785_sha256_and_matches_both_sides() -> None:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    actual = "sha256:" + hashlib.sha256(rfc8785.dumps(vector["input"])).hexdigest()

    assert vector["algorithm"] == "jcs-sha256-v1"
    assert actual == vector["expected_fingerprint"]
    assert _request()["input_fingerprint"] == actual
    assert _succeeded_event()["input_fingerprint"] == actual


def test_frozen_event_schema_enforces_attempt_sequence_and_terminal_shapes() -> None:
    schema = load_schema("ai-review.schema.json")
    fixture = load_fixture("ai-events-v1.1.0.json")
    succeeded = fixture["succeeded"]
    failed = fixture["retryable_failed"]
    validate_definition(schema, "event", succeeded)
    validate_definition(schema, "event", failed)
    assert succeeded["attempt_id"] == failed["attempt_id"]
    assert succeeded["attempt_number"] == failed["attempt_number"] == 1
    assert [succeeded["sequence"], failed["sequence"]] == [1, 2]

    for status in ("running", "partial"):
        valid_nonterminal = deepcopy(succeeded)
        valid_nonterminal.update(
            {
                "status": status,
                "is_final": False,
                "suggestions": [],
                "criterion_coverage": {
                    "expected_criterion_ids": [CRITERION_A],
                    "reported_criterion_ids": [],
                    "complete": False,
                },
                "ai_signal": None,
            }
        )
        validate_definition(schema, "event", valid_nonterminal)
    action_required = deepcopy(failed)
    action_required.update(
        {
            "status": "action_required",
            "error": {
                "code": "manual_action_required",
                "message": "Operator action is required",
                "retryable": False,
            },
        }
    )
    validate_definition(schema, "event", action_required)

    invalid_events: list[dict[str, Any]] = []
    for field in ("attempt_id", "attempt_number", "event_id", "sequence"):
        invalid = deepcopy(succeeded)
        invalid[field] = 0 if field in {"attempt_number", "sequence"} else "not-a-uuid"
        invalid_events.append(invalid)
    for status in ("running", "partial"):
        invalid = deepcopy(succeeded)
        invalid.update({"status": status, "is_final": True})
        invalid_events.append(invalid)
        invalid = deepcopy(succeeded)
        invalid.update(
            {
                "status": status,
                "is_final": False,
                "error": {"code": "unexpected", "message": "bad", "retryable": False},
            }
        )
        invalid_events.append(invalid)
    for patch in (
        {"is_final": False},
        {"error": {"code": "unexpected", "message": "bad", "retryable": False}},
        {"ai_signal": None},
        {"suggestions": []},
        {
            "criterion_coverage": {
                "expected_criterion_ids": [CRITERION_A],
                "reported_criterion_ids": [],
                "complete": False,
            }
        },
    ):
        invalid = deepcopy(succeeded)
        invalid.update(patch)
        invalid_events.append(invalid)
    for patch in ({"is_final": False}, {"error": None}):
        invalid = deepcopy(failed)
        invalid.update(patch)
        invalid_events.append(invalid)

    for invalid in invalid_events:
        with pytest.raises(ValidationError):
            validate_definition(schema, "event", invalid)


def test_not_checked_requires_null_and_structural_scores_are_nonnegative() -> None:
    schema = load_schema("ai-review.schema.json")
    valid = _suggestion(
        CRITERION_A,
        points=None,
        status="not_checked",
        reason="Could not verify",
    )
    validate_definition(schema, "suggestion", valid)

    invalid_not_checked = deepcopy(valid)
    invalid_not_checked["proposed_points"] = 0
    with pytest.raises(ValidationError):
        validate_definition(schema, "suggestion", invalid_not_checked)
    invalid_negative = _suggestion(CRITERION_A, points=-0.1)
    with pytest.raises(ValidationError):
        validate_definition(schema, "suggestion", invalid_negative)


def test_minimal_success_event_maps_to_review_detail_without_invented_fields() -> None:
    event = _succeeded_event()
    request = _request()
    suggestion = {"id": "00000000-0000-7000-8000-000000000070", **event["suggestions"][0]}
    ai_summary = {
        "run_id": event["run_id"],
        "input_fingerprint": event["input_fingerprint"],
        "contract_version": event["contract_version"],
        "state": event["status"],
        "attempts": [],
        "suggestions": [suggestion],
        "signal": event["ai_signal"],
        "error": None,
    }
    detail = {
        "review_iteration_id": request["review_iteration_id"],
        "immutable_inputs": {
            "course_run_id": request["course_run_id"],
            "submission_version_id": request["submission_version_id"],
            "effective_deadline": "2026-09-10T12:00:00Z",
            "artifact_version_id": request["artifact"]["artifact_version_id"],
            "artifact_content_digest": request["artifact"]["content_digest"],
            "artifact_download_url": request["artifact_download"]["url"],
            "artifact_download_expires_at": request["artifact_download"]["expires_at"],
            "homework_version_id": request["homework"]["version_id"],
            "criterion_set_id": request["criteria"]["set_id"],
            "contract_version": request["contract_version"],
        },
        "current_review_revision_id": None,
        "current_review_revision": None,
        "revision": 0,
        "status": "queued",
        "criterion_decisions": [],
        "review_notes": [],
        "ai_review": ai_summary,
        "responsibility_events": [],
        "publication_request": None,
        "deliveries": [],
    }
    _validate_component("ReviewDetail", detail)
    assert set(suggestion) == {"id", *event["suggestions"][0]}
    assert set(ai_summary) == {
        "run_id",
        "input_fingerprint",
        "contract_version",
        "state",
        "attempts",
        "suggestions",
        "signal",
        "error",
    }


def test_generated_models_enforce_request_relative_criterion_semantics() -> None:
    module = _generated_module()
    request_model = cast(GeneratedRequestModel, module.__dict__["AIReviewRequest"])
    event_model = cast(GeneratedEventModel, module.__dict__["AIReviewEvent"])
    semantic_validate = cast(
        SemanticValidator,
        module.__dict__["validate_event_against_request"],
    )
    request = request_model.model_validate(_request())
    valid_event = event_model.model_validate(_succeeded_event())
    validated = semantic_validate(valid_event, request)
    assert validated == valid_event
    ContractRegistry().validate(
        request.model_dump(mode="json"),
        "ai-review.schema.json",
        definition="request",
    )
    validate_definition(
        load_schema("ai-review.schema.json"),
        "event",
        valid_event.model_dump(mode="json"),
    )

    invalid_events = []
    unknown = _succeeded_event()
    unknown["suggestions"][0]["criterion_id"] = UUID_1
    invalid_events.append(unknown)
    duplicate = _succeeded_event()
    duplicate["suggestions"][1] = _suggestion(
        CRITERION_A,
        points=4,
        reason="Different duplicate",
    )
    invalid_events.append(duplicate)
    missing = _succeeded_event()
    missing["suggestions"] = missing["suggestions"][:1]
    invalid_events.append(missing)
    coverage_mismatch = _succeeded_event()
    coverage_mismatch["criterion_coverage"]["reported_criterion_ids"] = [CRITERION_A]
    invalid_events.append(coverage_mismatch)
    above_max = _succeeded_event()
    above_max["suggestions"][0]["proposed_points"] = 5.1
    invalid_events.append(above_max)

    for invalid in invalid_events:
        with pytest.raises((ValueError, PydanticValidationError)):
            event = event_model.model_validate(invalid)
            semantic_validate(event, request)


def _generated_module() -> ModuleType:
    module_name = "review_platform.contracts.ai_review"
    specification = importlib.util.find_spec(module_name)
    assert specification is not None, (
        "missing generated AIReviewRequest/AIReviewEvent boundary owned by T101"
    )
    module = importlib.import_module(module_name)
    for name in ("AIReviewRequest", "AIReviewEvent", "validate_event_against_request"):
        assert hasattr(module, name), f"generated AI contract is missing {name}"
    return module
