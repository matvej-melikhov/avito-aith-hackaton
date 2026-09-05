"""T111 RED state contract for complete human publication and successors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewRevision,
)
from review_platform.infrastructure.db.session import create_database_engine

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


@pytest.fixture
async def review_state_database(
    mysql_container: MySqlContainer,
) -> AsyncIterator[AsyncEngine]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_complete_scored_revision_cas_publication_and_successor_contract(
    review_state_database: AsyncEngine,
) -> None:
    async with review_state_database.connect() as connection:
        assert await connection.scalar(ReviewRevision.__table__.select().limit(0)) is None

    assert (
        "organization_id",
        "review_revision_id",
        "criterion_id",
    ) in _unique_columns("review_criterion_decision")
    assert isinstance(ReviewCriterionDecision.__table__.c.points.type, Numeric)
    assert isinstance(ReviewRevision.__table__.c.total_score.type, Numeric)
    assert {"evidence_ids", "reason", "points", "criterion_id"} <= set(
        ReviewCriterionDecision.__table__.columns.keys()
    )
    check_sql = " ".join(
        str(item.sqltext)
        for item in ReviewCriterionDecision.__table__.constraints
        if isinstance(item, CheckConstraint)
    )
    assert "points >= 0" in check_sql
    assert ReviewIteration.__table__.c.current_revision_id.nullable
    assert Decimal("0.10") + Decimal("0.20") == Decimal("0.30")

    required_publication_tables = {
        "review_publication",
        "review_iteration_relation",
    }
    missing = required_publication_tables.difference(Base.metadata.tables)
    assert not missing, (
        "US5 publication/successor persistence is not implemented; missing tables: "
        f"{sorted(missing)}"
    )

    publication = Base.metadata.tables["review_publication"]
    relation = Base.metadata.tables["review_iteration_relation"]
    assert {
        "organization_id",
        "review_iteration_id",
        "review_revision_id",
        "published_by",
        "published_at",
        "status",
    } <= set(publication.columns.keys())
    assert {"organization_id", "predecessor_iteration_id", "successor_iteration_id", "kind"} <= set(
        relation.columns.keys()
    )
    assert (
        "organization_id",
        "predecessor_iteration_id",
        "successor_iteration_id",
    ) in _unique_columns("review_iteration_relation")
    assert "published_at" not in ReviewRevision.__table__.columns

    # Persisted publication is separate from editable current iteration. This
    # is the structural basis for archive-vs-publish CAS, terminal publication
    # non-regression, and keeping predecessor bytes current while a successor
    # remains unpublished.
    assert "current_iteration_id" in Base.metadata.tables["review_case"].columns
    assert "current_revision_id" in ReviewIteration.__table__.columns
