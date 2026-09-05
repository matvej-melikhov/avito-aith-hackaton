from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.support.contracts import load_fixture

from review_platform.contracts.ai_review import (
    AIReviewEvent,
    AIReviewRequest,
    AISuggestion,
    validate_event_against_request,
)
from review_platform.contracts.registry import ContractRegistry

CRITERION_ID = "00000000-0000-7000-8000-000000000063"
OTHER_CRITERION_ID = "00000000-0000-7000-8000-000000000065"


def _request() -> dict[str, Any]:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    immutable = vector["input"]
    return {
        "contract_version": "1.1.0",
        "run_id": "00000000-0000-7000-8000-000000000060",
        "input_fingerprint": vector["expected_fingerprint"],
        "fingerprint_algorithm": vector["algorithm"],
        "organization_id": immutable["organization_id"],
        "course_run_id": immutable["course_run_id"],
        "submission_version_id": immutable["submission_version_id"],
        "review_iteration_id": immutable["review_iteration_id"],
        "credential_binding_id": "00000000-0000-7000-8000-000000000051",
        "credential_binding_version": 7,
        "artifact": {
            "contract_version": "1.1.0",
            "organization_id": immutable["organization_id"],
            "artifact_reference_id": "00000000-0000-7000-8000-000000000008",
            "artifact_version_id": immutable["artifact_version_id"],
            "provider": "github",
            "provider_version": "commit:" + "a" * 40,
            "content_digest": immutable["artifact_content_digest"],
            "captured_at": "2026-09-05T11:55:00Z",
            "object": {
                "key": "tenant/artifact.zip",
                "media_type": "application/zip",
                "byte_size": 128,
            },
            "metadata": {"source": "offline-fixture", "generation": 1},
        },
        "artifact_download": {
            "url": "https://objects.example.test/signed-artifact",
            "expires_at": "2026-09-05T12:15:00Z",
        },
        "homework": {
            "version_id": immutable["homework_version_id"],
            "title": "Contract homework",
            "student_text": "Submit the solution",
            "digest": immutable["homework_digest"],
        },
        "criteria": {
            "set_id": immutable["criterion_set_id"],
            "digest": immutable["criterion_set_digest"],
            "items": [
                {
                    "id": CRITERION_ID,
                    "key": "correctness",
                    "title": "Correctness",
                    "description": "The solution is correct",
                    "max_points": 5,
                }
            ],
        },
    }


def _events() -> dict[str, dict[str, Any]]:
    return load_fixture("ai-events-v1.1.0.json")


def test_shared_fixtures_roundtrip_and_generated_bindings_conform() -> None:
    registry = ContractRegistry()
    request = AIReviewRequest.model_validate(_request())
    succeeded = AIReviewEvent.model_validate(_events()["succeeded"])
    failed = AIReviewEvent.model_validate(_events()["retryable_failed"])

    assert AIReviewRequest.model_validate(request.model_dump(mode="json")) == request
    assert AIReviewEvent.model_validate(succeeded.model_dump(mode="json")) == succeeded
    assert validate_event_against_request(succeeded, request) is succeeded
    assert validate_event_against_request(failed, request) is failed
    registry.validate_pydantic(
        request,
        "ai-review.schema.json",
        definition="request",
    )
    registry.validate_pydantic(
        succeeded,
        "ai-review.schema.json",
        definition="event",
    )
    registry.assert_generated_schema_conforms(
        AIReviewRequest,
        "ai-review.schema.json",
        definition="request",
        samples=[_request()],
    )
    registry.assert_generated_schema_conforms(
        AIReviewEvent,
        "ai-review.schema.json",
        definition="event",
        samples=list(_events().values()),
    )


@pytest.mark.parametrize("model", [AIReviewRequest, AIReviewEvent])
def test_models_reject_unsupported_contract_version(model: type[Any]) -> None:
    value = _request() if model is AIReviewRequest else _events()["succeeded"]
    value["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError):
        model.model_validate(value)


def test_request_rejects_unknown_missing_and_inconsistent_provenance() -> None:
    unknown = _request()
    unknown["unexpected"] = True
    with pytest.raises(ValidationError):
        AIReviewRequest.model_validate(unknown)

    missing_nullable = _events()["succeeded"]
    del missing_nullable["error"]
    with pytest.raises(ValidationError):
        AIReviewEvent.model_validate(missing_nullable)

    wrong_digest = _request()
    wrong_digest["homework"]["digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="input_fingerprint"):
        AIReviewRequest.model_validate(wrong_digest)

    wrong_tenant = _request()
    wrong_tenant["artifact"]["organization_id"] = (
        "00000000-0000-7000-8000-000000000099"
    )
    with pytest.raises(ValidationError, match="organization_id"):
        AIReviewRequest.model_validate(wrong_tenant)

    invalid_binding = _request()
    invalid_binding["credential_binding_version"] = 0
    with pytest.raises(ValidationError):
        AIReviewRequest.model_validate(invalid_binding)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"status": "running", "is_final": True, "error": None}, "nonfinal"),
        (
            {
                "status": "partial",
                "is_final": False,
                "error": {"code": "bad", "message": "bad", "retryable": False},
            },
            "error=null",
        ),
        ({"status": "succeeded", "is_final": False}, "must be final"),
        ({"status": "succeeded", "ai_signal": None}, "require ai_signal"),
        (
            {"status": "retryable_failed", "is_final": True, "error": None},
            "typed error",
        ),
        (
            {"status": "action_required", "is_final": False},
            "must be final",
        ),
    ],
)
def test_event_terminal_rules_are_enforced(
    patch: dict[str, Any], message: str
) -> None:
    value = _events()["succeeded"]
    value.update(patch)
    with pytest.raises(ValidationError, match=message):
        AIReviewEvent.model_validate(value)


