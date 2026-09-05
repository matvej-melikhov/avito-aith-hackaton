"""Canonical immutable-input fingerprint for AI review runs."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict

from review_platform.domain.primitives import (
    canonical_json_bytes,
    sha256_digest,
    validate_digest,
)

FINGERPRINT_ALGORITHM = "jcs-sha256-v1"
AI_CONTRACT_VERSION = "1.1.0"

type CanonicalDigest = Annotated[str, AfterValidator(validate_digest)]


class FingerprintError(ValueError):
    """Base error for unsupported or malformed fingerprint operations."""


class UnsupportedFingerprintAlgorithm(FingerprintError):
    """Only the frozen JCS/SHA-256 algorithm is accepted."""


class InvalidExpectedFingerprint(FingerprintError):
    """A comparison digest was not in canonical SHA-256 form."""


class ImmutableAIReviewInput(BaseModel):
    """Exactly the versioned immutable identities included in the fingerprint.

    Attempt-specific credential binding is deliberately absent. Passing one as
    an extra field fails validation instead of silently changing run identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["1.1.0"]
    organization_id: UUID
    course_run_id: UUID
    submission_version_id: UUID
    review_iteration_id: UUID
    artifact_version_id: UUID
    artifact_content_digest: CanonicalDigest
    homework_version_id: UUID
    homework_digest: CanonicalDigest
    criterion_set_id: UUID
    criterion_set_digest: CanonicalDigest

    def canonical_mapping(self) -> dict[str, str]:
        """Return the flat JSON shape pinned by the shared golden vector."""

        return cast(dict[str, str], self.model_dump(mode="json"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ImmutableAIReviewInput:
        return cls.model_validate(dict(value))


def canonical_ai_input_bytes(
    value: ImmutableAIReviewInput | Mapping[str, object],
    *,
    algorithm: str = FINGERPRINT_ALGORITHM,
) -> bytes:
    """Serialize the exact frozen value object with RFC 8785 JCS."""

    _require_algorithm(algorithm)
    immutable = _coerce_input(value)
    return canonical_json_bytes(immutable.canonical_mapping())


def compute_ai_fingerprint(
    value: ImmutableAIReviewInput | Mapping[str, object],
    *,
    algorithm: str = FINGERPRINT_ALGORITHM,
) -> str:
    """Return ``sha256:<lowercase hex>`` for one immutable AI input."""

    return sha256_digest(canonical_ai_input_bytes(value, algorithm=algorithm))


def verify_ai_fingerprint(
    value: ImmutableAIReviewInput | Mapping[str, object],
    expected_fingerprint: str,
    *,
    algorithm: str = FINGERPRINT_ALGORITHM,
) -> bool:
    """Compare a canonical expected digest without data-dependent early exit."""

    try:
        expected = validate_digest(expected_fingerprint)
    except ValueError as error:
        raise InvalidExpectedFingerprint(
            "expected fingerprint must use sha256:<64 lowercase hex>"
        ) from error
    actual = compute_ai_fingerprint(value, algorithm=algorithm)
    return hmac.compare_digest(actual, expected)


def _coerce_input(
    value: ImmutableAIReviewInput | Mapping[str, object],
) -> ImmutableAIReviewInput:
    if isinstance(value, ImmutableAIReviewInput):
        return value
    return ImmutableAIReviewInput.from_mapping(value)


def _require_algorithm(algorithm: str) -> None:
    if algorithm != FINGERPRINT_ALGORITHM:
        raise UnsupportedFingerprintAlgorithm(
            f"unsupported fingerprint algorithm {algorithm!r}; "
            f"expected {FINGERPRINT_ALGORITHM!r}"
        )


__all__ = [
    "AI_CONTRACT_VERSION",
    "FINGERPRINT_ALGORITHM",
    "CanonicalDigest",
    "FingerprintError",
    "ImmutableAIReviewInput",
    "InvalidExpectedFingerprint",
    "UnsupportedFingerprintAlgorithm",
    "canonical_ai_input_bytes",
    "compute_ai_fingerprint",
    "verify_ai_fingerprint",
]
