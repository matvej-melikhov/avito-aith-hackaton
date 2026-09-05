"""RED state contract for append-only, non-exclusive review responsibility."""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, Table, UniqueConstraint

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import AsyncSessionFactory

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]


def _responsibility_table() -> Table:
    table = Base.metadata.tables.get("review_responsibility")
    assert table is not None, "T116 append-only review_responsibility table is missing"
    return table


def _unique_sets(table: Table) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


async def test_responsibility_records_all_four_append_only_actions(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    table = _responsibility_table()
    assert {
        "organization_id",
        "id",
        "review_case_id",
        "review_iteration_id",
        "reviewer_id",
        "actor_id",
        "action",
        "occurred_at",
    } <= set(table.c.keys())
    checks = " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )
    for action in ("started", "joined", "released", "completed"):
        assert action in checks
    for column in table.c:
        assert column.onupdate is None


async def test_responsibility_is_nonexclusive_for_multiple_reviewers_and_methodologists(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    table = _responsibility_table()
    unique_sets = _unique_sets(table)
    # No case/iteration/reviewer action key may act as an ownership lock. Only
    # event identity can be unique; concurrent participants append distinct rows.
    forbidden = (
        {"organization_id", "review_case_id"},
        {"organization_id", "review_iteration_id"},
        {"organization_id", "review_case_id", "reviewer_id"},
        {"organization_id", "review_iteration_id", "reviewer_id"},
    )
    for columns in forbidden:
        assert frozenset(columns) not in unique_sets
    assert "exclusive_owner_id" not in set(table.c.keys())
    assert "lock_token" not in set(table.c.keys())


async def test_responsibility_history_keeps_case_level_and_iteration_level_events(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    table = _responsibility_table()
    assert not table.c.review_case_id.nullable
    assert table.c.review_iteration_id.nullable
    assert not table.c.reviewer_id.nullable
    assert not table.c.actor_id.nullable
    assert not table.c.occurred_at.nullable
