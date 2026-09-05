from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.support.contracts import load_fixture

from review_platform.contracts.registry import ContractRegistry
from review_platform.domain.delivery_payload import (
    DeliverRequest,
    DeliveryPayload,
    DeliveryPayloadError,
    DeliveryResult,
    ReconcileRequest,
    build_deliver_request,
    build_reconcile_request,
    canonical_delivery_payload_digest,
    parse_deliver_request,
    parse_delivery_result,
    parse_reconcile_request,
    publication_provenance_fingerprint,
    render_deliver_request,
    render_delivery_error,
    render_delivery_result,
    render_reconcile_request,
)

PUBLICATION = UUID("00000000-0000-7000-8000-000000000040")


def _fixture() -> dict[str, Any]:
    return load_fixture("delivery-v1.1.0.json")


def test_shared_frozen_vectors_parse_render_and_generated_schemas_conform() -> None:
    fixture = _fixture()
    request = parse_deliver_request(fixture["request"])
    reconcile = parse_reconcile_request(fixture["reconcile_request"])
    success = parse_delivery_result(fixture["success_result"])
    unknown = parse_delivery_result(fixture["unknown_result"])

    assert render_deliver_request(request) == fixture["request"]
    assert render_reconcile_request(reconcile) == fixture["reconcile_request"]
    assert render_delivery_result(success) == fixture["success_result"]
    assert render_delivery_result(unknown) == fixture["unknown_result"]
    registry = ContractRegistry()
    registry.assert_generated_schema_conforms(
        DeliverRequest,
        "delivery.schema.json",
        definition="deliver_request",
        samples=[fixture["request"]],
    )
    registry.assert_generated_schema_conforms(
        ReconcileRequest,
        "delivery.schema.json",
        definition="reconcile_request",
        samples=[fixture["reconcile_request"]],
    )
    registry.assert_generated_schema_conforms(
        DeliveryResult,
        "delivery.schema.json",
        definition="result",
        samples=[fixture["success_result"], fixture["unknown_result"]],
    )


def test_builder_computes_canonical_payload_digest_and_full_fingerprint() -> None:
    source = _fixture()["request"]
    payload = {
        "total_score": Decimal("10.00"),
        "feedback": source["payload"]["feedback"],
        "criteria": [
            {
                "criterion_id": source["payload"]["criteria"][0]["criterion_id"],
                "points": Decimal("10.0"),
                "reason": source["payload"]["criteria"][0]["reason"],
            }
        ],
    }
    built = build_deliver_request(
        organization_id=UUID(source["organization_id"]),
        delivery_id=UUID(source["delivery_id"]),
        delivery_key=source["delivery_key"],
        destination=source["destination"],
        provenance=source["provenance"],
        publication_id=PUBLICATION,
        publication_version=2,
        payload=payload,
    )

    assert built.payload_digest == (
        "sha256:20fcc715bd72317e5d3634998c04714f3f7042e8e6966fdd09083f31afdfd727"
    )
    assert built.publication_fingerprint == (
        "sha256:21fc9a8a9f75f05fffac2f768af7d5bc4f36e842ab797bb0131e290076a5142b"
    )
    assert render_deliver_request(built)["payload"] == source["payload"]
    ContractRegistry().validate(
        render_deliver_request(built),
        "delivery.schema.json",
        definition="deliver_request",
    )


def test_decimal_equivalence_is_numeric_not_string_and_key_order_independent() -> None:
    fixture_payload = _fixture()["request"]["payload"]
    reordered = {
        "criteria": fixture_payload["criteria"],
        "feedback": fixture_payload["feedback"],
        "total_score": Decimal("10.000"),
    }
    model = DeliveryPayload.model_validate(reordered)
    rendered = model.model_dump(mode="json")

    assert rendered["total_score"] == 10
    assert not isinstance(rendered["total_score"], str)
    assert canonical_delivery_payload_digest(reordered) == (
        canonical_delivery_payload_digest(fixture_payload)
    )


