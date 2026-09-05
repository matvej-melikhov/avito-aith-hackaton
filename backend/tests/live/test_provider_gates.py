"""Provider-specific live gates with a durable, fail-closed result ledger."""

from __future__ import annotations

import inspect
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Literal, cast

import pytest

from review_platform.application.audit import sanitize_shared_value

LEDGER_PATH = Path(__file__).with_name("gates.json")
STATUSES = frozenset({"NOT_RUN", "BLOCKED", "PASS", "FAIL"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
RUNNER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")

type GateStatus = Literal["NOT_RUN", "BLOCKED", "PASS", "FAIL"]
type GateRunnerResult = Mapping[str, object]
type GateRunner = Callable[[LiveGateDefinition], GateRunnerResult | Awaitable[GateRunnerResult]]

EXPECTED_GATES = {
    "stepik_identity",
    "stepik_course_import",
    "github_artifact",
    "google_docs_artifact",
    "ai_review",
    "stepik_delivery_reconciliation",
    "github_delivery_reconciliation",
    "email_delivery",
}


@dataclass(frozen=True, slots=True)
class LiveGateDefinition:
    gate_id: str
    provider: str
    capability: str
    required_environment: tuple[str, ...]
    runner_environment: str


def _load_ledger(path: Path = LEDGER_PATH) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _definitions() -> tuple[LiveGateDefinition, ...]:
    raw_gates = _load_ledger()["gates"]
    assert isinstance(raw_gates, list)
    return tuple(
        LiveGateDefinition(
            gate_id=str(item["id"]),
            provider=str(item["provider"]),
            capability=str(item["capability"]),
            required_environment=tuple(item["required_environment"]),
            runner_environment=str(item["runner_environment"]),
        )
        for item in raw_gates
        if isinstance(item, dict)
    )


def _utc_timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)
    return parsed


def _assert_ledger_schema(ledger: Mapping[str, object]) -> None:
    assert set(ledger) == {"schema_version", "contract_version", "recorded_at", "gates"}
    assert ledger["schema_version"] == 1
    assert ledger["contract_version"] == "1.1.0"
    _utc_timestamp(ledger["recorded_at"])
    gates = ledger["gates"]
    assert isinstance(gates, list)
    assert len(gates) == len(EXPECTED_GATES)
    assert {item["id"] for item in gates} == EXPECTED_GATES
    assert len({(item["provider"], item["capability"]) for item in gates}) == len(gates)

    required_keys = {
        "id",
        "provider",
        "capability",
        "status",
        "recorded_at",
        "last_attempted_at",
        "evidence",
        "reason",
        "required_environment",
        "runner_environment",
    }
    for item in gates:
        assert isinstance(item, dict)
        assert set(item) == required_keys
        assert item["status"] in STATUSES
        _utc_timestamp(item["recorded_at"])
        if item["last_attempted_at"] is not None:
            _utc_timestamp(item["last_attempted_at"])
        assert isinstance(item["evidence"], list)
        assert all(isinstance(value, str) and value for value in item["evidence"])
        assert isinstance(item["reason"], str) and item["reason"]
        environment = item["required_environment"]
        assert isinstance(environment, list) and len(environment) == len(set(environment))
        assert item["runner_environment"] in environment
        assert "REVIEW_PLATFORM_LIVE_PROVIDERS_ENABLED" in environment


GATE_DEFINITIONS = _definitions()
_assert_ledger_schema(_load_ledger())


def test_live_gate_ledger_has_exact_schema_statuses_and_provider_coverage() -> None:
    _assert_ledger_schema(_load_ledger())


def test_checked_in_ledger_is_truthful_and_does_not_claim_unrun_support() -> None:
    gates = _load_ledger()["gates"]
    assert isinstance(gates, list)
    assert {item["status"] for item in gates} <= {"NOT_RUN", "BLOCKED"}
    assert all(item["last_attempted_at"] is None for item in gates)
    assert all(item["evidence"] == [] for item in gates)
    serialized = json.dumps(gates, ensure_ascii=False).casefold()
    assert "bearer " not in serialized
    assert "private_key" not in serialized
    assert "local-dev-only" not in serialized


def test_missing_environment_blocks_before_runner_import_or_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = False

    def forbidden_import(_name: str) -> object:
        nonlocal imported
        imported = True
        raise AssertionError("provider runner must not load before explicit environment")

    monkeypatch.setattr(f"{__name__}.import_module", forbidden_import)
    for definition in _definitions():
        for name in definition.required_environment:
            monkeypatch.delenv(name, raising=False)
        missing = _missing_environment(definition)
        assert set(missing) == set(definition.required_environment)
    assert imported is False


def test_durable_ledger_transition_is_atomic_bounded_and_secret_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gates.json"
    path.write_bytes(LEDGER_PATH.read_bytes())
    definition = _definitions()[0]

    _record_gate(
        path,
        definition.gate_id,
        status="PASS",
        evidence=["sandbox_case=identity-roundtrip", "provider_version=fixture-live-v1"],
        reason="Authorized sandbox assertion matched the frozen contract.",
        attempted=True,
    )

    updated = _load_ledger(path)
    gates = updated["gates"]
    assert isinstance(gates, list)
    record = next(item for item in gates if item["id"] == definition.gate_id)
    assert record["status"] == "PASS"
    assert record["last_attempted_at"] is not None
    assert record["evidence"] == [
        "sandbox_case=identity-roundtrip",
        "provider_version=fixture-live-v1",
    ]
    assert not list(tmp_path.glob(".gates-*.tmp"))


@pytest.mark.live
@pytest.mark.anyio
@pytest.mark.parametrize("definition", GATE_DEFINITIONS, ids=lambda gate: gate.gate_id)
async def test_authorized_provider_live_gate(
    definition: LiveGateDefinition,
    request: pytest.FixtureRequest,
) -> None:
    assert request.config.getoption("--run-live") is True
    missing = _missing_environment(definition)
    if missing:
        reason = f"Missing explicit live gate environment: {', '.join(missing)}"
        _record_gate(
            LEDGER_PATH,
            definition.gate_id,
            status="BLOCKED",
            evidence=[],
            reason=reason,
            attempted=False,
        )
        pytest.skip(reason)

    runner = _load_runner(os.environ[definition.runner_environment])
    try:
        outcome = runner(definition)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        status, evidence, reason = _validate_runner_result(outcome)
    except Exception as error:
        reason = f"{type(error).__name__}: live gate runner failed"
        _record_gate(
            LEDGER_PATH,
            definition.gate_id,
            status="FAIL",
            evidence=[],
            reason=reason,
            attempted=True,
        )
        pytest.fail(reason, pytrace=False)

    _record_gate(
        LEDGER_PATH,
        definition.gate_id,
        status=status,
        evidence=evidence,
        reason=reason,
        attempted=True,
    )
    assert status == "PASS", reason


def _missing_environment(definition: LiveGateDefinition) -> tuple[str, ...]:
    missing: list[str] = []
    for name in definition.required_environment:
        value = os.environ.get(name)
        if not value:
            missing.append(name)
            continue
        if name.endswith("_ENABLED") and value.casefold() not in TRUE_VALUES:
            missing.append(name)
    return tuple(missing)


def _load_runner(reference: str) -> GateRunner:
    if RUNNER_PATTERN.fullmatch(reference) is None:
        raise ValueError("live gate runner must be a module:function reference")
    module_name, attribute = reference.split(":", maxsplit=1)
    candidate = getattr(import_module(module_name), attribute, None)
    if not callable(candidate):
        raise TypeError("configured live gate runner is not callable")
    return cast(GateRunner, candidate)


def _validate_runner_result(
    value: GateRunnerResult,
) -> tuple[Literal["PASS", "FAIL"], list[str], str]:
    if not isinstance(value, Mapping):
        raise TypeError("live gate runner result must be an object")
    status = value.get("status")
    evidence = value.get("evidence")
    reason = value.get("reason")
    if status not in {"PASS", "FAIL"}:
        raise ValueError("live gate runner status must be PASS or FAIL")
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) and item for item in evidence
    ):
        raise ValueError("live gate evidence must be a non-empty string list")
    if status == "PASS" and not evidence:
        raise ValueError("PASS requires durable evidence")
    if not isinstance(reason, str) or not reason:
        raise ValueError("live gate runner reason is required")
    rendered = "\n".join([*evidence, reason])
    for name, configured in os.environ.items():
        if (
            configured
            and any(marker in name for marker in ("TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY"))
            and configured in rendered
        ):
            raise ValueError("live gate result contains configured secret material")
    safe = sanitize_shared_value({"evidence": evidence, "reason": reason})
    safe_evidence = safe.get("evidence")
    safe_reason = safe.get("reason")
    if not isinstance(safe_evidence, list) or not isinstance(safe_reason, str):
        raise ValueError("live gate evidence could not be sanitized")
    return cast(Literal["PASS", "FAIL"], status), [str(item) for item in safe_evidence], safe_reason


def _record_gate(
    path: Path,
    gate_id: str,
    *,
    status: GateStatus,
    evidence: list[str],
    reason: str,
    attempted: bool,
) -> None:
    if status not in STATUSES:
        raise ValueError("unsupported live gate ledger status")
    ledger = _load_ledger(path)
    gates = ledger["gates"]
    assert isinstance(gates, list)
    record = next((item for item in gates if item.get("id") == gate_id), None)
    if not isinstance(record, dict):
        raise KeyError(f"unknown live gate: {gate_id}")
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    safe = sanitize_shared_value({"evidence": evidence, "reason": reason})
    record["status"] = status
    record["recorded_at"] = now
    record["last_attempted_at"] = now if attempted else None
    record["evidence"] = safe.get("evidence", [])
    record["reason"] = safe.get("reason", "Live gate status recorded")
    ledger["recorded_at"] = now
    encoded = (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".gates-",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