def test_suggestion_not_checked_and_unique_array_rules() -> None:
    suggestion = _events()["succeeded"]["suggestions"][0]
    suggestion.update({"status": "not_checked", "proposed_points": None})
    assert AISuggestion.model_validate(suggestion).proposed_points is None

    invalid_points = deepcopy(suggestion)
    invalid_points["proposed_points"] = 0
    with pytest.raises(ValidationError, match="proposed_points=null"):
        AISuggestion.model_validate(invalid_points)

    duplicate_flag = deepcopy(suggestion)
    duplicate_flag["flags"] = ["manual", "manual"]
    with pytest.raises(ValidationError, match="must be unique"):
        AISuggestion.model_validate(duplicate_flag)

    duplicate_coverage = _events()["succeeded"]
    duplicate_coverage["criterion_coverage"]["reported_criterion_ids"] *= 2
    with pytest.raises(ValidationError, match="must be unique"):
        AIReviewEvent.model_validate(duplicate_coverage)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_run",
        "wrong_fingerprint",
        "unknown_criterion",
        "duplicate_criterion",
        "reported_mismatch",
        "expected_mismatch",
        "above_max",
        "false_complete",
    ],
)
def test_request_relative_event_validation_rejects_inconsistent_context(
    mutation: str,
) -> None:
    request = AIReviewRequest.model_validate(_request())
    value = _events()["succeeded"]
    if mutation == "wrong_run":
        value["run_id"] = "00000000-0000-7000-8000-000000000099"
    elif mutation == "wrong_fingerprint":
        value["input_fingerprint"] = "sha256:" + "f" * 64
    elif mutation == "unknown_criterion":
        value["suggestions"][0]["criterion_id"] = OTHER_CRITERION_ID
        value["criterion_coverage"]["reported_criterion_ids"] = [OTHER_CRITERION_ID]
    elif mutation == "duplicate_criterion":
        value["suggestions"].append(deepcopy(value["suggestions"][0]))
        value["suggestions"][1]["reason"] = "Different duplicate"
        value["criterion_coverage"]["reported_criterion_ids"] = [CRITERION_ID]
    elif mutation == "reported_mismatch":
        value["criterion_coverage"]["reported_criterion_ids"] = []
    elif mutation == "expected_mismatch":
        value["criterion_coverage"]["expected_criterion_ids"] = [OTHER_CRITERION_ID]
    elif mutation == "above_max":
        value["suggestions"][0]["proposed_points"] = 5.1
    elif mutation == "false_complete":
        value["status"] = "partial"
        value["is_final"] = False
        value["criterion_coverage"]["complete"] = False

    event = AIReviewEvent.model_validate(value)
    with pytest.raises(ValueError):
        validate_event_against_request(event, request)


def test_partial_event_may_report_an_exact_request_subset() -> None:
    request_value = _request()
    request_value["criteria"]["items"].append(
        {
            "id": OTHER_CRITERION_ID,
            "key": "safety",
            "title": "Safety",
            "description": "The solution is safe",
            "max_points": 2,
        }
    )
    request = AIReviewRequest.model_validate(request_value)
    value = _events()["succeeded"]
    value.update({"status": "partial", "is_final": False, "ai_signal": None})
    value["criterion_coverage"] = {
        "expected_criterion_ids": [CRITERION_ID, OTHER_CRITERION_ID],
        "reported_criterion_ids": [CRITERION_ID],
        "complete": False,
    }
    event = AIReviewEvent.model_validate(value)

    assert validate_event_against_request(event, request) is event


def test_complete_coverage_is_order_independent_but_membership_exact() -> None:
    request_value = _request()
    request_value["criteria"]["items"].append(
        {
            "id": OTHER_CRITERION_ID,
            "key": "safety",
            "title": "Safety",
            "description": "The solution is safe",
            "max_points": 2,
        }
    )
    request = AIReviewRequest.model_validate(request_value)
    value = _events()["succeeded"]
    second = deepcopy(value["suggestions"][0])
    second.update({"criterion_id": OTHER_CRITERION_ID, "proposed_points": 2})
    value["suggestions"] = [second, value["suggestions"][0]]
    value["criterion_coverage"] = {
        "expected_criterion_ids": [OTHER_CRITERION_ID, CRITERION_ID],
        "reported_criterion_ids": [CRITERION_ID, OTHER_CRITERION_ID],
        "complete": True,
    }
    event = AIReviewEvent.model_validate(value)

    assert validate_event_against_request(event, request) is event
