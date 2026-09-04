from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from review_platform.domain.homework import (
    CriterionRequirement,
    HomeworkRequirements,
    RequirementValidationError,
)


def _criterion(
    key: str,
    points: str,
    *,
    position: int | None = None,
    title: str | None = None,
) -> CriterionRequirement:
    return CriterionRequirement(
        key=key,
        title=title or key.title(),
        description=f"Assess {key}",
        max_points=Decimal(points),
        position=position,
    )


def _requirements(**changes: object) -> HomeworkRequirements:
    values: dict[str, object] = {
        "student_text": "Submit code and explain the design.",
        "max_score": Decimal("10.00"),
        "artifact_kinds": ("google_docs", "github"),
        "estimated_review_minutes": 25,
        "criteria": (
            _criterion("correctness", "6.0", position=1),
            _criterion("explanation", "4.00", position=2),
        ),
    }
    values.update(changes)
    return HomeworkRequirements(**values)  # type: ignore[arg-type]


def test_digest_is_golden_deterministic_and_normalizes_decimal_and_set_order() -> None:
    first = _requirements()
    reordered = _requirements(
        max_score=Decimal("10"),
        artifact_kinds=("github", "google_docs"),
        criteria=(
            _criterion("explanation", "4", position=2),
            _criterion("correctness", "6.000", position=1),
        ),
    )

    assert first == reordered
    assert first.max_score == Decimal("10")
    assert first.artifact_kinds == ("github", "google_docs")
    assert [criterion.key for criterion in first.criteria] == ["correctness", "explanation"]
    assert first.requirements_digest == reordered.requirements_digest
    assert first.requirements_digest == (
        "sha256:89e33ff7ea112d1e0206f5e521f94cdb7e89c8fce5c52af5872f90d44cd2454b"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"student_text": "Different text"},
        {"max_score": Decimal("11"), "criteria": (_criterion("correctness", "11"),)},
        {
            "criteria": (
                _criterion("correctness", "5"),
                _criterion("explanation", "5", title="Different title"),
            )
        },
    ],
)
def test_digest_changes_for_any_requirement_content(changes: dict[str, object]) -> None:
    assert _requirements(**changes).requirements_digest != _requirements().requirements_digest


def test_implicit_positions_follow_input_order_and_are_stable() -> None:
    requirements = _requirements(
        criteria=(_criterion("first", "3"), _criterion("second", "7"))
    )

    assert [criterion.position for criterion in requirements.criteria] == [1, 2]
    assert requirements.canonical_payload()["criteria"][0]["key"] == "first"


@pytest.mark.parametrize(
    "criteria, message",
    [
        ((_criterion("same", "5"), _criterion("same", "5")), "key"),
        (
            (
                _criterion("one", "5", position=1),
                _criterion("two", "5", position=1),
            ),
            "position",
        ),
        ((_criterion("only", "9"),), "sum"),
        ((), "between 1 and 500"),
    ],
)
def test_rejects_duplicate_keys_positions_total_mismatch_and_empty_set(
    criteria: tuple[CriterionRequirement, ...],
    message: str,
) -> None:
    with pytest.raises(RequirementValidationError, match=message):
        _requirements(criteria=criteria)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"artifact_kinds": ("github", "github")}, "unique"),
        ({"artifact_kinds": ("zip",)}, "artifact"),
        ({"artifact_kinds": ()}, "at least one"),
        ({"estimated_review_minutes": 0}, "estimated"),
        ({"estimated_review_minutes": 10081}, "estimated"),
        ({"student_text": "x" * 100001}, "student_text"),
    ],
)
def test_rejects_unknown_duplicate_kinds_and_bounds(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(RequirementValidationError, match=message):
        _requirements(**changes)


def test_criterion_bounds_decimal_finiteness_and_mixed_positions_fail_closed() -> None:
    with pytest.raises(RequirementValidationError, match="non-negative finite Decimal"):
        _criterion("negative", "-1")
    with pytest.raises(RequirementValidationError, match="non-negative finite Decimal"):
        _criterion("nan", "NaN")
    with pytest.raises(RequirementValidationError, match="mixed"):
        _requirements(
            criteria=(
                _criterion("implicit", "5"),
                _criterion("explicit", "5", position=2),
            )
        )


def test_snapshots_are_deeply_immutable() -> None:
    requirements = _requirements()

    with pytest.raises(FrozenInstanceError):
        requirements.student_text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        requirements.criteria[0].title = "changed"  # type: ignore[misc]
    assert isinstance(requirements.criteria, tuple)
    assert isinstance(requirements.artifact_kinds, tuple)
