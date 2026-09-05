from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.projections.review_detail import (
    ReviewDetailProjectionError,
    project_delivery_summary,
)
from review_platform.infrastructure.db.models.delivery import (
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
from review_platform.infrastructure.db.models.publication import ExternalDelivery

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000160001")
DELIVERY = UUID("00000000-0000-7000-8000-000000160002")
OPERATION = UUID("00000000-0000-7000-8000-000000160003")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000160004")
ATTEMPT_ONE = UUID("00000000-0000-7000-8000-000000160005")
ATTEMPT_TWO = UUID("00000000-0000-7000-8000-000000160006")


def test_delivery_summary_orders_attempts_and_projects_latest_reconciliation() -> None:
    delivery = _delivery()
    first = _attempt(ATTEMPT_ONE, 1, "unknown_outcome")
    second = _attempt(ATTEMPT_TWO, 2, "succeeded")
    observation = _observation(first, outcome="not_found")

    summary = project_delivery_summary(
        delivery,
        attempts=[second, first],
        observations=[observation],
    )

    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/components/schemas/DeliverySummary",
            "components": load_openapi()["components"],
        }
    ).validate(summary)
    assert summary["state"] == "succeeded"
    assert [item["attempt_number"] for item in summary["attempts"]] == [1, 2]
    reconciled = summary["attempts"][0]
    assert reconciled == {
        "attempt_number": 1,
        "state": "retryable_failed",
        "started_at": NOW.isoformat(),
        "finished_at": (NOW + timedelta(seconds=10)).isoformat(),
        "error": {
            "code": "delivery_not_found",
            "message": "Provider reports no result for the delivery attempt",
            "action": "retry",
        },
    }
    assert summary["attempts"][1]["state"] == "succeeded"
    assert summary["provenance"] == {
        "course_run_id": "00000000-0000-7000-8000-000000160010",
        "homework_version_id": "00000000-0000-7000-8000-000000160011",
        "criterion_set_id": "00000000-0000-7000-8000-000000160012",
        "submission_version_id": "00000000-0000-7000-8000-000000160013",
        "artifact_version_id": "00000000-0000-7000-8000-000000160014",
        "artifact_content_digest": "sha256:" + "a" * 64,
        "review_iteration_id": "00000000-0000-7000-8000-000000160015",
        "review_revision_id": "00000000-0000-7000-8000-000000160016",
        "contract_version": "1.1.0",
    }
    assert "observations" not in summary


def test_delivery_summary_rejects_attempt_from_different_credential_snapshot() -> None:
    attempt = _attempt(ATTEMPT_ONE, 1, "succeeded")
    attempt.credential_binding_version = 2

    with pytest.raises(ReviewDetailProjectionError, match="provenance mismatched"):
        project_delivery_summary(_delivery(), attempts=[attempt], observations=[])


def _delivery() -> ExternalDelivery:
    return ExternalDelivery(
        id=DELIVERY,
        organization_id=ORG,
        publication_id=UUID("00000000-0000-7000-8000-000000160007"),
        operation_id=OPERATION,
        delivery_key="review:publication:destination:1",
        destination_binding_id=UUID("00000000-0000-7000-8000-000000160008"),
        binding_version=1,
        credential_binding_id=CREDENTIAL,
        credential_binding_version=1,
        destination_kind="github",
        recipient_ref="example/repository#review",
        course_run_id=UUID("00000000-0000-7000-8000-000000160010"),
        homework_version_id=UUID("00000000-0000-7000-8000-000000160011"),
        criterion_set_id=UUID("00000000-0000-7000-8000-000000160012"),
        submission_version_id=UUID("00000000-0000-7000-8000-000000160013"),
        artifact_version_id=UUID("00000000-0000-7000-8000-000000160014"),
        artifact_content_digest="sha256:" + "a" * 64,
        review_iteration_id=UUID("00000000-0000-7000-8000-000000160015"),
        review_revision_id=UUID("00000000-0000-7000-8000-000000160016"),
        contract_version="1.1.0",
        publication_fingerprint="sha256:" + "b" * 64,
        payload_version="1.1.0",
        payload_digest="sha256:" + "c" * 64,
        payload={},
        state="succeeded",
        attempt_count=2,
        next_attempt_at=None,
        external_id="provider-result",
        external_url="https://provider.example.test/result",
        last_error_code=None,
        sanitized_error=None,
        revision=3,
    )


def _attempt(identity: UUID, number: int, outcome: str) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=identity,
        organization_id=ORG,
        delivery_id=DELIVERY,
        external_delivery_id=DELIVERY,
        operation_id=OPERATION,
        attempt_number=number,
        credential_binding_id=CREDENTIAL,
        credential_binding_version=1,
        worker_identity="delivery-worker",
        claim_token=UUID(f"00000000-0000-7000-8000-{160100 + number:012d}"),
        lease_expires_at=NOW + timedelta(minutes=5),
        state=outcome,
        started_at=NOW + timedelta(minutes=number - 1),
        finished_at=NOW + timedelta(minutes=number - 1, seconds=5),
        outcome=outcome,
        error_code=None,
        sanitized_error=None,
    )


def _observation(
    attempt: DeliveryAttempt,
    *,
    outcome: str,
) -> DeliveryReconciliationObservation:
    return DeliveryReconciliationObservation(
        id=UUID("00000000-0000-7000-8000-000000160020"),
        organization_id=ORG,
        delivery_id=DELIVERY,
        external_delivery_id=DELIVERY,
        delivery_attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        operation_id=OPERATION,
        credential_binding_id=CREDENTIAL,
        credential_binding_version=1,
        request_payload_version="1.1.0",
        request_digest="sha256:" + "d" * 64,
        result_payload_version="1.1.0",
        result_digest="sha256:" + "e" * 64,
        observed_at=NOW + timedelta(seconds=10),
        outcome=outcome,
        external_id=None,
        external_url=None,
        error_code=None,
        sanitized_error=None,
    )
