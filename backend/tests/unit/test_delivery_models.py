"""Structural and MySQL DDL checks for delivery recovery history."""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint, text
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base, UTCDateTime
from review_platform.infrastructure.db.models import (
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
from review_platform.infrastructure.db.models.delivery import (
    DELIVERY_ATTEMPT_OUTCOMES,
    DELIVERY_RECONCILIATION_OUTCOMES,
)
from review_platform.infrastructure.db.session import create_database_engine


def _unique_sets(table: Table) -> set[tuple[str, ...]]:
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


def _checks(table: Table) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def test_delivery_recovery_tables_are_registered() -> None:
    assert Base.metadata.tables["delivery_attempt"] is DeliveryAttempt.__table__
    assert (
        Base.metadata.tables["delivery_reconciliation_observation"]
        is DeliveryReconciliationObservation.__table__
    )


def test_attempt_has_numbered_delivery_operation_credential_and_lease_identity() -> None:
    table = DeliveryAttempt.__table__
    assert {
        "delivery_id",
        "external_delivery_id",
        "operation_id",
        "attempt_number",
        "credential_binding_id",
        "credential_binding_version",
        "worker_identity",
        "claim_token",
        "lease_expires_at",
        "state",
        "started_at",
        "finished_at",
        "outcome",
        "error_code",
        "sanitized_error",
    } <= set(table.columns.keys())
    uniques = _unique_sets(table)
    assert ("organization_id", "delivery_id", "attempt_number") in uniques
    assert (
        "organization_id",
        "external_delivery_id",
        "attempt_number",
    ) in uniques
    assert ("organization_id", "delivery_id", "id") in uniques
    assert ("organization_id", "claim_token") in uniques
    foreign_keys = _foreign_keys(table)
    assert (
        ("organization_id", "delivery_id"),
        ("external_delivery.organization_id", "external_delivery.id"),
    ) in foreign_keys
    assert (
        ("organization_id", "operation_id"),
        ("operation.organization_id", "operation.id"),
    ) in foreign_keys
    assert (
        ("organization_id", "credential_binding_id", "credential_binding_version"),
        (
            "external_credential.organization_id",
            "external_credential.id",
            "external_credential.binding_version",
        ),
    ) in foreign_keys
    checks = _checks(table)
    assert all(f"'{outcome}'" in checks for outcome in DELIVERY_ATTEMPT_OUTCOMES)
    assert "delivery_id = external_delivery_id" in checks
    assert "state = outcome" in checks
    assert "attempt_number >= 1" in checks
    assert "lease_expires_at >= started_at" in checks
    assert "sanitized_error" in checks and "2048" in checks
    assert isinstance(table.c.started_at.type, UTCDateTime)
    assert isinstance(table.c.lease_expires_at.type, UTCDateTime)
    assert getattr(table.c.worker_identity.type, "length", 256) <= 255
    assert getattr(table.c.error_code.type, "length", 129) <= 128


def test_reconciliation_observation_is_append_only_and_attempt_affine() -> None:
    table = DeliveryReconciliationObservation.__table__
    assert {
        "delivery_id",
        "external_delivery_id",
        "delivery_attempt_id",
        "attempt_number",
        "operation_id",
        "credential_binding_id",
        "credential_binding_version",
        "request_payload_version",
        "request_digest",
        "result_payload_version",
        "result_digest",
        "observed_at",
        "outcome",
        "external_id",
        "external_url",
        "error_code",
        "sanitized_error",
    } <= set(table.columns.keys())
    assert ("organization_id", "delivery_id", "id") in _unique_sets(table)
    assert (
        ("organization_id", "delivery_id", "delivery_attempt_id"),
        (
            "delivery_attempt.organization_id",
            "delivery_attempt.delivery_id",
            "delivery_attempt.id",
        ),
    ) in _foreign_keys(table)
    assert (
        (
            "organization_id",
            "delivery_id",
            "delivery_attempt_id",
            "attempt_number",
            "operation_id",
            "credential_binding_id",
            "credential_binding_version",
        ),
        (
            "delivery_attempt.organization_id",
            "delivery_attempt.delivery_id",
            "delivery_attempt.id",
            "delivery_attempt.attempt_number",
            "delivery_attempt.operation_id",
            "delivery_attempt.credential_binding_id",
            "delivery_attempt.credential_binding_version",
        ),
    ) in _foreign_keys(table)
    checks = _checks(table)
    assert all(f"'{outcome}'" in checks for outcome in DELIVERY_RECONCILIATION_OUTCOMES)
    assert "request_digest" in checks and "result_digest" in checks
    assert "delivery_id = external_delivery_id" in checks
    assert "sanitized_error" in checks and "2048" in checks
    assert isinstance(table.c.observed_at.type, UTCDateTime)
    assert {"revision", "created_at", "updated_at"}.isdisjoint(table.columns.keys())
    assert all(column.onupdate is None for column in table.columns)
    assert getattr(table.c.external_id.type, "length", 513) <= 512
    assert getattr(table.c.external_url.type, "length", 2049) <= 2048
    assert getattr(table.c.error_code.type, "length", 129) <= 128


@pytest.mark.infrastructure
@pytest.mark.anyio
async def test_mysql_creates_delivery_recovery_fk_graph(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
