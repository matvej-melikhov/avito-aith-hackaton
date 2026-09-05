"""Structural invariants for reviewer work preference and participation models."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.sql.schema import Table

from review_platform.infrastructure.db.base import Base, UTCDateTime
from review_platform.infrastructure.db.models import (
    AvailabilityPlan,
    ReviewerCourseSelection,
    ReviewResponsibility,
)
from review_platform.infrastructure.db.models.review_work import (
    REVIEW_RESPONSIBILITY_ACTIONS,
)


def _unique_sets(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_pairs(table: Table) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _check_sql(table: Table) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _index_sets(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in index.columns)
        for index in table.indexes
        if isinstance(index, Index)
    }


def test_review_work_models_are_discoverable_from_base_metadata() -> None:
    assert Base.metadata.tables["availability_plan"] is AvailabilityPlan.__table__
    assert Base.metadata.tables["reviewer_course_selection"] is ReviewerCourseSelection.__table__
    assert Base.metadata.tables["review_responsibility"] is ReviewResponsibility.__table__


def test_availability_is_one_advisory_cas_plan_per_tenant_reviewer() -> None:
    table = AvailabilityPlan.__table__
    assert ("organization_id", "id") in _unique_sets(table)
    assert ("organization_id", "reviewer_id") in _unique_sets(table)
    assert (
        ("organization_id", "reviewer_id"),
        (
            "organization_membership.organization_id",
            "organization_membership.user_id",
        ),
    ) in _foreign_key_pairs(table)
    assert "planned_minutes >= 0" in _check_sql(table)
    assert "revision >= 0" in _check_sql(table)
    assert ("organization_id", "until_at") in _index_sets(table)
    assert isinstance(table.c.until_at.type, UTCDateTime)
    assert not table.c.planned_minutes.nullable
    assert not table.c.until_at.nullable

    # This is deliberately an advisory signal rather than an assignment cap.
    planned_minutes_checks = [sql for sql in _check_sql(table) if "planned_minutes" in sql]
    assert planned_minutes_checks == ["planned_minutes >= 0"]


def test_course_selection_is_exact_tenant_reviewer_and_course_run_state() -> None:
    table = ReviewerCourseSelection.__table__
    assert ("organization_id", "id") in _unique_sets(table)
    assert (
        "organization_id",
        "course_run_id",
        "reviewer_id",
    ) in _unique_sets(table)
    foreign_keys = _foreign_key_pairs(table)
    assert (
        ("organization_id", "course_run_id"),
        ("course_run.organization_id", "course_run.id"),
    ) in foreign_keys
    assert (
        ("organization_id", "reviewer_id"),
        (
            "organization_membership.organization_id",
            "organization_membership.user_id",
        ),
    ) in foreign_keys
    assert not table.c.active.nullable
    assert ("organization_id", "reviewer_id", "active") in _index_sets(table)
    assert ("organization_id", "course_run_id", "active") in _index_sets(table)


def test_responsibility_is_append_only_and_does_not_encode_exclusive_ownership() -> None:
    table = ReviewResponsibility.__table__
    assert {
        "organization_id",
        "id",
        "review_case_id",
        "review_iteration_id",
        "reviewer_id",
        "actor_id",
        "action",
        "occurred_at",
    } == set(table.columns.keys())
    assert all(column.onupdate is None for column in table.columns)
    assert {"revision", "created_at", "updated_at", "exclusive_owner_id", "lock_token"}.isdisjoint(
        table.columns.keys()
    )
    assert not table.c.review_case_id.nullable
    assert table.c.review_iteration_id.nullable
    assert not table.c.reviewer_id.nullable
    assert not table.c.actor_id.nullable
    assert not table.c.occurred_at.nullable
    assert isinstance(table.c.occurred_at.type, UTCDateTime)

    unique_sets = _unique_sets(table)
    assert unique_sets == {("organization_id", "id")}
    assert not any("reviewer" in columns or "review_case" in columns for columns in unique_sets)


def test_responsibility_actions_and_relation_affinity_are_exact() -> None:
    table = ReviewResponsibility.__table__
    checks = " ".join(_check_sql(table))
    assert REVIEW_RESPONSIBILITY_ACTIONS == ("started", "joined", "released", "completed")
    assert all(f"'{action}'" in checks for action in REVIEW_RESPONSIBILITY_ACTIONS)

    foreign_keys = _foreign_key_pairs(table)
    assert (
        ("organization_id", "review_case_id"),
        ("review_case.organization_id", "review_case.id"),
    ) in foreign_keys
    assert (
        ("organization_id", "review_case_id", "review_iteration_id"),
        (
            "review_iteration.organization_id",
            "review_iteration.review_case_id",
            "review_iteration.id",
        ),
    ) in foreign_keys
    membership_target = (
        "organization_membership.organization_id",
        "organization_membership.user_id",
    )
    assert (("organization_id", "reviewer_id"), membership_target) in foreign_keys
    assert (("organization_id", "actor_id"), membership_target) in foreign_keys

    indexes = _index_sets(table)
    assert ("organization_id", "review_case_id", "occurred_at", "id") in indexes
    assert ("organization_id", "review_iteration_id", "occurred_at", "id") in indexes
    assert ("organization_id", "reviewer_id", "occurred_at", "id") in indexes
