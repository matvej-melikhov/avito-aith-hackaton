"""Executable RED state contract for recoverable external delivery."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import AsyncIterator
from copy import deepcopy
from types import ModuleType
from typing import Any, Protocol, cast

import pytest
from jsonschema import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.mysql import MySqlContainer
from tests.support.contracts import load_fixture

from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.publication import ExternalDelivery
from review_platform.infrastructure.db.session import create_database_engine

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

DELIVERY_STATES = {
    "pending",
    "processing",
    "retryable_failed",
    "unknown_outcome",
    "reconciling",
    "succeeded",
    "action_required",
    "superseded",
}


class ValidateTransition(Protocol):
    def __call__(
        self,
        *,
        current_state: str,
        target_state: str,
        attempt_count: int,
        max_attempts: int,
        reconciliation_observed: bool,
    ) -> str: ...


@pytest.fixture
async def delivery_state_database(
    mysql_container: MySqlContainer,
) -> AsyncIterator[AsyncEngine]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


def _table(name: str, *, task: str) -> Table:
    table = Base.metadata.tables.get(name)
    assert table is not None, f"{task} delivery persistence is missing table {name}"
    return table


def _unique_columns(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_keys(table: Table) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _check_sql(table: Table) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


async def test_external_delivery_keeps_exact_destination_binding_and_full_provenance(
    delivery_state_database: AsyncEngine,
) -> None:
    async with delivery_state_database.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1

    table = ExternalDelivery.__table__
    assert {
        "organization_id",
        "id",
        "publication_id",
        "operation_id",
        "delivery_key",
        "destination_binding_id",
        "binding_version",
        "credential_binding_id",
        "credential_binding_version",
        "destination_kind",
        "recipient_ref",
        "course_run_id",
        "homework_version_id",
        "criterion_set_id",
        "submission_version_id",
        "artifact_version_id",
        "artifact_content_digest",
        "review_iteration_id",
        "review_revision_id",
        "contract_version",
        "publication_fingerprint",
        "payload_version",
        "payload_digest",
        "payload",
        "state",
        "attempt_count",
        "next_attempt_at",
        "external_id",
        "external_url",
        "last_error_code",
        "sanitized_error",
        "revision",
    } <= set(table.c.keys())
    assert (
        "organization_id",
        "publication_id",
        "destination_binding_id",
        "binding_version",
        "payload_version",
    ) in _unique_columns(table)
    assert (
        ("organization_id", "credential_binding_id", "credential_binding_version"),
        (
            "external_credential.organization_id",
            "external_credential.id",
            "external_credential.binding_version",
        ),
    ) in _foreign_keys(table)
    checks = _check_sql(table)
    assert all(state in checks for state in DELIVERY_STATES)
    assert "attempt_count >= 0" in checks
    assert "binding_version >= 1" in checks
    assert "credential_binding_version >= 1" in checks
    assert getattr(table.c.delivery_key.type, "length", 513) <= 512
    assert getattr(table.c.recipient_ref.type, "length", 513) <= 512
    assert getattr(table.c.last_error_code.type, "length", 129) <= 128
    assert getattr(table.c.external_url.type, "length", 2049) <= 2048


def test_frozen_delivery_and_reconciliation_vectors_are_exact_and_bounded() -> None:
    registry = ContractRegistry()
    fixture = load_fixture("delivery-v1.1.0.json")
    registry.validate(
        fixture["request"],
        "delivery.schema.json",
        definition="deliver_request",
    )
    registry.validate(
        fixture["reconcile_request"],
        "delivery.schema.json",
        definition="reconcile_request",
    )
    for name in (
        "success_result",
        "unknown_result",
        "reconcile_found_result",
        "reconcile_not_found_result",
        "reconcile_action_required_result",
    ):
        registry.validate(
            fixture[name],
            "delivery.schema.json",
            definition="result",
        )

    invalid_values: list[tuple[dict[str, Any], str]] = []
    wrong_binding = deepcopy(fixture["request"])
    wrong_binding["destination"]["credential_binding_version"] = 0
    invalid_values.append((wrong_binding, "deliver_request"))
    missing_provenance = deepcopy(fixture["request"])
    del missing_provenance["provenance"]["artifact_content_digest"]
    invalid_values.append((missing_provenance, "deliver_request"))
    oversized_payload = deepcopy(fixture["request"])
    oversized_payload["payload"]["feedback"] = "x" * 50_001
    invalid_values.append((oversized_payload, "deliver_request"))
    oversized_error = deepcopy(fixture["unknown_result"])
    oversized_error["error"]["message"] = "x" * 2_049
    invalid_values.append((oversized_error, "result"))
    unknown_field = deepcopy(fixture["reconcile_request"])
    unknown_field["provider_body"] = "secret"
    invalid_values.append((unknown_field, "reconcile_request"))
    for value, definition in invalid_values:
        with pytest.raises(ValidationError):
            registry.validate(
                value,
                "delivery.schema.json",
                definition=definition,
            )


def test_delivery_attempt_history_has_numbered_exact_credential_provenance() -> None:
    attempt = _table("delivery_attempt", task="T136")
    assert {
        "organization_id",
        "id",
        "delivery_id",
        "attempt_number",
        "credential_binding_id",
        "credential_binding_version",
        "started_at",
        "finished_at",
        "outcome",
        "error_code",
        "sanitized_error",
    } <= set(attempt.c.keys())
    assert (
        "organization_id",
        "delivery_id",
        "attempt_number",
    ) in _unique_columns(attempt)
    assert (
        ("organization_id", "delivery_id"),
        ("external_delivery.organization_id", "external_delivery.id"),
    ) in _foreign_keys(attempt)
    checks = _check_sql(attempt)
    assert "attempt_number >= 1" in checks
    assert "credential_binding_version >= 1" in checks
    assert getattr(attempt.c.error_code.type, "length", 129) <= 128


def test_reconciliation_observations_are_append_only_and_attempt_linked() -> None:
    observation = _table("delivery_reconciliation_observation", task="T136")
    assert {
        "organization_id",
        "id",
        "delivery_id",
        "delivery_attempt_id",
        "observed_at",
        "outcome",
        "external_id",
        "external_url",
        "error_code",
        "sanitized_error",
    } <= set(observation.c.keys())
    assert (
        "organization_id",
        "delivery_id",
        "id",
    ) in _unique_columns(observation)
    assert (
        ("organization_id", "delivery_id", "delivery_attempt_id"),
        (
            "delivery_attempt.organization_id",
            "delivery_attempt.delivery_id",
            "delivery_attempt.id",
        ),
    ) in _foreign_keys(observation)


def test_state_policy_requires_reconciliation_and_enforces_retry_cap() -> None:
    module = _delivery_service_module()
    validate_transition = cast(
        ValidateTransition,
        module.__dict__["validate_delivery_transition"],
    )
    assert validate_transition(
        current_state="pending",
        target_state="processing",
        attempt_count=0,
        max_attempts=3,
        reconciliation_observed=False,
    ) == "processing"
    assert validate_transition(
        current_state="processing",
        target_state="unknown_outcome",
        attempt_count=1,
        max_attempts=3,
        reconciliation_observed=False,
    ) == "unknown_outcome"
    assert validate_transition(
        current_state="unknown_outcome",
        target_state="reconciling",
        attempt_count=1,
        max_attempts=3,
        reconciliation_observed=False,
    ) == "reconciling"
    assert validate_transition(
        current_state="reconciling",
        target_state="retryable_failed",
        attempt_count=1,
        max_attempts=3,
        reconciliation_observed=True,
    ) == "retryable_failed"

    invalid = (
        {
            "current_state": "unknown_outcome",
            "target_state": "processing",
            "attempt_count": 1,
            "max_attempts": 3,
            "reconciliation_observed": False,
        },
        {
            "current_state": "retryable_failed",
            "target_state": "processing",
            "attempt_count": 3,
            "max_attempts": 3,
            "reconciliation_observed": True,
        },
        {
            "current_state": "succeeded",
            "target_state": "processing",
            "attempt_count": 1,
            "max_attempts": 3,
            "reconciliation_observed": True,
        },
    )
    for transition in invalid:
        with pytest.raises(ValueError):
            validate_transition(**transition)


def _delivery_service_module() -> ModuleType:
    module_name = "review_platform.application.services.deliveries"
    specification = importlib.util.find_spec(module_name)
    assert specification is not None, "T139 delivery state service is not implemented"
    module = importlib.import_module(module_name)
    assert callable(module.__dict__.get("validate_delivery_transition")), (
        "T139 must expose validate_delivery_transition"
    )
    return module
