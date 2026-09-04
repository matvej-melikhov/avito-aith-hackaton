"""Structural and MySQL invariants for immutable homework publication history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint, Numeric, UniqueConstraint, update
from sqlalchemy.exc import IntegrityError
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    Course,
    CourseRun,
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
    Organization,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
)
from review_platform.infrastructure.db.session import (
    test_transaction as foundation_test_transaction,
)


def _unique_columns(model: type[object]) -> set[tuple[str, ...]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_keys(model: type[object]) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def test_homework_and_version_have_no_global_current_or_publication_fields() -> None:
    forbidden = {"current_version_id", "current_publication_id", "published_at"}
    assert forbidden.isdisjoint(Homework.__table__.columns.keys())
    assert forbidden.isdisjoint(HomeworkVersion.__table__.columns.keys())
    assert "updated_at" not in CourseRunHomeworkPublication.__table__.columns


def test_current_pointer_is_tenant_and_relation_local() -> None:
    assert (
        ("organization_id", "id", "current_publication_id"),
        (
            "course_run_homework_publication.organization_id",
            "course_run_homework_publication.course_run_homework_id",
            "course_run_homework_publication.id",
        ),
    ) in _foreign_keys(CourseRunHomework)
    assert (
        "organization_id",
        "course_run_homework_id",
        "id",
    ) in _unique_columns(CourseRunHomeworkPublication)


def test_publication_sequences_and_criterion_keys_are_relation_local_and_unique() -> None:
    assert (
        "organization_id",
        "course_run_homework_id",
        "publication_sequence",
    ) in _unique_columns(CourseRunHomeworkPublication)
    assert (
        "organization_id",
        "criterion_set_id",
        "stable_key",
    ) in _unique_columns(Criterion)
    assert (
        "organization_id",
        "criterion_set_id",
        "position",
    ) in _unique_columns(Criterion)


def test_scores_use_exact_fixed_point_storage() -> None:
    version_score = HomeworkVersion.__table__.c.max_score.type
    criterion_score = Criterion.__table__.c.max_points.type
    assert isinstance(version_score, Numeric)
    assert isinstance(criterion_score, Numeric)
    assert (version_score.precision, version_score.scale) == (12, 2)
    assert (criterion_score.precision, criterion_score.scale) == (12, 2)


def test_homework_migration_is_single_head_after_identity_courses() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(scripts.get_heads()) == 1
    revision = scripts.get_revision("0003_homework_versions")
    assert revision is not None
    assert revision.down_revision == "0002_identity_and_courses"


@pytest.mark.infrastructure
@pytest.mark.anyio
async def test_mysql_rejects_cross_tenant_and_cross_relation_publications(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    org_a = UUID("00000000-0000-7000-8000-000000000801")
    org_b = UUID("00000000-0000-7000-8000-000000000802")
    course_a = UUID("00000000-0000-7000-8000-000000000803")
    course_b = UUID("00000000-0000-7000-8000-000000000804")
    run_a1 = UUID("00000000-0000-7000-8000-000000000805")
    run_a2 = UUID("00000000-0000-7000-8000-000000000806")
    run_b = UUID("00000000-0000-7000-8000-000000000807")
    homework_a = UUID("00000000-0000-7000-8000-000000000808")
    homework_b = UUID("00000000-0000-7000-8000-000000000809")
    version_a1 = UUID("00000000-0000-7000-8000-000000000810")
    version_a2 = UUID("00000000-0000-7000-8000-000000000811")
    version_b = UUID("00000000-0000-7000-8000-000000000812")
    relation_a1 = UUID("00000000-0000-7000-8000-000000000813")
    relation_a2 = UUID("00000000-0000-7000-8000-000000000814")
    relation_b = UUID("00000000-0000-7000-8000-000000000815")
    publication_a1 = UUID("00000000-0000-7000-8000-000000000816")
    publication_a2 = UUID("00000000-0000-7000-8000-000000000817")
    publication_b = UUID("00000000-0000-7000-8000-000000000818")
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    try:
        async with foundation_test_transaction(engine) as session:
            session.add_all(
                [
                    Organization(id=org_a, slug="homework-org-a", name="Homework Org A"),
                    Organization(id=org_b, slug="homework-org-b", name="Homework Org B"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Course(
                        id=course_a,
                        organization_id=org_a,
                        title="Course A",
                        description="",
                        source_kind="standalone",
                    ),
                    Course(
                        id=course_b,
                        organization_id=org_b,
                        title="Course B",
                        description="",
                        source_kind="standalone",
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    CourseRun(
                        id=run_a1,
                        organization_id=org_a,
                        course_id=course_a,
                        title="Run A1",
                        timezone="UTC",
                    ),
                    CourseRun(
                        id=run_a2,
                        organization_id=org_a,
                        course_id=course_a,
                        title="Run A2",
                        timezone="UTC",
                    ),
                    CourseRun(
                        id=run_b,
                        organization_id=org_b,
                        course_id=course_b,
                        title="Run B",
                        timezone="UTC",
                    ),
                    Homework(
                        id=homework_a,
                        organization_id=org_a,
                        course_id=course_a,
                        title="Homework A",
                    ),
                    Homework(
                        id=homework_b,
                        organization_id=org_b,
                        course_id=course_b,
                        title="Homework B",
                    ),
                ]
            )
            await session.flush()

            def version(identity: UUID, org: UUID, homework: UUID, number: int) -> HomeworkVersion:
                return HomeworkVersion(
                    id=identity,
                    organization_id=org,
                    homework_id=homework,
                    version_number=number,
                    student_text=f"Version {number}",
                    max_score=Decimal("10.00"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                )

            session.add_all(
                [
                    version(version_a1, org_a, homework_a, 1),
                    version(version_a2, org_a, homework_a, 2),
                    version(version_b, org_b, homework_b, 1),
                ]
            )
            await session.flush()

            def relation(identity: UUID, org: UUID, run: UUID, homework: UUID) -> CourseRunHomework:
                return CourseRunHomework(
                    id=identity,
                    organization_id=org,
                    course_run_id=run,
                    homework_id=homework,
                    current_publication_id=None,
                    status="draft",
                )

            relations = [
                relation(relation_a1, org_a, run_a1, homework_a),
                relation(relation_a2, org_a, run_a2, homework_a),
                relation(relation_b, org_b, run_b, homework_b),
            ]
            session.add_all(relations)
            await session.flush()

            def publication(
                identity: UUID,
                org: UUID,
                relation_id: UUID,
                homework: UUID,
                version_id: UUID,
                sequence: int,
            ) -> CourseRunHomeworkPublication:
                return CourseRunHomeworkPublication(
                    id=identity,
                    organization_id=org,
                    course_run_homework_id=relation_id,
                    homework_id=homework,
                    homework_version_id=version_id,
                    publication_sequence=sequence,
                    submission_deadline=now + timedelta(days=1),
                    review_deadline=now + timedelta(days=2),
                    published_at=now,
                )

            publications = [
                publication(publication_a1, org_a, relation_a1, homework_a, version_a1, 1),
                publication(publication_a2, org_a, relation_a2, homework_a, version_a2, 1),
                publication(publication_b, org_b, relation_b, homework_b, version_b, 1),
            ]
            session.add_all(publications)
            await session.flush()
            relations[0].current_publication_id = publication_a1
            relations[1].current_publication_id = publication_a2
            relations[2].current_publication_id = publication_b
            relations[0].status = relations[1].status = relations[2].status = "active"
            await session.flush()
            assert relations[0].current_publication_id != relations[1].current_publication_id

            invalid_publications = (
                publication(
                    UUID("00000000-0000-7000-8000-000000000819"),
                    org_b,
                    relation_a1,
                    homework_b,
                    version_b,
                    2,
                ),
                publication(
                    UUID("00000000-0000-7000-8000-000000000820"),
                    org_a,
                    relation_a1,
                    homework_a,
                    version_a2,
                    1,
                ),
            )
            for row in invalid_publications:
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush([row])

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        update(CourseRunHomework)
                        .where(CourseRunHomework.id == relation_a1)
                        .values(current_publication_id=publication_a2)
                    )

            criterion_set_id = UUID("00000000-0000-7000-8000-000000000821")
            session.add(
                CriterionSet(
                    id=criterion_set_id,
                    organization_id=org_a,
                    homework_version_id=version_a1,
                )
            )
            await session.flush()
            session.add(
                Criterion(
                    id=UUID("00000000-0000-7000-8000-000000000822"),
                    organization_id=org_a,
                    criterion_set_id=criterion_set_id,
                    stable_key="correctness",
                    position=0,
                    title="Correctness",
                    description="",
                    max_points=Decimal("10.00"),
                    active=True,
                )
            )
            await session.flush()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    duplicate = Criterion(
                        id=UUID("00000000-0000-7000-8000-000000000823"),
                        organization_id=org_a,
                        criterion_set_id=criterion_set_id,
                        stable_key="correctness",
                        position=1,
                        title="Duplicate",
                        description="",
                        max_points=Decimal("1.00"),
                        active=True,
                    )
                    session.add(duplicate)
                    await session.flush([duplicate])
    finally:
        await engine.dispose()
