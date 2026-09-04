"""Tenant-scoped idempotency reservation and canonical replay detection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from review_platform.domain.primitives import canonical_json_sha256

type IdempotencyDisposition = Literal["reserved", "replay"]
type CanonicalValue = (
    None | bool | int | float | str | list["CanonicalValue"] | dict[str, "CanonicalValue"]
)
type StableResultReference = dict[str, CanonicalValue]


class IdempotencyError(RuntimeError):
    """Base class for durable command-receipt failures."""


class InvalidIdempotencyRequest(IdempotencyError):
    """The idempotency identity is structurally invalid."""


class IdempotencyConflict(IdempotencyError):
    """The same tenant/key was previously bound to a different canonical payload."""


@dataclass(frozen=True, slots=True)
class IdempotencyReceipt:
    receipt_id: UUID
    organization_id: UUID
    idempotency_key: str
    request_id: UUID
    command_name: str
    target_id: UUID
    expected_revision: int
    payload_digest: str
    result_reference: StableResultReference


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    receipt: IdempotencyReceipt
    disposition: IdempotencyDisposition


class IdempotencyReceiptRepository(Protocol):
    """Atomic insert-or-return-existing boundary backed by a unique tenant/key."""

    async def reserve(
        self, proposed: IdempotencyReceipt, *, transaction: object
    ) -> tuple[IdempotencyReceipt, bool]:
        """Return ``(stored_receipt, created)`` without replacing an existing row."""

        ...


class IdempotencyCoordinator:
    """Compute canonical identity and adjudicate reservation versus replay."""

    def __init__(
        self,
        repository: IdempotencyReceiptRepository,
        *,
        receipt_id_factory: Callable[[], UUID],
        result_reference_factory: Callable[[], Mapping[str, CanonicalValue]],
    ) -> None:
        self._repository = repository
        self._receipt_id_factory = receipt_id_factory
        self._result_reference_factory = result_reference_factory

    async def reserve(
        self,
        *,
        organization_id: UUID,
        idempotency_key: str,
        request_id: UUID,
        command_name: str,
        target_id: UUID,
        expected_revision: int,
        payload: object,
        transaction: object,
    ) -> IdempotencyReservation:
        if not 16 <= len(idempotency_key) <= 128:
            raise InvalidIdempotencyRequest("idempotency key must contain 16 to 128 characters")
        if not command_name:
            raise InvalidIdempotencyRequest("command name is required")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise InvalidIdempotencyRequest("expected revision must be non-negative")

        payload_digest = canonical_command_digest(
            command_name=command_name,
            target_id=target_id,
            expected_revision=expected_revision,
            payload=payload,
        )
        result_reference = dict(self._result_reference_factory())
        if not result_reference:
            raise InvalidIdempotencyRequest("result reference must be a non-empty object")
        proposed = IdempotencyReceipt(
            receipt_id=self._receipt_id_factory(),
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            command_name=command_name,
            target_id=target_id,
            expected_revision=expected_revision,
            payload_digest=payload_digest,
            result_reference=result_reference,
        )
        stored, created = await self._repository.reserve(proposed, transaction=transaction)
        self._validate_repository_result(
            stored=stored,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )
        if stored.payload_digest != payload_digest:
            raise IdempotencyConflict(
                "idempotency key is already bound to a different canonical payload"
            )
        return IdempotencyReservation(
            receipt=stored,
            disposition="reserved" if created else "replay",
        )

    @staticmethod
    def _validate_repository_result(
        *, stored: IdempotencyReceipt, organization_id: UUID, idempotency_key: str
    ) -> None:
        if stored.organization_id != organization_id or stored.idempotency_key != idempotency_key:
            raise IdempotencyError("receipt repository returned a different tenant/key identity")
        if not stored.result_reference:
            raise IdempotencyError("receipt repository returned an empty result reference")


def canonical_command_digest(
    *, command_name: str, target_id: UUID, expected_revision: int, payload: object
) -> str:
    """SHA-256 of RFC 8785 canonical JSON for the logical mutation identity."""

    logical_command: dict[str, CanonicalValue] = {
        "command_name": command_name,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": _json_compatible(payload),
    }
    try:
        return canonical_json_sha256(logical_command)
    except (TypeError, ValueError) as exc:
        raise InvalidIdempotencyRequest("payload cannot be canonicalized") from exc


def _json_compatible(value: object) -> CanonicalValue:
    if isinstance(value, BaseModel):
        return _json_compatible(value.model_dump(mode="json"))
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _json_compatible(value.value)
    if isinstance(value, Mapping):
        converted: dict[str, CanonicalValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidIdempotencyRequest("canonical payload object keys must be strings")
            converted[key] = _json_compatible(item)
        return converted
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_compatible(item) for item in value]
    if isinstance(value, set | frozenset):
        converted_items = [_json_compatible(item) for item in value]
        return sorted(converted_items, key=repr)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise InvalidIdempotencyRequest(f"unsupported canonical payload type: {type(value).__name__}")