def test_reconcile_builder_preserves_exact_binding_and_payload_identity() -> None:
    source = _fixture()["request"]
    request = build_deliver_request(
        organization_id=UUID(source["organization_id"]),
        delivery_id=UUID(source["delivery_id"]),
        delivery_key=source["delivery_key"],
        destination=source["destination"],
        provenance=source["provenance"],
        publication_id=PUBLICATION,
        publication_version=1,
        payload=source["payload"],
    )
    reconcile = build_reconcile_request(request)

    assert reconcile.organization_id == request.organization_id
    assert reconcile.delivery_id == request.delivery_id
    assert reconcile.destination_binding_id == request.destination.binding_id
    assert reconcile.destination_binding_version == request.destination.binding_version
    assert reconcile.credential_binding_id == request.destination.credential_binding_id
    assert (
        reconcile.credential_binding_version
        == request.destination.credential_binding_version
    )
    assert reconcile.payload_digest == request.payload_digest


@pytest.mark.parametrize(
    "field",
    [
        "course_run_id",
        "homework_version_id",
        "criterion_set_id",
        "submission_version_id",
        "artifact_version_id",
        "artifact_content_digest",
        "review_iteration_id",
        "review_revision_id",
    ],
)
def test_every_provenance_change_changes_publication_fingerprint(field: str) -> None:
    request = _fixture()["request"]
    provenance = deepcopy(request["provenance"])
    original = publication_provenance_fingerprint(
        organization_id=UUID(request["organization_id"]),
        publication_id=PUBLICATION,
        publication_version=1,
        provenance=provenance,
        payload_digest=request["payload_digest"],
    )
    provenance[field] = (
        "sha256:" + "f" * 64
        if field == "artifact_content_digest"
        else "00000000-0000-7000-8000-000000000099"
    )

    assert publication_provenance_fingerprint(
        organization_id=UUID(request["organization_id"]),
        publication_id=PUBLICATION,
        publication_version=1,
        provenance=provenance,
        payload_digest=request["payload_digest"],
    ) != original


def test_error_renderer_redacts_secrets_and_bounds_final_utf8_json() -> None:
    rendered = render_delivery_error(
        {
            "code": "provider_timeout",
            "message": "Bearer super-secret " + "я" * 4_000,
            "retryable": True,
            "action": "reconcile",
            "authorization": "Bearer hidden-token",
            "raw_body": "provider private response",
        }
    )
    encoded = json.dumps(rendered, ensure_ascii=False, sort_keys=True).encode()

    assert rendered["code"] == "provider_timeout"
    assert rendered["retryable"] is True
    assert rendered["action"] == "reconcile"
    assert b"super-secret" not in encoded
    assert b"hidden-token" not in encoded
    assert b"provider private response" not in encoded
    assert len(encoded) <= 2048
    ContractRegistry().validate(
        rendered,
        "delivery.schema.json",
        definition="error",
    )


def test_result_renderer_sanitizes_provider_error_before_contract_output() -> None:
    result = deepcopy(_fixture()["unknown_result"])
    result["error"]["authorization"] = "Bearer provider-secret"
    result["error"]["raw_body"] = "private provider body"
    rendered = render_delivery_result(result)
    encoded = json.dumps(rendered, sort_keys=True).encode()

    assert b"provider-secret" not in encoded
    assert b"private provider body" not in encoded
    assert rendered["outcome"] == "unknown_outcome"


@pytest.mark.parametrize(
    "value",
    [
        "10",
        True,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.123456789012345678901"),
        2**53,
    ],
)
def test_decimal_input_rejects_coercion_nonfinite_and_lossy_values(value: object) -> None:
    payload = deepcopy(_fixture()["request"]["payload"])
    payload["total_score"] = value
    with pytest.raises((DeliveryPayloadError, ValidationError)):
        DeliveryPayload.model_validate(payload)


def test_strict_models_reject_unknown_fields_versions_and_result_conditionals() -> None:
    request = deepcopy(_fixture()["request"])
    request["provider_body"] = "forbidden"
    with pytest.raises(ValidationError):
        DeliverRequest.model_validate(request)

    reconcile = deepcopy(_fixture()["reconcile_request"])
    reconcile["contract_version"] = "1.0.0"
    with pytest.raises(ValidationError):
        ReconcileRequest.model_validate(reconcile)

    succeeded = deepcopy(_fixture()["success_result"])
    succeeded["error"] = {
        "code": "unexpected",
        "message": "bad",
        "retryable": False,
    }
    with pytest.raises(ValidationError, match="error=null"):
        DeliveryResult.model_validate(succeeded)

    failed = deepcopy(_fixture()["unknown_result"])
    failed["error"] = None
    with pytest.raises(ValidationError, match="typed error"):
        DeliveryResult.model_validate(failed)
