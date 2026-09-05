"""Typed frozen delivery payloads and canonical publication fingerprints."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, ClassVar, Literal, cast
from uuid import UUID

from pydantic import (
    AfterValidator,
    AnyUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_serializer,
    model_validator,
)

from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry
from review_platform.domain.primitives import (
    JsonValue,
    canonical_json_sha256,
    sanitize_error,
    validate_digest,
)

_CONTRACT_VERSION: Literal["1.1.0"] = "1.1.0"
if CONTRACT_VERSION != _CONTRACT_VERSION:
    raise RuntimeError("delivery domain version differs from frozen contract")

type CanonicalDigest = Annotated[str, AfterValidator(validate_digest)]
type DeliveryKey = Annotated[str, StringConstraints(min_length=1, max_length=512)]
type RecipientReference = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512),
]
type PositiveInteger = Annotated[int, Field(ge=1, strict=True)]


def _validated_decimal(value: object) -> Decimal:
    return _decimal(value, field="score")


type NonNegativeDecimal = Annotated[
    Decimal,
    BeforeValidator(_validated_decimal),
    Field(ge=0),
]


class DeliveryPayloadError(ValueError):
    """Delivery data cannot be represented by the frozen JSON contract."""


class _StrictDeliveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class DeliveryError(_StrictDeliveryModel):
    code: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    message: Annotated[str, StringConstraints(max_length=2048)]
    retryable: StrictBool
    action: Annotated[str, StringConstraints(max_length=512)] | None = None


class DeliveryProvenance(_StrictDeliveryModel):
    course_run_id: UUID
    homework_version_id: UUID
    criterion_set_id: UUID
    submission_version_id: UUID
    artifact_version_id: UUID
    artifact_content_digest: CanonicalDigest
    review_iteration_id: UUID
    review_revision_id: UUID
    contract_version: Literal["1.1.0"]


class DeliveryCriterionResult(_StrictDeliveryModel):
    criterion_id: UUID
    points: NonNegativeDecimal
    reason: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]

    @field_serializer("points", when_used="json")
    def serialize_points(self, value: Decimal) -> int | float:
        return _decimal_json_number(value)


class DeliveryPayload(_StrictDeliveryModel):
    total_score: NonNegativeDecimal
    feedback: Annotated[str, StringConstraints(max_length=50_000)]
    criteria: Annotated[list[DeliveryCriterionResult], Field(max_length=500)]

    @field_serializer("total_score", when_used="json")
    def serialize_total_score(self, value: Decimal) -> int | float:
        return _decimal_json_number(value)


class DeliveryDestination(_StrictDeliveryModel):
    binding_id: UUID
    binding_version: PositiveInteger
    credential_binding_id: UUID
    credential_binding_version: PositiveInteger
    kind: Literal["stepik", "github"]
    recipient_ref: RecipientReference


class DeliverRequest(_StrictDeliveryModel):
    __contract_schema__: ClassVar[str] = "delivery.schema.json"
    __contract_definition__: ClassVar[str] = "deliver_request"
    __contract_version__: ClassVar[str] = CONTRACT_VERSION

    contract_version: Literal["1.1.0"]
    organization_id: UUID
    delivery_id: UUID
    delivery_key: DeliveryKey
    destination: DeliveryDestination
    provenance: DeliveryProvenance
    publication_fingerprint: CanonicalDigest
    payload_digest: CanonicalDigest
    payload: DeliveryPayload


class ReconcileRequest(_StrictDeliveryModel):
    __contract_schema__: ClassVar[str] = "delivery.schema.json"
    __contract_definition__: ClassVar[str] = "reconcile_request"
    __contract_version__: ClassVar[str] = CONTRACT_VERSION

    contract_version: Literal["1.1.0"]
    organization_id: UUID
    delivery_id: UUID
    delivery_key: DeliveryKey
    destination_binding_id: UUID
    destination_binding_version: PositiveInteger
    credential_binding_id: UUID
    credential_binding_version: PositiveInteger
    payload_digest: CanonicalDigest


class DeliveryResult(_StrictDeliveryModel):
    __contract_schema__: ClassVar[str] = "delivery.schema.json"
    __contract_definition__: ClassVar[str] = "result"
    __contract_version__: ClassVar[str] = CONTRACT_VERSION

    contract_version: Literal["1.1.0"]
    organization_id: UUID
    delivery_id: UUID
    outcome: Literal[
        "succeeded",
        "retryable_failed",
        "unknown_outcome",
        "action_required",
        "not_found",
    ]
    external_id: Annotated[str, StringConstraints(max_length=512)] | None
    external_url: AnyUrl | None
    error: DeliveryError | None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> DeliveryResult:
        if self.outcome == "succeeded":
            if not self.external_id or self.error is not None:
                raise ValueError("succeeded delivery requires external_id and error=null")
        elif self.error is None:
            raise ValueError("non-success delivery result requires a typed error")
        return self


def parse_deliver_request(value: DeliverRequest | Mapping[str, Any]) -> DeliverRequest:
    request = value if isinstance(value, DeliverRequest) else DeliverRequest.model_validate(value)
    _validate_model(request, "deliver_request")
    return request


def parse_reconcile_request(
    value: ReconcileRequest | Mapping[str, Any],
) -> ReconcileRequest:
    request = (
        value if isinstance(value, ReconcileRequest) else ReconcileRequest.model_validate(value)
    )
    _validate_model(request, "reconcile_request")
    return request


def parse_delivery_result(value: DeliveryResult | Mapping[str, Any]) -> DeliveryResult:
    result = value if isinstance(value, DeliveryResult) else DeliveryResult.model_validate(value)
    _validate_model(result, "result")
    return result


def render_deliver_request(
    value: DeliverRequest | Mapping[str, Any],
) -> dict[str, JsonValue]:
    return _contract_mapping(parse_deliver_request(value))


def render_reconcile_request(
    value: ReconcileRequest | Mapping[str, Any],
) -> dict[str, JsonValue]:
    return _contract_mapping(parse_reconcile_request(value))


def render_delivery_result(
    value: DeliveryResult | Mapping[str, Any],
) -> dict[str, JsonValue]:
    if isinstance(value, DeliveryResult):
        source: Mapping[str, Any] = value.model_dump(mode="python")
    else:
        source = value
    prepared = dict(source)
    raw_error = prepared.get("error")
    if isinstance(raw_error, Mapping):
        prepared["error"] = render_delivery_error(raw_error)
    result = parse_delivery_result(prepared)
    return _contract_mapping(result)


def render_delivery_error(
    error: Mapping[str, Any] | BaseException | str,
    *,
    retryable: bool | None = None,
) -> dict[str, JsonValue]:
    source_retryable = error.get("retryable") if isinstance(error, Mapping) else None
    sanitized = sanitize_error(error, max_bytes=2048)
    raw_retryable = retryable if retryable is not None else source_retryable
    if not isinstance(raw_retryable, bool):
        raise DeliveryPayloadError("delivery error retryable flag is required")
    raw_code = sanitized.get("code", sanitized.get("safe_code", "delivery_error"))
    raw_message = sanitized.get("message", "Delivery failed")
    raw_action = sanitized.get("action", sanitized.get("safe_action"))
    payload = {
        "code": raw_code if isinstance(raw_code, str) and raw_code else "delivery_error",
        "message": raw_message if isinstance(raw_message, str) else "Delivery failed",
        "retryable": raw_retryable,
        "action": raw_action if isinstance(raw_action, str) else None,
    }
    model = DeliveryError.model_validate(payload)
    rendered = _contract_mapping(model)
    if _json_size(rendered) > 2048:
        # The sanitizer bounded its own richer mapping. Re-run only the exact
        # frozen error fields so the final persisted/provider form is bounded.
        # Reserve room for the required retryable field, which the shared
        # sanitizer may omit from its minimal over-budget summary.
        bounded = sanitize_error(rendered, max_bytes=2000)
        model = DeliveryError.model_validate(
            {
                "code": bounded.get("code", "delivery_error"),
                "message": bounded.get("message", "Delivery failed"),
                "retryable": raw_retryable,
                "action": bounded.get("action"),
            }
        )
        rendered = _contract_mapping(model)
    if _json_size(rendered) > 2048:
        raise DeliveryPayloadError("sanitized delivery error exceeds 2048 UTF-8 bytes")
    ContractRegistry().validate(
        rendered,
        "delivery.schema.json",
        definition="error",
    )
    return rendered


def canonical_delivery_payload_digest(
    payload: DeliveryPayload | Mapping[str, Any],
) -> str:
    model = (
        payload
        if isinstance(payload, DeliveryPayload)
        else DeliveryPayload.model_validate(payload)
    )
    _validate_model(model, "payload")
    return canonical_json_sha256(_contract_mapping(model))


def publication_provenance_fingerprint(
    *,
    organization_id: UUID,
    publication_id: UUID,
    publication_version: int,
    provenance: DeliveryProvenance | Mapping[str, Any],
    payload_digest: str,
    contract_version: str = CONTRACT_VERSION,
) -> str:
    if contract_version != CONTRACT_VERSION:
        raise DeliveryPayloadError("unsupported delivery contract version")
    if (
        not isinstance(publication_version, int)
        or isinstance(publication_version, bool)
        or publication_version < 1
    ):
        raise DeliveryPayloadError("publication_version must be a positive integer")
    provenance_model = (
        provenance
        if isinstance(provenance, DeliveryProvenance)
        else DeliveryProvenance.model_validate(provenance)
    )
    _validate_model(provenance_model, "provenance")
    digest = validate_digest(payload_digest)
    return canonical_json_sha256(
        {
            "contract_version": CONTRACT_VERSION,
            "organization_id": str(organization_id),
            "publication_id": str(publication_id),
            "publication_version": publication_version,
            "provenance": _contract_mapping(provenance_model),
            "payload_digest": digest,
        }
    )


def build_deliver_request(
    *,
    organization_id: UUID,
    delivery_id: UUID,
    delivery_key: str,
    destination: DeliveryDestination | Mapping[str, Any],
    provenance: DeliveryProvenance | Mapping[str, Any],
    publication_id: UUID,
    publication_version: int,
    payload: DeliveryPayload | Mapping[str, Any],
) -> DeliverRequest:
    destination_model = (
        destination
        if isinstance(destination, DeliveryDestination)
        else DeliveryDestination.model_validate(destination)
    )
    provenance_model = (
        provenance
        if isinstance(provenance, DeliveryProvenance)
        else DeliveryProvenance.model_validate(provenance)
    )
    payload_model = (
        payload if isinstance(payload, DeliveryPayload) else DeliveryPayload.model_validate(payload)
    )
    payload_digest = canonical_delivery_payload_digest(payload_model)
    fingerprint = publication_provenance_fingerprint(
        organization_id=organization_id,
        publication_id=publication_id,
        publication_version=publication_version,
        provenance=provenance_model,
        payload_digest=payload_digest,
    )
    return parse_deliver_request(
        DeliverRequest(
            contract_version=_CONTRACT_VERSION,
            organization_id=organization_id,
            delivery_id=delivery_id,
            delivery_key=delivery_key,
            destination=destination_model,
            provenance=provenance_model,
            publication_fingerprint=fingerprint,
            payload_digest=payload_digest,
            payload=payload_model,
        )
    )


def build_reconcile_request(request: DeliverRequest) -> ReconcileRequest:
    parsed = parse_deliver_request(request)
    return parse_reconcile_request(
        ReconcileRequest(
            contract_version=_CONTRACT_VERSION,
            organization_id=parsed.organization_id,
            delivery_id=parsed.delivery_id,
            delivery_key=parsed.delivery_key,
            destination_binding_id=parsed.destination.binding_id,
            destination_binding_version=parsed.destination.binding_version,
            credential_binding_id=parsed.destination.credential_binding_id,
            credential_binding_version=parsed.destination.credential_binding_version,
            payload_digest=parsed.payload_digest,
        )
    )


def _decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | int | float):
        raise DeliveryPayloadError(f"{field} must be a JSON number or Decimal")
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if not decimal.is_finite() or decimal < 0:
        raise DeliveryPayloadError(f"{field} must be a finite nonnegative number")
    _decimal_json_number(decimal)
    return decimal


def _decimal_json_number(value: Decimal) -> int | float:
    if not value.is_finite():
        raise DeliveryPayloadError("delivery Decimal must be finite")
    if value == 0:
        return 0
    integral = value.to_integral_value()
    if value == integral:
        integer = int(integral)
        if abs(integer) > (2**53 - 1):
            raise DeliveryPayloadError("delivery integer exceeds exact JSON number range")
        return integer
    number = float(value)
    if not math.isfinite(number) or Decimal(str(number)) != value.normalize():
        raise DeliveryPayloadError("delivery Decimal cannot round-trip as a JSON number")
    return number


def _validate_model(model: BaseModel, definition: str) -> None:
    ContractRegistry().validate(
        _contract_mapping(model),
        "delivery.schema.json",
        definition=definition,
    )


def _contract_mapping(model: BaseModel) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], model.model_dump(mode="json"))


def _json_size(value: Mapping[str, JsonValue]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


__all__ = [
    "DeliverRequest",
    "DeliveryCriterionResult",
    "DeliveryDestination",
    "DeliveryError",
    "DeliveryPayload",
    "DeliveryPayloadError",
    "DeliveryProvenance",
    "DeliveryResult",
    "ReconcileRequest",
    "build_deliver_request",
    "build_reconcile_request",
    "canonical_delivery_payload_digest",
    "parse_deliver_request",
    "parse_delivery_result",
    "parse_reconcile_request",
    "publication_provenance_fingerprint",
    "render_deliver_request",
    "render_delivery_error",
    "render_delivery_result",
    "render_reconcile_request",
]
