"""MySQL-backed tests for the exact T071 HomeworkRepository protocol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

from review_platform.application.services.homeworks import (
    HomeworkPublicationRecord,
    HomeworkRecord,
    HomeworkRepository,
    HomeworkVersionRecord,
)
from review_platform.domain.homework import CriterionRequirement, HomeworkRequirements
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.repositories.homeworks import (
    HomeworkPublicationHistoryRecord,
    SqlHomeworkRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
COURSE_A = UUID("00000000-0000-7000-8000-000000001001")
COURSE_OTHER = UUID("00000000-0000-7000-8000-000000001002")
COURSE_B = UUID("00000000-0000-7000-8000-000000001003")
RUN_A1 = UUID("00000000-0000-7000-8000-000000001011")
RUN_A2 = UUID("00000000-0000-7000-8000-000000001012")
RUN_OTHER = UUID("00000000-0000-7000-8000-000000001013")
RUN_B = UUID("00000000-0000-7000-8000-000000001014")
HOMEWORK_A = UUID("00000000-0000-7000-8000-000000001021")
HOMEWORK_B = UUID("00000000-0000-7000-8000-000000001022")
RELATION_A1 = UUID("00000000-0000-7000-8000-000000001031")
RELATION_A2 = UUID("00000000-0000-7000-8000-000000001032")
VERSION_1 = UUID("00000000-0000-7000-8000-000000001041")
VERSION_2 = UUID("00000000-0000-7000-8000-000000001042")
CRITERIA_1 = UUID("00000000-0000-7000-8000-000000001051")
CRITERIA_2 = UUID("00000000-0000-7000-8000-000000001052")
PUBLICATION_A1_V1 = UUID("00000000-0000-7000-8000-000000001061")
PUBLICATION_A2_V2 = UUID("00000000-0000-7000-8000-000000001062")
PUBLICATION_A1_V2 = UUID("00000000-0000-7000-8000-000000001063")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class _IDs:
    def __init__(self, start: int = 1100) -> None:
        self._next = start

    def __call__(self) -> UUID:
        self._next += 1
        return UUID(f"00000000-0000-7000-8000-{self._next:012d}")


async def _seed_learning(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                _course(ORG_A, COURSE_A, "Course A"),
                _course(ORG_A, COURSE_OTHER, "Other Course"),
                _course(ORG_B, COURSE_B, "Course B"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _run(ORG_A, COURSE_A, RUN_A1, "Run A1"),
                _run(ORG_A, COURSE_A, RUN_A2, "Run A2"),
                _run(ORG_A, COURSE_OTHER, RUN_OTHER, "Other Run"),
                _run(ORG_B, COURSE_B, RUN_B, "Run B"),
            ]
        )


def _course(organization_id: UUID, course_id: UUID, title: str) -> Course:
    return Course(
        id=course_id,
        organization_id=organization_id,
        title=title,
        description="",
        source_kind="standalone",
        status="active",
        revision=0,
    )


def _run(
    organization_id: UUID,
    course_id: UUID,
    run_id: UUID,
    title: str,
) -> CourseRun:
    return CourseRun(
        id=run_id,
        organization_id=organization_id,
        course_id=course_id,
        external_run_id=None,
        title=title,
        starts_at=None,
        ends_at=None,
        timezone="Europe/Moscow",
        status="active",
        revision=0,
    )


def _requirements(label: str, score: str) -> HomeworkRequirements:
    total = Decimal(score)
    first = (total / Decimal("2")).quantize(Decimal("0.01"))
    second = total - first
    return HomeworkRequirements(
        student_text=f"Requirements {label}",
        max_score=total,
        artifact_kinds=("github", "google_docs"),
        estimated_review_minutes=30,
        criteria=(
            CriterionRequirement(
                key="quality",
                title="Quality",
                description=f"Quality {label}",
                max_points=first,
            ),
            CriterionRequirement(
                key="correctness",
                title="Correctness",
                description=f"Correctness {label}",
                max_points=second,
            ),
        ),
    )


async def _seed_homework(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(
            Homework(
                id=HOMEWORK_A,
                organization_id=ORG_A,
                course_id=COURSE_A,
                title="Homework A",
                revision=0,
            )
        )


def _publication(
    *,
    publication_id: UUID,
    relation_id: UUID,
    run_id: UUID,
    version_id: UUID,
    sequence: int,
) -> HomeworkPublicationRecord:
    return HomeworkPublicationRecord(
        organization_id=ORG_A,
        publication_id=publication_id,
        course_run_homework_id=relation_id,
        course_run_id=run_id,
        homework_id=HOMEWORK_A,
        homework_version_id=version_id,
        publication_sequence=sequence,
        submission_deadline=NOW + timedelta(days=1),
        review_deadline=NOW + timedelta(days=2),
        published_at=NOW,
    )


def test_adapter_satisfies_exact_application_protocol() -> None:
    repository: HomeworkRepository = SqlHomeworkRepository()
    assert isinstance(repository, SqlHomeworkRepository)


async def test_tenant_locks_cas_and_caller_owned_rollback(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_learning(foundation_session_factory)
    repository = SqlHomeworkRepository(id_factory=_IDs())
    async with foundation_session_factory() as session:
        assert await repository.lock_course_run(
            ORG_A,
            RUN_A1,
            expected_revision=0,
            transaction=session,
        ) == COURSE_A
        assert await repository.lock_course_run(
            ORG_B,
            RUN_A1,
            expected_revision=0,
            transaction=session,
        ) is None
        await repository.add_homework(
            HomeworkRecord(ORG_A, HOMEWORK_A, COURSE_A, "Homework A", 0),
            relation=_relation_record(RELATION_A1, RUN_A1),
            transaction=session,
        )
        assert session.in_transaction()
        assert await repository.lock_homework(
            ORG_B,
            HOMEWORK_A,
            expected_revision=0,
            transaction=session,
        ) is None
        assert await repository.increment_homework_revision(
            ORG_A,
            HOMEWORK_A,
            expected_revision=0,
            transaction=session,
        )
        assert not await repository.increment_homework_revision(
            ORG_A,
            HOMEWORK_A,
            expected_revision=0,
            transaction=session,
        )
        await session.rollback()

    async with foundation_session_factory() as session:
        assert await session.get(Homework, HOMEWORK_A) is None
        assert await session.get(CourseRunHomework, RELATION_A1) is None


def _relation_record(relation_id: UUID, run_id: UUID):
    from review_platform.application.services.homeworks import CourseRunHomeworkRecord

    return CourseRunHomeworkRecord(
        organization_id=ORG_A,
        course_run_homework_id=relation_id,
        course_run_id=run_id,
        homework_id=HOMEWORK_A,
        current_publication_id=None,
        status="draft",
        revision=0,
    )


async def test_lock_or_create_serializes_unique_race_and_rejects_wrong_relation(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_learning(foundation_session_factory)
    await _seed_homework(foundation_session_factory)
    ready = anyio.Event()
    arrivals = 0
    results: list[UUID] = []

    async def create(relation_id: UUID) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await ready.wait()
        async with session_scope(foundation_session_factory) as session:
            relation = await SqlHomeworkRepository().lock_or_create_course_run_homework(
                ORG_A,
                RUN_A1,
                HOMEWORK_A,
                new_relation_id=relation_id,
                transaction=session,
            )
            assert relation is not None
            results.append(relation.course_run_homework_id)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(create, RELATION_A1)
        tasks.start_soon(create, RELATION_A2)

    assert len(set(results)) == 1
    async with foundation_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(CourseRunHomework))
        assert count == 1
        repository = SqlHomeworkRepository()
        assert await repository.lock_or_create_course_run_homework(
            ORG_A,
            RUN_OTHER,
            HOMEWORK_A,
            new_relation_id=UUID("00000000-0000-7000-8000-000000001039"),
            transaction=session,
        ) is None
        assert await repository.lock_or_create_course_run_homework(
            ORG_B,
            RUN_A1,
            HOMEWORK_A,
            new_relation_id=UUID("00000000-0000-7000-8000-000000001040"),
            transaction=session,
        ) is None
        await session.rollback()


async def test_two_runs_have_independent_current_and_full_deterministic_history(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_learning(foundation_session_factory)
    await _seed_homework(foundation_session_factory)
    ids = _IDs(1200)
    repository = SqlHomeworkRepository(id_factory=ids)
    requirements_1 = _requirements("v1", "10")
    requirements_2 = _requirements("v2", "12")

    async with session_scope(foundation_session_factory) as session:
        relation_a1 = await repository.lock_or_create_course_run_homework(
            ORG_A,
            RUN_A1,
            HOMEWORK_A,
            new_relation_id=RELATION_A1,
            transaction=session,
        )
        relation_a2 = await repository.lock_or_create_course_run_homework(
            ORG_A,
            RUN_A2,
            HOMEWORK_A,
            new_relation_id=RELATION_A2,
            transaction=session,
        )
        assert relation_a1 is not None and relation_a2 is not None

        assert await repository.next_version_number(
            ORG_A, HOMEWORK_A, transaction=session
        ) == 1
        await repository.append_version(
            HomeworkVersionRecord(
                ORG_A,
                VERSION_1,
                HOMEWORK_A,
                1,
                0,
                CRITERIA_1,
                requirements_1,
            ),
            transaction=session,
        )
        assert await repository.increment_homework_revision(
            ORG_A,
            HOMEWORK_A,
            expected_revision=0,
            transaction=session,
        )
        assert await repository.next_version_number(
            ORG_A, HOMEWORK_A, transaction=session
        ) == 2
        await repository.append_version(
            HomeworkVersionRecord(
                ORG_A,
                VERSION_2,
                HOMEWORK_A,
                2,
                0,
                CRITERIA_2,
                requirements_2,
            ),
            transaction=session,
        )
        assert await repository.increment_homework_revision(
            ORG_A,
            HOMEWORK_A,
            expected_revision=1,
            transaction=session,
        )

        publication_a1_v1 = _publication(
            publication_id=PUBLICATION_A1_V1,
            relation_id=RELATION_A1,
            run_id=RUN_A1,
            version_id=VERSION_1,
            sequence=1,
        )
        await repository.append_publication(publication_a1_v1, transaction=session)
        assert await repository.compare_and_set_current_publication(
            ORG_A,
            RELATION_A1,
            expected_revision=0,
            publication_id=PUBLICATION_A1_V1,
            transaction=session,
        )

        publication_a2_v2 = _publication(
            publication_id=PUBLICATION_A2_V2,
            relation_id=RELATION_A2,
            run_id=RUN_A2,
            version_id=VERSION_2,
            sequence=1,
        )
        await repository.append_publication(publication_a2_v2, transaction=session)
        assert await repository.compare_and_set_current_publication(
            ORG_A,
            RELATION_A2,
            expected_revision=0,
            publication_id=PUBLICATION_A2_V2,
            transaction=session,
        )

        publication_a1_v2 = _publication(
            publication_id=PUBLICATION_A1_V2,
            relation_id=RELATION_A1,
            run_id=RUN_A1,
            version_id=VERSION_2,
            sequence=2,
        )
        await repository.append_publication(publication_a1_v2, transaction=session)
        assert not await repository.compare_and_set_current_publication(
            ORG_A,
            RELATION_A1,
            expected_revision=0,
            publication_id=PUBLICATION_A1_V2,
            transaction=session,
        )
        assert await repository.compare_and_set_current_publication(
            ORG_A,
            RELATION_A1,
            expected_revision=1,
            publication_id=PUBLICATION_A1_V2,
            transaction=session,
        )

    async with foundation_session_factory() as session:
        history = await repository.history(ORG_A, HOMEWORK_A, transaction=session)
        assert history is not None
        assert history.homework.revision == 2
        assert [version.version_id for version in history.versions] == [VERSION_1, VERSION_2]
        assert [criterion.key for criterion in history.versions[0].requirements.criteria] == [
            "quality",
            "correctness",
        ]
        assert history.versions[0].requirements == requirements_1
        assert history.versions[1].requirements == requirements_2
        assert [relation.course_run_id for relation in history.course_run_homeworks] == [
            RUN_A1,
            RUN_A2,
        ]
        assert [publication.publication_id for publication in history.publications] == [
            PUBLICATION_A1_V1,
            PUBLICATION_A1_V2,
            PUBLICATION_A2_V2,
        ]
        publication_history = cast(
            tuple[HomeworkPublicationHistoryRecord, ...],
            history.publications,
        )
        assert [publication.is_current for publication in publication_history] == [
            False,
            True,
            True,
        ]
        current_by_run = {
            relation.course_run_id: relation.current_publication_id
            for relation in history.course_run_homeworks
        }
        assert current_by_run == {
            RUN_A1: PUBLICATION_A1_V2,
            RUN_A2: PUBLICATION_A2_V2,
        }
        assert await repository.get_publication(
            ORG_B,
            PUBLICATION_A1_V1,
            transaction=session,
        ) is None
        old = await session.get(CourseRunHomeworkPublication, PUBLICATION_A1_V1)
        assert old is not None
        assert (
            old.homework_version_id,
            old.publication_sequence,
            old.published_at,
        ) == (VERSION_1, 1, NOW)
        assert {"current_version_id", "current_publication_id"}.isdisjoint(
            HomeworkVersion.__table__.columns.keys()
        )
