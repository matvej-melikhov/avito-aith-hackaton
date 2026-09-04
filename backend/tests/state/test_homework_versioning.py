"""RED state specification for immutable, CourseRun-local homework versions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Table, select
from sqlalchemy.exc import IntegrityError

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import Course, CourseRun, OutboxMessage
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
COURSE = UUID("00000000-0000-7000-8000-000000000801")
RUN_A = UUID("00000000-0000-7000-8000-000000000802")
RUN_B = UUID("00000000-0000-7000-8000-000000000803")
HOMEWORK = UUID("00000000-0000-7000-8000-000000000804")
VERSION_1 = UUID("00000000-0000-7000-8000-000000000805")
VERSION_2 = UUID("00000000-0000-7000-8000-000000000806")
CRH_A = UUID("00000000-0000-7000-8000-000000000807")
CRH_B = UUID("00000000-0000-7000-8000-000000000808")
PUBLICATION_A1 = UUID("00000000-0000-7000-8000-000000000809")
PUBLICATION_A2 = UUID("00000000-0000-7000-8000-000000000810")
PUBLICATION_B1 = UUID("00000000-0000-7000-8000-000000000811")
CRITERION_SET_1 = UUID("00000000-0000-7000-8000-000000000812")
CRITERION_SET_2 = UUID("00000000-0000-7000-8000-000000000813")
NOW = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)

TABLE_NAMES = (
    "homework",
    "homework_version",
    "course_run_homework",
    "course_run_homework_publication",
    "criterion_set",
    "criterion",
)


def _tables() -> dict[str, Table]:
    tables: dict[str, Table] = {}
    for name in TABLE_NAMES:
        table = Base.metadata.tables.get(name)
        assert table is not None, f"T068 homework state table is missing: {name}"
        tables[name] = table
    return tables


async def _seed_learning(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(
            Course(
                id=COURSE,
                organization_id=ORG,
                title="Versioned Homework Course",
                description="",
                source_kind="standalone",
                status="active",
                revision=0,
            )
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=RUN_A,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id="run-a",
                    title="Run A",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=RUN_B,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id="run-b",
                    title="Run B",
                    timezone="Europe/Moscow",
                    status="active",
                    revision=0,
                ),
            ]
        )


def _homework_rows(tables: dict[str, Table]) -> list[tuple[Table, dict[str, Any]]]:
    return [
        (
            tables["homework"],
            {
                "id": HOMEWORK,
                "organization_id": ORG,
                "course_id": COURSE,
                "title": "Evidence-based review",
                "revision": 0,
            },
        ),
        (
            tables["homework_version"],
            {
                "id": VERSION_1,
                "organization_id": ORG,
                "homework_id": HOMEWORK,
                "version_number": 1,
                "student_text": "Submit a repository and explain the design.",
                "max_score": Decimal("10.0"),
                "artifact_kinds": ["github", "google_docs"],
                "estimated_review_minutes": 25,
                "revision": 0,
            },
        ),
        (
            tables["criterion_set"],
            {
                "id": CRITERION_SET_1,
                "organization_id": ORG,
                "homework_version_id": VERSION_1,
            },
        ),
    ]


async def test_full_version_round_trip_total_and_stable_unique_criterion_keys(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    tables = _tables()
    await _seed_learning(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        for table, values in _homework_rows(tables):
            await session.execute(table.insert().values(**values))
        criteria = [
            {
                "id": UUID("00000000-0000-7000-8000-000000000814"),
                "organization_id": ORG,
                "criterion_set_id": CRITERION_SET_1,
                "stable_key": "correctness",
                "position": 1,
                "title": "Correctness",
                "description": "The solution satisfies the requirements.",
                "max_points": Decimal("6.0"),
                "active": True,
            },
            {
                "id": UUID("00000000-0000-7000-8000-000000000815"),
                "organization_id": ORG,
                "criterion_set_id": CRITERION_SET_1,
                "stable_key": "explanation",
                "position": 2,
                "title": "Explanation",
                "description": "The design is explained clearly.",
                "max_points": Decimal("4.0"),
                "active": True,
            },
        ]
        await session.execute(tables["criterion"].insert(), criteria)

        version = (
            await session.execute(
                select(tables["homework_version"]).where(
                    tables["homework_version"].c.id == VERSION_1
                )
            )
        ).mappings().one()
        stored_criteria = (
            await session.execute(
                select(tables["criterion"])
                .where(tables["criterion"].c.criterion_set_id == CRITERION_SET_1)
                .order_by(tables["criterion"].c.position)
            )
        ).mappings().all()

        assert version["student_text"] == "Submit a repository and explain the design."
        assert Decimal(str(version["max_score"])) == Decimal("10.0")
        assert version["artifact_kinds"] == ["github", "google_docs"]
        assert version["estimated_review_minutes"] == 25
        assert sum(Decimal(str(row["max_points"])) for row in stored_criteria) == Decimal(
            str(version["max_score"])
        )
        assert [row["stable_key"] for row in stored_criteria] == [
            "correctness",
            "explanation",
        ]

        duplicate = dict(criteria[1])
        duplicate["id"] = UUID("00000000-0000-7000-8000-000000000816")
        duplicate["stable_key"] = "correctness"
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(tables["criterion"].insert().values(**duplicate))


async def test_versions_are_append_only_and_homework_has_no_global_current_pointer(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    tables = _tables()
    assert "current_version_id" not in tables["homework"].c
    assert "published_at" not in tables["homework_version"].c
    await _seed_learning(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        for table, values in _homework_rows(tables):
            await session.execute(table.insert().values(**values))
        before = (
            await session.execute(
                select(tables["homework_version"]).where(
                    tables["homework_version"].c.id == VERSION_1
                )
            )
        ).mappings().one()
        await session.execute(
            tables["homework_version"].insert().values(
                id=VERSION_2,
                organization_id=ORG,
                homework_id=HOMEWORK,
                version_number=2,
                student_text="Submit a repository, tests, and an architecture note.",
                max_score=Decimal("12.0"),
                artifact_kinds=["github"],
                estimated_review_minutes=30,
                revision=0,
            )
        )
        after = (
            await session.execute(
                select(tables["homework_version"]).where(
                    tables["homework_version"].c.id == VERSION_1
                )
            )
        ).mappings().one()
        versions = (
            await session.execute(
                select(tables["homework_version"].c.version_number)
                .where(tables["homework_version"].c.homework_id == HOMEWORK)
                .order_by(tables["homework_version"].c.version_number)
            )
        ).scalars().all()

    assert dict(after) == dict(before)
    assert versions == [1, 2]


async def test_course_run_current_publication_is_local_and_change_event_is_durable(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    tables = _tables()
    await _seed_learning(foundation_session_factory)
    publication = tables["course_run_homework_publication"]
    course_run_homework = tables["course_run_homework"]
    async with session_scope(foundation_session_factory) as session:
        for table, values in _homework_rows(tables):
            await session.execute(table.insert().values(**values))
        await session.execute(
            tables["homework_version"].insert().values(
                id=VERSION_2,
                organization_id=ORG,
                homework_id=HOMEWORK,
                version_number=2,
                student_text="Version two",
                max_score=Decimal("10.0"),
                artifact_kinds=["github"],
                estimated_review_minutes=30,
                revision=0,
            )
        )
        await session.execute(
            course_run_homework.insert(),
            [
                {
                    "id": CRH_A,
                    "organization_id": ORG,
                    "course_run_id": RUN_A,
                    "homework_id": HOMEWORK,
                    "current_publication_id": None,
                    "status": "active",
                    "revision": 0,
                },
                {
                    "id": CRH_B,
                    "organization_id": ORG,
                    "course_run_id": RUN_B,
                    "homework_id": HOMEWORK,
                    "current_publication_id": None,
                    "status": "active",
                    "revision": 0,
                },
            ],
        )
        common = {
            "organization_id": ORG,
            "submission_deadline": NOW + timedelta(days=7),
            "review_deadline": NOW + timedelta(days=10),
            "published_at": NOW,
        }
        await session.execute(
            publication.insert(),
            [
                {
                    **common,
                    "id": PUBLICATION_A1,
                    "course_run_homework_id": CRH_A,
                    "homework_id": HOMEWORK,
                    "homework_version_id": VERSION_1,
                    "publication_sequence": 1,
                },
                {
                    **common,
                    "id": PUBLICATION_B1,
                    "course_run_homework_id": CRH_B,
                    "homework_id": HOMEWORK,
                    "homework_version_id": VERSION_2,
                    "publication_sequence": 1,
                },
            ],
        )
        await session.execute(
            course_run_homework.update()
            .where(course_run_homework.c.id == CRH_A)
            .values(current_publication_id=PUBLICATION_A1)
        )
        await session.execute(
            course_run_homework.update()
            .where(course_run_homework.c.id == CRH_B)
            .values(current_publication_id=PUBLICATION_B1)
        )
        prior = (
            await session.execute(select(publication).where(publication.c.id == PUBLICATION_A1))
        ).mappings().one()
        await session.execute(
            publication.insert().values(
                **common,
                id=PUBLICATION_A2,
                course_run_homework_id=CRH_A,
                homework_id=HOMEWORK,
                homework_version_id=VERSION_2,
                publication_sequence=2,
            )
        )
        await session.execute(
            course_run_homework.update()
            .where(course_run_homework.c.id == CRH_A)
            .values(current_publication_id=PUBLICATION_A2, revision=1)
        )
        session.add(
            OutboxMessage(
                organization_id=ORG,
                message_id=UUID("00000000-0000-7000-8000-000000000817"),
                aggregate_type="course_run_homework",
                aggregate_id=CRH_A,
                event_type="HomeworkRequirementsChanged",
                payload_version="1.1.0",
                payload={
                    "course_run_homework_id": str(CRH_A),
                    "previous_homework_version_id": str(VERSION_1),
                    "current_homework_version_id": str(VERSION_2),
                },
                available_at=NOW,
                enqueue_state="pending",
                attempts=0,
                max_attempts=5,
            )
        )
        after = (
            await session.execute(select(publication).where(publication.c.id == PUBLICATION_A1))
        ).mappings().one()
        current = (
            await session.execute(
                select(
                    course_run_homework.c.course_run_id,
                    course_run_homework.c.current_publication_id,
                ).order_by(course_run_homework.c.course_run_id)
            )
        ).all()

    assert dict(after) == dict(prior)
    assert current == [(RUN_A, PUBLICATION_A2), (RUN_B, PUBLICATION_B1)]
    async with foundation_session_factory() as session:
        event = (
            await session.execute(
                select(OutboxMessage).where(
                    OutboxMessage.event_type == "HomeworkRequirementsChanged"
                )
            )
        ).scalar_one()
    assert event.payload["previous_homework_version_id"] == str(VERSION_1)
    assert event.payload["current_homework_version_id"] == str(VERSION_2)
