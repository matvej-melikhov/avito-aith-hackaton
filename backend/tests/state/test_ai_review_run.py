"""T093 RED state specification for durable AI review runs and attempts."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint, text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import create_database_engine

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

RUN_STATES = {
    "pending",
    "running",
    "partial",
    "succeeded",
    "retryable_failed",
    "action_required",
    "stale",
}
ATTEMPT_STATUSES = {
    "pending",
    "running",
    "partial",
    "succeeded",
    "retryable_failed",
    "action_required",
    "stale",
}


@pytest.fixture
async def ai_metadata_database(
    mysql_container: MySqlContainer,
) -> AsyncIterator[AsyncEngine]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


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


async def test_ai_review_run_attempt_retry_and_event_receipt_contract(
    ai_metadata_database: AsyncEngine,
) -> None:
    # Opening a real asyncmy connection proves the RED is model behavior, not
    # an unavailable Testcontainer or an import-time failure.
    async with ai_metadata_database.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1

    required_tables = {
        "ai_review_run",
        "ai_review_attempt",
        "ai_review_event_receipt",
    }
    missing = required_tables.difference(Base.metadata.tables)
    assert not missing, (
        "US4 AI persistence is not implemented; missing metadata tables: "
        f"{sorted(missing)}"
    )

    run = Base.metadata.tables["ai_review_run"]
    attempt = Base.metadata.tables["ai_review_attempt"]
    receipt = Base.metadata.tables["ai_review_event_receipt"]

    assert ("organization_id", "input_fingerprint") in _unique_columns(run)
    assert {
        "organization_id",
        "review_iteration_id",
        "input_fingerprint",
        "artifact_version_id",
        "content_digest",
        "homework_version_id",
        "criterion_set_id",
        "contract_version",
        "status",
        "current_attempt_no",
        "created_at",
        "finished_at",
    } <= set(run.columns.keys())
    run_checks = _check_sql(run)
    assert all(state in run_checks for state in RUN_STATES)
    assert "current_attempt_no" in run_checks

    assert ("organization_id", "ai_review_run_id", "attempt_number") in _unique_columns(
        attempt
    )
    assert {
        "organization_id",
        "ai_review_run_id",
        "attempt_number",
        "credential_binding_id",
        "credential_binding_version",
        "status",
        "last_sequence",
        "started_at",
        "finished_at",
        "error_code",
        "sanitized_error",
    } <= set(attempt.columns.keys())
    assert (
        ("organization_id", "credential_binding_id", "credential_binding_version"),
        (
            "external_credential.organization_id",
            "external_credential.id",
            "external_credential.binding_version",
        ),
    ) in _foreign_keys(attempt)
    attempt_checks = _check_sql(attempt)
    assert all(state in attempt_checks for state in ATTEMPT_STATUSES)
    assert "attempt_number" in attempt_checks and "last_sequence" in attempt_checks

    # A globally unique event identity prevents an old or duplicated attempt
    # event from changing the current run. Attempt/sequence remain explicit
    # provenance for legal retry with a newly numbered attempt.
    assert receipt.c.event_id.primary_key or ("event_id",) in _unique_columns(receipt)
    assert {
        "event_id",
        "organization_id",
        "ai_review_run_id",
        "attempt_number",
        "sequence",
        "received_at",
    } <= set(receipt.columns.keys())
    assert (
        (
            "organization_id",
            "ai_review_run_id",
            "attempt_id",
            "attempt_number",
        ),
        (
            "ai_review_attempt.organization_id",
            "ai_review_attempt.ai_review_run_id",
            "ai_review_attempt.id",
            "ai_review_attempt.attempt_number",
        ),
    ) in _foreign_keys(receipt)

    error_code_type = attempt.c.error_code.type
    assert getattr(error_code_type, "length", 129) <= 128
    assert attempt.c.sanitized_error.nullable
    assert run.c.finished_at.nullable
