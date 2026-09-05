"""Honest product timing gate for SC-002 through SC-004 evidence boundaries."""

from __future__ import annotations

import inspect
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest

TRACEABILITY_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "001-backend-core"
    / "requirements-traceability.md"
)
LIVE_LEDGER_PATH = Path(__file__).resolve().parents[1] / "live" / "gates.json"
HARNESS_ENV = "REVIEW_PLATFORM_REVIEWER_WORKFLOW_HARNESS"
HARNESS_ENABLED_ENV = "REVIEW_PLATFORM_REVIEWER_WORKFLOW_ENABLED"
RUNNER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
EXPECTED_STEPS = (
    "select_reviewer_courses",
    "set_reviewer_availability",
    "recommend_next_review",
    "open_review_iteration",
)

type HarnessResult = Mapping[str, object]
type Harness = Callable[[], HarnessResult | Awaitable[HarnessResult]]


@dataclass(frozen=True, slots=True)
class SuccessEvidence:
    frontend_harness: str | None
    frontend_passed: bool
    duration_seconds: float | None
    provider_passes: frozenset[str]


def test_sc002_sc004_remain_explicitly_blocked_without_external_evidence() -> None:
    rows = _success_rows()

    assert set(rows) >= {"SC-002", "SC-003", "SC-004"}
    for criterion in ("SC-002", "SC-003", "SC-004"):
        assert rows[criterion][4].startswith("BLOCKED ")
        assert rows[criterion][4] != "PASS"
    assert "stepik_course_import" in rows["SC-002"][4]
    assert "stepik_delivery_reconciliation" in rows["SC-002"][4]
    assert "frontend" in rows["SC-002"][4].casefold()
    assert "github_artifact" in rows["SC-003"][4]
    assert "google_docs_artifact" in rows["SC-003"][4]
    assert "frontend" in rows["SC-003"][4].casefold()
    assert HARNESS_ENABLED_ENV in rows["SC-004"][4]
    assert HARNESS_ENV in rows["SC-004"][4]
    assert "120 seconds" in rows["SC-004"][4]
    assert "backend latency alone is insufficient" in rows["SC-004"][4]


def test_current_provider_ledger_cannot_promote_sc002_or_sc003() -> None:
    ledger = json.loads(LIVE_LEDGER_PATH.read_text(encoding="utf-8"))
    statuses = {item["id"]: item["status"] for item in ledger["gates"]}
    provider_passes = frozenset(
        gate_id for gate_id, status in statuses.items() if status == "PASS"
    )
    frontend_only = SuccessEvidence(
        frontend_harness="configured:fixture",
        frontend_passed=True,
        duration_seconds=1,
        provider_passes=provider_passes,
    )

    blocked = {"NOT_RUN", "BLOCKED", "FAIL"}
    assert statuses["stepik_course_import"] in blocked
    assert statuses["stepik_delivery_reconciliation"] in blocked
    assert statuses["github_artifact"] in blocked
    assert statuses["google_docs_artifact"] in blocked
    assert not _can_mark_pass("SC-002", frontend_only)
    assert not _can_mark_pass("SC-003", frontend_only)


def test_backend_or_mock_timing_alone_cannot_promote_sc004() -> None:
    backend_only = SuccessEvidence(None, True, 0.01, frozenset())
    skipped_frontend = SuccessEvidence("configured:but-skipped", False, None, frozenset())
    too_slow = SuccessEvidence("configured:real-harness", True, 120.001, frozenset())

    assert not _can_mark_pass("SC-004", backend_only)
    assert not _can_mark_pass("SC-004", skipped_frontend)
    assert not _can_mark_pass("SC-004", too_slow)
    assert _can_mark_pass(
        "SC-004",
        SuccessEvidence("configured:real-harness", True, 120, frozenset()),
    )


@pytest.mark.anyio
async def test_reviewer_selection_hours_recommend_start_frontend_gate() -> None:
    reference = os.environ.get(HARNESS_ENV)
    enabled = os.environ.get(HARNESS_ENABLED_ENV, "").casefold() in TRUE_VALUES
    if not enabled or not reference:
        pytest.skip(
            f"BLOCKED: set {HARNESS_ENABLED_ENV}=true and {HARNESS_ENV}=module:function"
        )
    harness = _load_harness(reference)
    started = time.perf_counter()
    raw = harness()
    if inspect.isawaitable(raw):
        raw = await raw
    elapsed = time.perf_counter() - started
    result = _validate_harness_result(raw)
    evidence = SuccessEvidence(
        frontend_harness=reference,
        frontend_passed=result["status"] == "PASS",
        duration_seconds=elapsed,
        provider_passes=frozenset(),
    )

    assert tuple(result["steps"]) == EXPECTED_STEPS
    assert result["evidence"]
    assert _can_mark_pass("SC-004", evidence), (
        f"reviewer frontend workflow took {elapsed:.3f}s; required <=120s"
    )


def _can_mark_pass(criterion: str, evidence: SuccessEvidence) -> bool:
    if not evidence.frontend_harness or not evidence.frontend_passed:
        return False
    if evidence.duration_seconds is None:
        return False
    if criterion == "SC-002":
        return evidence.duration_seconds <= 600 and {
            "stepik_course_import",
            "stepik_delivery_reconciliation",
        } <= evidence.provider_passes
    if criterion == "SC-003":
        return evidence.duration_seconds <= 180 and bool(
            {"github_artifact", "google_docs_artifact"} & evidence.provider_passes
        )
    if criterion == "SC-004":
        return evidence.duration_seconds <= 120
    raise ValueError(f"unsupported product success criterion: {criterion}")


def _load_harness(reference: str) -> Harness:
    if RUNNER_PATTERN.fullmatch(reference) is None:
        raise ValueError("frontend harness must be configured as module:function")
    module_name, attribute = reference.split(":", maxsplit=1)
    candidate = getattr(import_module(module_name), attribute, None)
    if not callable(candidate):
        raise TypeError("configured frontend harness is not callable")
    return cast(Harness, candidate)


def _validate_harness_result(value: HarnessResult) -> HarnessResult:
    if not isinstance(value, Mapping):
        raise TypeError("frontend harness result must be an object")
    if value.get("status") not in {"PASS", "FAIL"}:
        raise ValueError("frontend harness status must be PASS or FAIL")
    steps = value.get("steps")
    evidence = value.get("evidence")
    if not isinstance(steps, list) or not all(isinstance(item, str) for item in steps):
        raise ValueError("frontend harness must report ordered workflow steps")
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) and item for item in evidence
    ):
        raise ValueError("frontend harness must report non-empty evidence strings")
    return value


def _success_rows() -> dict[str, tuple[str, ...]]:
    rows: dict[str, tuple[str, ...]] = {}
    for line in TRACEABILITY_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| SC-"):
            continue
        columns = tuple(part.strip() for part in line.strip("|").split("|"))
        if len(columns) == 5:
            rows[columns[0]] = columns
    return rows
