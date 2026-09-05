"""Executable RED contract for the canonical US5 ReviewDetail projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from fastapi.routing import APIRoute
from jsonschema import ValidationError
from tests.support.contracts import load_openapi, validator_for

from review_platform.main import create_app
from review_platform.settings import Settings

ITERATION = "00000000-0000-7000-8000-000000030001"
COURSE_RUN = "00000000-0000-7000-8000-000000030002"
SUBMISSION_VERSION = "00000000-0000-7000-8000-000000030003"
ARTIFACT_VERSION = "00000000-0000-7000-8000-000000030004"
HOMEWORK_VERSION = "00000000-0000-7000-8000-000000030005"
CRITERION_SET = "00000000-0000-7000-8000-000000030006"
CRITERION = "00000000-0000-7000-8000-000000030007"
REVIEW_REVISION = "00000000-0000-7000-8000-000000030008"
REVIEWER = "00000000-0000-7000-8000-000000030009"
AI_RUN = "00000000-0000-7000-8000-000000030010"
AI_SUGGESTION = "00000000-0000-7000-8000-000000030011"
RESPONSIBILITY = "00000000-0000-7000-8000-000000030012"
PUBLICATION_REQUEST = "00000000-0000-7000-8000-000000030013"
DELIVERY = "00000000-0000-7000-8000-000000030014"
DELIVERY_OPERATION = "00000000-0000-7000-8000-000000030015"
DESTINATION_BINDING = "00000000-0000-7000-8000-000000030016"
DIGEST = "sha256:" + "a" * 64
FINGERPRINT = "sha256:" + "b" * 64


def _review_detail() -> dict[str, Any]:
    return {
        "review_iteration_id": ITERATION,
        "immutable_inputs": {
            "course_run_id": COURSE_RUN,
            "submission_version_id": SUBMISSION_VERSION,
            "effective_deadline": "2026-09-12T12:00:00Z",
            "artifact_version_id": ARTIFACT_VERSION,
            "artifact_content_digest": DIGEST,
            "artifact_download_url": (
                "https://objects.example.test/tenant/artifact?signature=opaque"
            ),
            "artifact_download_expires_at": "2026-09-05T12:15:00Z",
            "homework_version_id": HOMEWORK_VERSION,
            "criterion_set_id": CRITERION_SET,
            "contract_version": "1.1.0",
        },
        "current_review_revision_id": REVIEW_REVISION,
        "current_review_revision": {
            "id": REVIEW_REVISION,
            "review_iteration_id": ITERATION,
            "revision_number": 3,
            "author_user_id": REVIEWER,
            "feedback": "Human feedback remains authoritative.",
            "total_score": 4.5,
            "created_at": "2026-09-05T12:05:00Z",
        },
        "revision": 7,
        "status": "ready_to_publish",
        "criterion_decisions": [
            {
                "criterion_id": CRITERION,
                "points": 4.5,
                "decision": "changed",
                "reason": "Human adjusted the AI suggestion",
                "evidence_ids": [AI_SUGGESTION],
            }
        ],
        "review_notes": [
            {
                "id": "00000000-0000-7000-8000-000000030017",
                "criterion_id": CRITERION,
                "text": "Check the edge case before publication.",
                "author_user_id": REVIEWER,
                "position": 0,
            }
        ],
        "ai_review": {
            "run_id": AI_RUN,
            "input_fingerprint": FINGERPRINT,
            "contract_version": "1.1.0",
            "state": "succeeded",
            "attempts": [
                {
                    "attempt_number": 1,
                    "state": "succeeded",
                    "started_at": "2026-09-05T12:00:00Z",
                    "finished_at": "2026-09-05T12:01:00Z",
                    "error": None,
                }
            ],
            "suggestions": [
                {
                    "id": AI_SUGGESTION,
                    "criterion_id": CRITERION,
                    "status": "suggested",
                    "proposed_points": 5,
                    "reason": "Automated evidence",
                    "evidence": [
                        {
                            "locator": "file:solution.py#L10",
                            "quote": "return result",
                            "verified": True,
                        }
                    ],
                    "confidence": "high",
                    "reviewer_note": None,
                    "student_feedback": "Good implementation",
                    "flags": [],
                }
            ],
            "signal": {
                "level": "low",
                "evidence": [],
                "limitations": ["One optional file was absent"],
                "questions": [],
            },
            "error": None,
        },
        "responsibility_events": [
            {
                "id": RESPONSIBILITY,
                "reviewer_id": REVIEWER,
                "actor_id": REVIEWER,
                "action": "started",
                "occurred_at": "2026-09-05T12:02:00Z",
            }
        ],
        "publication_request": {
            "id": PUBLICATION_REQUEST,
            "review_revision_id": REVIEW_REVISION,
            "status": "pending",
            "expires_at": "2026-09-05T12:30:00Z",
            "revision": 0,
        },
        "deliveries": [
            {
                "id": DELIVERY,
                "operation_id": DELIVERY_OPERATION,
                "destination_binding_id": DESTINATION_BINDING,
                "destination_kind": "stepik",
                "state": "retryable_failed",
                "attempts": [
                    {
                        "attempt_number": 1,
                        "state": "retryable_failed",
                        "started_at": "2026-09-05T12:10:00Z",
                        "finished_at": "2026-09-05T12:10:05Z",
                        "error": {
                            "code": "provider_timeout",
                            "message": "Provider timed out",
                            "action": "retry",
                        },
                    }
                ],
                "provenance": {
                    "course_run_id": COURSE_RUN,
                    "homework_version_id": HOMEWORK_VERSION,
                    "criterion_set_id": CRITERION_SET,
                    "submission_version_id": SUBMISSION_VERSION,
                    "artifact_version_id": ARTIFACT_VERSION,
                    "artifact_content_digest": DIGEST,
                    "review_iteration_id": ITERATION,
                    "review_revision_id": REVIEW_REVISION,
                    "contract_version": "1.1.0",
                },
                "error": {
                    "code": "provider_timeout",
                    "message": "Provider timed out",
                    "action": "retry",
                },
            }
        ],
    }


def _validator(name: str):
    openapi = load_openapi()
    return validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/components/schemas/{name}",
            "components": openapi["components"],
        }
    )


def test_canonical_review_detail_contains_exact_human_ai_and_delivery_state() -> None:
    detail = _review_detail()
    _validator("ReviewDetail").validate(detail)

    assert set(detail) == {
        "review_iteration_id",
        "immutable_inputs",
        "current_review_revision_id",
        "current_review_revision",
        "revision",
        "status",
        "criterion_decisions",
        "review_notes",
        "ai_review",
        "responsibility_events",
        "publication_request",
        "deliveries",
    }
    immutable = detail["immutable_inputs"]
    assert immutable == {
        "course_run_id": COURSE_RUN,
        "submission_version_id": SUBMISSION_VERSION,
        "effective_deadline": "2026-09-12T12:00:00Z",
        "artifact_version_id": ARTIFACT_VERSION,
        "artifact_content_digest": DIGEST,
        "artifact_download_url": (
            "https://objects.example.test/tenant/artifact?signature=opaque"
        ),
        "artifact_download_expires_at": "2026-09-05T12:15:00Z",
        "homework_version_id": HOMEWORK_VERSION,
        "criterion_set_id": CRITERION_SET,
        "contract_version": "1.1.0",
    }
    assert detail["current_review_revision"]["feedback"] == (
        "Human feedback remains authoritative."
    )
    assert detail["current_review_revision"]["total_score"] == 4.5
    assert detail["criterion_decisions"][0]["criterion_id"] == CRITERION
    assert detail["review_notes"][0]["position"] == 0
    assert detail["ai_review"]["signal"]["level"] == "low"
    assert detail["deliveries"][0]["operation_id"] == DELIVERY_OPERATION
    assert detail["deliveries"][0]["provenance"]["review_revision_id"] == (
        REVIEW_REVISION
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("immutable_inputs", "artifact_content_digest"), "not-a-digest"),
        (("current_review_revision", "total_score"), -1),
        (("criterion_decisions", 0, "decision"), "automatic"),
        (("deliveries", 0, "provenance", "review_revision_id"), "not-a-uuid"),
    ],
)
def test_review_detail_rejects_invalid_provenance_or_human_revision_fields(
    path: tuple[str | int, ...], value: object
) -> None:
    detail = deepcopy(_review_detail())
    current: Any = detail
    for segment in path[:-1]:
        current = current[segment]
    current[path[-1]] = value
    with pytest.raises(ValidationError):
        _validator("ReviewDetail").validate(detail)


def test_review_detail_is_closed_and_nullable_sections_are_explicit() -> None:
    detail = _review_detail()
    detail["ai_review"] = None
    detail["publication_request"] = None
    detail["current_review_revision_id"] = None
    detail["current_review_revision"] = None
    detail["criterion_decisions"] = []
    detail["review_notes"] = []
    detail["responsibility_events"] = []
    detail["deliveries"] = []
    _validator("ReviewDetail").validate(detail)

    detail["invented_summary"] = "must not appear"
    with pytest.raises(ValidationError):
        _validator("ReviewDetail").validate(detail)


def test_get_review_detail_route_is_registered_with_exact_frozen_operation() -> None:
    app = create_app(Settings())
    operations = {
        (route.path, method): route.operation_id
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    route_key = ("/api/v1/review-iterations/{reviewIterationId}", "GET")
    assert route_key in operations, (
        "T130/T131 are missing the canonical getReviewIteration route"
    )
    assert operations[route_key] == "getReviewIteration"
