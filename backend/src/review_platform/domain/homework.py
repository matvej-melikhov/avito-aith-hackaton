"""Immutable homework requirement snapshots and canonical content digests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from review_platform.domain.primitives import canonical_json_sha256

type ArtifactKind = Literal["github", "google_docs"]
type CanonicalScalar = str | int
type CanonicalValue = CanonicalScalar | list["CanonicalValue"] | dict[str, "CanonicalValue"]

_ARTIFACT_ORDER: tuple[ArtifactKind, ...] = ("github", "google_docs")


class RequirementValidationError(ValueError):
    """Homework requirement content violates the frozen command contract."""


@dataclass(frozen=True, slots=True)
class CriterionRequirement:
    """One stable, immutable criterion in a requirement snapshot."""

    key: str
    title: str
    description: str
    max_points: Decimal
    position: int | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.key, field_name="criterion key", minimum=1, maximum=128)
        _bounded_text(self.title, field_name="criterion title", minimum=1, maximum=512)
        _bounded_text(
            self.description,
            field_name="criterion description",
            minimum=0,
            maximum=20_000,
        )
        object.__setattr__(
            self,
            "max_points",
            _nonnegative_decimal(self.max_points, field_name="criterion max_points"),
        )
        if self.position is not None and (
            not isinstance(self.position, int)
            or isinstance(self.position, bool)
            or self.position < 1
        ):
            raise RequirementValidationError("criterion position must be a positive integer")

    def canonical_payload(self) -> dict[str, CanonicalValue]:
        if self.position is None:
            raise RequirementValidationError("criterion position must be resolved before digest")
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "max_points": _decimal_text(self.max_points),
            "position": self.position,
        }


@dataclass(frozen=True, slots=True)
class HomeworkRequirements:
    """Validated full content of one immutable HomeworkVersion."""

    student_text: str
    max_score: Decimal
    artifact_kinds: Sequence[ArtifactKind]
    estimated_review_minutes: int
    criteria: Sequence[CriterionRequirement]
    requirements_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _bounded_text(
            self.student_text,
            field_name="student_text",
            minimum=0,
            maximum=100_000,
        )
        score = _nonnegative_decimal(self.max_score, field_name="HomeworkVersion max_score")
        object.__setattr__(self, "max_score", score)
        object.__setattr__(self, "artifact_kinds", _artifact_kinds(self.artifact_kinds))
        if (
            not isinstance(self.estimated_review_minutes, int)
            or isinstance(self.estimated_review_minutes, bool)
            or not 1 <= self.estimated_review_minutes <= 10_080
        ):
            raise RequirementValidationError(
                "estimated_review_minutes must be between 1 and 10080"
            )
        criteria = _criteria(self.criteria)
        if sum((criterion.max_points for criterion in criteria), Decimal("0")) != score:
            raise RequirementValidationError(
                "sum of criterion max_points must exactly equal HomeworkVersion max_score"
            )
        object.__setattr__(self, "criteria", criteria)
        object.__setattr__(
            self,
            "requirements_digest",
            canonical_json_sha256(self.canonical_payload()),
        )

    def canonical_payload(self) -> dict[str, CanonicalValue]:
        """Return the exact stable JSON value hashed by ``requirements_digest``."""

        return {
            "schema_version": "1.1.0",
            "student_text": self.student_text,
            "max_score": _decimal_text(self.max_score),
            "artifact_kinds": list(self.artifact_kinds),
            "estimated_review_minutes": self.estimated_review_minutes,
            "criteria": [criterion.canonical_payload() for criterion in self.criteria],
        }


def _criteria(values: Sequence[CriterionRequirement]) -> tuple[CriterionRequirement, ...]:
    criteria = tuple(values)
    if not 1 <= len(criteria) <= 500:
        raise RequirementValidationError("criteria count must be between 1 and 500")
    if not all(isinstance(criterion, CriterionRequirement) for criterion in criteria):
        raise RequirementValidationError("criteria must contain CriterionRequirement values")

    explicit = [criterion.position is not None for criterion in criteria]
    if any(explicit) and not all(explicit):
        raise RequirementValidationError("mixed explicit and implicit criterion positions")
    if all(explicit):
        positions = [criterion.position for criterion in criteria]
        if len(set(positions)) != len(positions):
            raise RequirementValidationError("criterion positions must be unique")
        resolved = tuple(sorted(criteria, key=lambda criterion: criterion.position or 0))
    else:
        resolved = tuple(
            replace(criterion, position=index)
            for index, criterion in enumerate(criteria, start=1)
        )

    keys = [criterion.key for criterion in resolved]
    if len(set(keys)) != len(keys):
        raise RequirementValidationError("criterion stable keys must be unique")
    return resolved


def _artifact_kinds(values: Sequence[ArtifactKind]) -> tuple[ArtifactKind, ...]:
    kinds = tuple(values)
    if not kinds:
        raise RequirementValidationError("at least one artifact kind is required")
    if len(kinds) > 2:
        raise RequirementValidationError("at most two artifact kinds are allowed")
    if len(set(kinds)) != len(kinds):
        raise RequirementValidationError("artifact kinds must be unique")
    unknown = set(kinds).difference(_ARTIFACT_ORDER)
    if unknown:
        raise RequirementValidationError(f"unknown artifact kind(s): {sorted(unknown)!r}")
    return tuple(kind for kind in _ARTIFACT_ORDER if kind in kinds)


def _nonnegative_decimal(value: Decimal, *, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise RequirementValidationError(
            f"{field_name} must be a non-negative finite Decimal"
        )
    if value == 0:
        return Decimal("0")
    return value.normalize()


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _bounded_text(value: str, *, field_name: str, minimum: int, maximum: int) -> None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise RequirementValidationError(
            f"{field_name} length must be between {minimum} and {maximum} characters"
        )


__all__ = [
    "ArtifactKind",
    "CriterionRequirement",
    "HomeworkRequirements",
    "RequirementValidationError",
]
