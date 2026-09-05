"""Golden and negative tests for the frozen AI input fingerprint."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy

import pytest
import rfc8785
from pydantic import ValidationError
from tests.support.contracts import load_fixture

from review_platform.domain.ai_fingerprint import (
    FINGERPRINT_ALGORITHM,
    ImmutableAIReviewInput,
    InvalidExpectedFingerprint,
    UnsupportedFingerprintAlgorithm,
    canonical_ai_input_bytes,
    compute_ai_fingerprint,
    verify_ai_fingerprint,
        )

def _vector() -> dict[str, object]:
    return load_fixture("ai-fingerprint-v1.1.0.json")


def _input() -> dict[str, object]:
    return cast_dict(_vector()["input"])


def cast_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return dict(value)


def test_golden_fixture_matches_exact_rfc8785_bytes_and_expected_digest() -> None:
    vector = _vector()
    source = _input()
    immutable = ImmutableAIReviewInput.model_validate(source)

    assert vector["algorithm"] == FINGERPRINT_ALGORITHM
    assert canonical_ai_input_bytes(immutable) == rfc8785.dumps(source)
    assert compute_ai_fingerprint(immutable) == vector["expected_fingerprint"]
    assert verify_ai_fingerprint(immutable, str(vector["expected_fingerprint"]))


def test_mapping_key_order_does_not_change_canonical_bytes_or_digest() -> None:
    original = _input()
    reversed_input = OrderedDict(reversed(tuple(original.items())))

    assert canonical_ai_input_bytes(original) == canonical_ai_input_bytes(reversed_input)
    assert compute_ai_fingerprint(original) == compute_ai_fingerprint(reversed_input)


@pytest.mark.parametrize(
    "field",
    [
        "organization_id",
        "course_run_id",
        "submission_version_id",
        "review_iteration_id",
        "artifact_version_id",
        "homework_version_id",
        "criterion_set_id",
    ],
)
def test_each_identity_change_changes_fingerprint(field: str) -> None:
    original = _input()
    changed = deepcopy(original)
    changed[field] = "00000000-0000-7000-8000-000000000099"

    assert compute_ai_fingerprint(changed) != compute_ai_fingerprint(original)


@pytest.mark.parametrize(
    "field",
    [
        "artifact_content_digest",
        "homework_digest",
        "criterion_set_digest",
    ],
)
def test_each_content_digest_change_changes_fingerprint(field: str) -> None:
    original = _input()
    changed = deepcopy(original)
    changed[field] = "sha256:" + "f" * 64

    assert compute_ai_fingerprint(changed) != compute_ai_fingerprint(original)


@pytest.mark.parametrize(
    "field",
    [
        "contract_version",
        "organization_id",
        "course_run_id",
        "submission_version_id",
        "review_iteration_id",
        "artifact_version_id",
        "artifact_content_digest",
        "homework_version_id",
        "homework_digest",
        "criterion_set_id",
        "criterion_set_digest",
    ],
)
def test_missing_fields_are_rejected(field: str) -> None:
    value = _input()
    del value[field]

    with pytest.raises(ValidationError):
        ImmutableAIReviewInput.model_validate(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_version", "1.0.0"),
        ("organization_id", "not-a-uuid"),
        ("artifact_content_digest", "sha256:" + "A" * 64),
        ("homework_digest", "not-a-digest"),
        ("criterion_set_digest", "sha512:" + "0" * 128),
    ],
)
def test_unsupported_version_invalid_uuids_and_noncanonical_digests_are_rejected(
    field: str,
    value: str,
) -> None:
    invalid = _input()
    invalid[field] = value

    with pytest.raises(ValidationError):
        ImmutableAIReviewInput.model_validate(invalid)


def test_extra_fields_and_attempt_credentials_are_explicitly_excluded() -> None:
    for extra in (
        {"unexpected": True},
        {
            "credential_binding_id": "00000000-0000-7000-8000-000000000051",
            "credential_binding_version": 7,
        },
    ):
        invalid = {**_input(), **extra}
        with pytest.raises(ValidationError):
            ImmutableAIReviewInput.model_validate(invalid)


def test_algorithm_and_expected_digest_fail_closed() -> None:
    with pytest.raises(UnsupportedFingerprintAlgorithm):
        compute_ai_fingerprint(_input(), algorithm="plain-sha256")
    with pytest.raises(InvalidExpectedFingerprint):
        verify_ai_fingerprint(_input(), "sha256:NOT-CANONICAL")
    assert verify_ai_fingerprint(_input(), "sha256:" + "f" * 64) is False


def test_value_object_is_immutable() -> None:
    immutable = ImmutableAIReviewInput.model_validate(_input())

    with pytest.raises(ValidationError):
        immutable.organization_id = immutable.course_run_id
