"""Focused structural tests for the Foundation persistence contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from inspect import signature
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import mysql

from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
    SqlRevisionStore,
)
from review_platform.infrastructure.db.base import Base, UTCDateTime
from review_platform.infrastructure.db.models import Operation, OperationAttempt
from review_platform.infrastructure.db.repositories.operations import (
    CommandReceiptRepository,
    OperationRepository,
    OutboxMessageRepository,
)
from review_platform.infrastructure.db.session import asyncmy_url


def test_mysql_urls_are_normalized_to_asyncmy_without_losing_connection_data() -> None:
    url = asyncmy_url(
        "mysql+pymysql://review_platform:secret@db.example.test:3307/reviews?charset=utf8mb4"
    )

    assert url.drivername == "mysql+asyncmy"
    assert url.host == "db.example.test"
    assert url.port == 3307
    assert url.database == "reviews"
    assert url.query["charset"] == "utf8mb4"


def test_utc_datetime_rejects_naive_values_and_restores_utc_awareness() -> None:
    column_type = UTCDateTime()
    dialect = mysql.dialect()

    with pytest.raises(ValueError, match="timezone-aware"):
        column_type.process_bind_param(datetime(2026, 9, 4, 12, 0), dialect)

    stored = column_type.process_bind_param(
        datetime(2026, 9, 4, 15, 0, tzinfo=timezone(timedelta(hours=3))),
        dialect,
    )
    restored = column_type.process_result_value(stored, dialect)
    assert restored == datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def test_foundation_metadata_has_deterministic_named_constraints() -> None:
    expected_tables = {
        "organization",
        "command_receipt",
        "audit_event",
        "operation",
        "operation_attempt",
        "outbox_message",
    }

    assert expected_tables.issubset(Base.metadata.tables)
    assert all(
        constraint.name
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
    )


def test_every_foundational_tenant_entity_exposes_a_composite_candidate_key() -> None:
    identity_by_table = {
        "command_receipt": "id",
        "audit_event": "id",
        "operation": "id",
        "operation_attempt": "id",
        "outbox_message": "message_id",
    }

    for table_name, identity_column in identity_by_table.items():
        table = Base.metadata.tables[table_name]
        unique_column_sets = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert ("organization_id", identity_column) in unique_column_sets


def test_operation_attempt_fk_carries_the_organization_boundary() -> None:
    table = OperationAttempt.__table__
    foreign_keys = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    ]
    operation_fk = next(
        constraint
        for constraint in foreign_keys
        if constraint.referred_table.name == Operation.__tablename__
    )

    assert tuple(column.name for column in operation_fk.columns) == (
        "organization_id",
        "operation_id",
    )
    assert tuple(element.target_fullname for element in operation_fk.elements) == (
        "operation.organization_id",
        "operation.id",
    )


def test_attempt_number_is_unique_per_tenant_operation_and_relationship_is_ordered() -> None:
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in OperationAttempt.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    relationship = Operation.__mapper__.relationships["attempts"]

    assert ("organization_id", "operation_id", "attempt_number") in unique_column_sets
    assert tuple(column.name for column in relationship.order_by) == ("attempt_number",)


def test_outbox_lock_statement_uses_skip_locked_and_attempt_budget() -> None:
    statement = OutboxMessageRepository.available_statement(
        now=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        limit=5,
    )
    rendered = str(statement.compile(dialect=mysql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in rendered
    assert "ATTEMPTS < OUTBOX_MESSAGE.MAX_ATTEMPTS" in rendered
    assert "LEASE_EXPIRES_AT" in rendered


def test_repository_entity_lookups_always_require_organization_id() -> None:
    for method in (
        CommandReceiptRepository.get,
        CommandReceiptRepository.get_by_idempotency_key,
        OperationRepository.get,
        OutboxMessageRepository.get,
    ):
        parameters = signature(method).parameters
        assert "organization_id" in parameters
        assert parameters["organization_id"].default is parameters["organization_id"].empty


@pytest.mark.anyio
async def test_sql_application_adapters_reject_non_session_transactions() -> None:
    with pytest.raises(TypeError, match="AsyncSession"):
        await SqlRevisionStore().lock_and_check(
            organization_id=UUID("00000000-0000-7000-8000-000000000001"),
            revision_target="organization",
            target_id=UUID("00000000-0000-7000-8000-000000000001"),
            expected_revision=0,
            transaction=object(),
        )

    assert SqlIdempotencyReceiptRepository() is not None
    assert SqlAppendOnlyAuditRepository() is not None


def test_alembic_history_has_one_current_head_and_preserves_foundation_root() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0002_identity_and_courses"]
    revision = scripts.get_revision("0001_foundation")
    assert revision is not None
    assert revision.down_revision is None
