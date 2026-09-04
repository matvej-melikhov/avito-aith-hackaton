"""RED state contract for immutable human review revisions and AI separation."""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import AsyncSessionFactory

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

HUMAN_TABLES = (
    "review_revision",
    "review_criterion_decision",
    "review_note",
)
AI_TABLES = (
    "ai_review_run",
    "ai_review_event_receipt",
    "ai_criterion_suggestion",
    "ai_signal",
)


def _tables(*names: str) -> dict[str, Table]:
    result: dict[str, Table] = {}
    for name in names:
        table = Base.metadata.tables.get(name)
        assert table is not None, f"US4 review-spine table is missing: {name}"
        result[name] = table
    return result


def _unique_columns(table: Table) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_sql(table: Table) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


async def test_human_revisions_are_append_only_complete_snapshots_with_expected_current_cas(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    tables = _tables(*HUMAN_TABLES, "review_iteration")
    revision = tables["review_revision"]
    iteration = tables["review_iteration"]

    assert {
        "organization_id",
        "id",
        "review_iteration_id",
        "revision_number",
        "author_user_id",
        "base_revision_id",
        "feedback",
        "total_score",
        "created_at",
    } <= set(revision.c.keys())
    assert frozenset(
        {"organization_id", "review_iteration_id", "revision_number"}
    ) in _unique_columns(revision)
    assert {"current_revision_id", "revision"} <= set(iteration.c.keys())
    assert iteration.c.current_revision_id.nullable
    # Human snapshots are appended, then only ReviewIteration's CAS pointer is
    # advanced. Snapshot bytes have no automatic update hook.
    for name in (
        "review_iteration_id",
        "revision_number",
        "author_user_id",
        "base_revision_id",
        "feedback",
        "total_score",
        "created_at",
    ):
        assert revision.c[name].onupdate is None


async def test_decisions_and_notes_form_exact_criterion_and_global_human_snapshot(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    tables = _tables(*HUMAN_TABLES)
    decision = tables["review_criterion_decision"]
    note = tables["review_note"]

    assert {
        "organization_id",
        "id",
        "review_revision_id",
        "criterion_id",
        "ai_suggestion_id",
        "points",
        "decision",
        "reason",
        "evidence_ids",
    } <= set(decision.c.keys())
    assert frozenset(
        {"organization_id", "review_revision_id", "criterion_id"}
    ) in _unique_columns(decision)
    checks = _check_sql(decision)
    for value in ("accepted", "changed", "manual"):
        assert value in checks
    assert "points >= 0" in checks
    for name in ("criterion_id", "points", "decision", "reason", "evidence_ids"):
        assert decision.c[name].onupdate is None

    assert {
        "organization_id",
        "id",
        "review_revision_id",
        "criterion_id",
        "text",
        "author_user_id",
        "position",
    } <= set(note.c.keys())
    assert note.c.criterion_id.nullable
    assert frozenset(
        {"organization_id", "review_revision_id", "position"}
    ) in _unique_columns(note)
    for name in ("criterion_id", "text", "author_user_id", "position"):
        assert note.c[name].onupdate is None


async def test_ai_state_is_separate_and_cannot_own_or_mutate_human_snapshot_bytes(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    tables = _tables(*HUMAN_TABLES, *AI_TABLES)
    human_names = set(HUMAN_TABLES)

    for ai_name in AI_TABLES:
        ai_table = tables[ai_name]
        assert ai_table.name not in human_names
        # AI may reference immutable review/criterion identities, but no human
        # snapshot table may cascade from or be owned by an AI row.
        for constraint in ai_table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            for element in constraint.elements:
                assert element.column.table.name not in human_names

    revision = tables["review_revision"]
    decision = tables["review_criterion_decision"]
    note = tables["review_note"]
    assert "ai_review_run_id" not in set(revision.c.keys())
    assert "ai_review_run_id" not in set(note.c.keys())
    # Optional provenance from a human decision to a suggestion is permitted;
    # it does not grant the AI table any update path back into human bytes.
    assert decision.c.ai_suggestion_id.nullable
    for table in (revision, decision, note):
        assert all(column.onupdate is None for column in table.c)
