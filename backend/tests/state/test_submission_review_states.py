"""RED state contract for submission versions and explicit initial review opening."""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, Table, UniqueConstraint

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import AsyncSessionFactory

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

REQUIRED_TABLES = (
    "submission",
    "submission_version",
    "artifact_reference",
    "artifact_version",
    "review_case",
    "review_iteration",
)


def _tables() -> dict[str, Table]:
    result: dict[str, Table] = {}
    for name in REQUIRED_TABLES:
        table = Base.metadata.tables.get(name)
        assert table is not None, f"US3 state table is missing: {name}"
        result[name] = table
    return result


def _check_sql(table: Table) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def _unique_column_sets(table: Table) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


async def test_submission_version_state_and_immutable_requirement_snapshots(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    tables = _tables()
    submission = tables["submission"]
    version = tables["submission_version"]

    assert {
        "organization_id",
        "id",
        "course_run_id",
        "homework_id",
        "student_id",
        "current_predeadline_version_id",
        "revision",
    } <= set(submission.c.keys())
    assert {
        "organization_id",
        "id",
        "submission_id",
        "course_run_id",
        "homework_id",
        "sequence",
        "homework_version_id",
        "artifact_reference_id",
        "artifact_version_id",
        "submitted_at",
        "effective_deadline",
        "phase",
        "status",
        "revision",
        "capture_operation_id",
    } <= set(version.c.keys())
    checks = _check_sql(version)
    for state in ("validating", "ready", "access_error", "pending_review", "superseded"):
        assert state in checks
    for phase in ("before_deadline", "revision"):
        assert phase in checks
    assert frozenset({"organization_id", "submission_id", "sequence"}) in (
        _unique_column_sets(version)
    )
    # These values are copied into an immutable version. They are neither
    # global pointers nor fields with an automatic on-update mutation.
    assert version.c.homework_version_id.onupdate is None
    assert version.c.effective_deadline.onupdate is None
    assert version.c.artifact_reference_id.onupdate is None


async def test_predeadline_replacement_and_late_pending_are_explicit_not_automatic_review(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    tables = _tables()
    submission = tables["submission"]
    version = tables["submission_version"]

    submission_columns = set(submission.c.keys())
    version_columns = set(version.c.keys())
    assert "current_predeadline_version_id" in submission_columns
    assert "current_review_iteration_id" not in submission_columns
    assert "review_iteration_id" not in version_columns
    assert "pending_review" in _check_sql(version)
    assert "superseded" in _check_sql(version)
    # Ready replacement is represented by the Submission pointer plus the old
    # version's superseded state. A late version stays pending until T085's
    # explicit open command; no FK from SubmissionVersion auto-opens review.
    assert submission.c.current_predeadline_version_id.nullable
    assert version.c.status.onupdate is None


async def test_explicit_initial_iteration_has_one_current_pointer_and_terminal_non_regression_shape(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    del foundation_session_factory
    tables = _tables()
    review_case = tables["review_case"]
    iteration = tables["review_iteration"]

    assert {
        "organization_id",
        "id",
        "course_run_id",
        "homework_id",
        "student_id",
        "current_iteration_id",
        "revision",
    } <= set(review_case.c.keys())
    assert {
        "organization_id",
        "id",
        "review_case_id",
        "course_run_id",
        "homework_id",
        "student_id",
        "iteration_number",
        "submission_version_id",
        "initial_submission_version_id",
        "artifact_version_id",
        "homework_version_id",
        "criterion_set_id",
        "status",
        "current_revision_id",
        "revision",
        "predecessor_iteration_id",
        "origin",
    } <= set(iteration.c.keys())
    assert frozenset(
        {"organization_id", "course_run_id", "homework_id", "student_id"}
    ) in _unique_column_sets(review_case)
    uniques = _unique_column_sets(iteration)
    assert frozenset({"organization_id", "review_case_id", "iteration_number"}) in uniques
    assert frozenset({"organization_id", "initial_submission_version_id"}) in uniques
    checks = _check_sql(iteration)
    for state in ("queued", "in_review", "ready_to_publish", "published", "canceled"):
        assert state in checks
    for origin in ("initial", "resubmission", "correction", "requirements_migration"):
        assert origin in checks
    # Selection and terminal progression are CAS state, not mutable input
    # identity. No immutable provenance field can change on row update.
    assert review_case.c.current_iteration_id.nullable
    for name in (
        "submission_version_id",
        "artifact_version_id",
        "homework_version_id",
        "criterion_set_id",
        "origin",
    ):
        assert iteration.c[name].onupdate is None
