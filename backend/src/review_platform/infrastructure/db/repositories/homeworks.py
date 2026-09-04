"""SQLAlchemy adapter for immutable, CourseRun-local homework history."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.services.homeworks import (
    CourseRunHomeworkRecord,
    HomeworkHistory,
    HomeworkPublicationRecord,
    HomeworkRecord,
    HomeworkRepository,
    HomeworkVersionRecord,
)
from review_platform.domain.homework import (
    ArtifactKind,
    CriterionRequirement,
    HomeworkRequirements,
)
from review_platform.domain.primitives import uuid7
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.learning import CourseRun


class HomeworkRepositoryError(RuntimeError):
    """The requested persistent relation violates tenant or immutable identity."""


class InvalidHomeworkTransaction(HomeworkRepositoryError):
    """The repository did not receive its caller-owned AsyncSession."""


class HomeworkRelationConflict(HomeworkRepositoryError):
    """Persistent homework identities disagree or a unique race is inconsistent."""


@dataclass(frozen=True, slots=True)
class HomeworkPublicationHistoryRecord(HomeworkPublicationRecord):
    """Publication plus its CourseRun-relation-local current marker."""

    is_current: bool


class SqlHomeworkRepository:
    """Implement the application repository port without opening or committing sessions."""

    def __init__(self, *, id_factory: Callable[[], UUID] = uuid7) -> None:
        self._id_factory = id_factory

    async def lock_course_run(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> UUID | None:
        session = _session(transaction)
        if not _revision(expected_revision):
            return None
        result = await session.execute(
            select(CourseRun.course_id)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
                CourseRun.revision == expected_revision,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def add_homework(
        self,
        homework: HomeworkRecord,
        relation: CourseRunHomeworkRecord,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        if (
            homework.organization_id != relation.organization_id
            or homework.homework_id != relation.homework_id
            or homework.revision != 0
            or relation.revision != 0
            or relation.current_publication_id is not None
            or relation.status != "draft"
        ):
            raise HomeworkRelationConflict("new Homework and CourseRun relation disagree")
        run_course_id = await session.scalar(
            select(CourseRun.course_id)
            .where(
                CourseRun.organization_id == homework.organization_id,
                CourseRun.id == relation.course_run_id,
            )
            .with_for_update()
        )
        if run_course_id is None or run_course_id != homework.course_id:
            raise HomeworkRelationConflict(
                "CourseRun does not belong to the Homework tenant/course"
            )
        session.add(
            Homework(
                id=homework.homework_id,
                organization_id=homework.organization_id,
                course_id=homework.course_id,
                title=homework.title,
                revision=homework.revision,
            )
        )
        # Explicit flush order is required because the relation participates in
        # the use_alter current-publication cycle and has no ORM relationship.
        await session.flush()
        session.add(
            CourseRunHomework(
                id=relation.course_run_homework_id,
                organization_id=relation.organization_id,
                course_run_id=relation.course_run_id,
                homework_id=relation.homework_id,
                current_publication_id=None,
                status=relation.status,
                revision=relation.revision,
            )
        )
        await session.flush()

    async def lock_homework(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> HomeworkRecord | None:
        session = _session(transaction)
        if not _revision(expected_revision):
            return None
        row = await session.scalar(
            select(Homework)
            .where(
                Homework.organization_id == organization_id,
                Homework.id == homework_id,
                Homework.revision == expected_revision,
            )
            .with_for_update()
        )
        return None if row is None else _homework_record(row)

    async def append_version(
        self,
        version: HomeworkVersionRecord,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        if version.revision != 0:
            raise HomeworkRelationConflict("new immutable HomeworkVersion must start at revision 0")
        homework_exists = await session.scalar(
            select(Homework.id).where(
                Homework.organization_id == version.organization_id,
                Homework.id == version.homework_id,
            )
        )
        if homework_exists is None:
            raise HomeworkRelationConflict("tenant-scoped Homework for version was not found")
        requirements = version.requirements
        session.add(
            HomeworkVersion(
                id=version.version_id,
                organization_id=version.organization_id,
                homework_id=version.homework_id,
                version_number=version.version_number,
                revision=version.revision,
                student_text=requirements.student_text,
                max_score=requirements.max_score,
                artifact_kinds=list(requirements.artifact_kinds),
                estimated_review_minutes=requirements.estimated_review_minutes,
            )
        )
        await session.flush()
        session.add(
            CriterionSet(
                id=version.criterion_set_id,
                organization_id=version.organization_id,
                homework_version_id=version.version_id,
            )
        )
        await session.flush()
        for criterion in requirements.criteria:
            if criterion.position is None:
                raise HomeworkRelationConflict("criterion position must be resolved")
            session.add(
                Criterion(
                    id=self._id_factory(),
                    organization_id=version.organization_id,
                    criterion_set_id=version.criterion_set_id,
                    stable_key=criterion.key,
                    position=criterion.position,
                    title=criterion.title,
                    description=criterion.description,
                    max_points=criterion.max_points,
                    active=True,
                )
            )
        await session.flush()

    async def next_version_number(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        latest = await session.scalar(
            select(HomeworkVersion.version_number)
            .where(
                HomeworkVersion.organization_id == organization_id,
                HomeworkVersion.homework_id == homework_id,
            )
            .order_by(HomeworkVersion.version_number.desc(), HomeworkVersion.id.desc())
            .limit(1)
            .with_for_update()
        )
        return 1 if latest is None else latest + 1

    async def increment_homework_revision(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        if not _revision(expected_revision):
            return False
        result = await session.execute(
            update(Homework)
            .where(
                Homework.organization_id == organization_id,
                Homework.id == homework_id,
                Homework.revision == expected_revision,
            )
            .values(
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def lock_version(
        self,
        organization_id: UUID,
        version_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> HomeworkVersionRecord | None:
        session = _session(transaction)
        if not _revision(expected_revision):
            return None
        row = await session.scalar(
            select(HomeworkVersion)
            .where(
                HomeworkVersion.organization_id == organization_id,
                HomeworkVersion.id == version_id,
                HomeworkVersion.revision == expected_revision,
            )
            .with_for_update()
        )
        if row is None:
            return None
        return await self._version_record(session, row)

    async def lock_or_create_course_run_homework(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        homework_id: UUID,
        *,
        new_relation_id: UUID,
        transaction: object,
    ) -> CourseRunHomeworkRecord | None:
        session = _session(transaction)
        run = await session.scalar(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
            )
            .with_for_update()
        )
        if run is None:
            return None
        homework = await session.scalar(
            select(Homework)
            .where(
                Homework.organization_id == organization_id,
                Homework.id == homework_id,
            )
            .with_for_update()
        )
        if homework is None or homework.course_id != run.course_id:
            return None
        existing = await self._relation_by_identity(
            session,
            organization_id,
            course_run_id,
            homework_id,
            for_update=True,
        )
        if existing is not None:
            return _relation_record(existing)

        proposed = CourseRunHomework(
            id=new_relation_id,
            organization_id=organization_id,
            course_run_id=course_run_id,
            homework_id=homework_id,
            current_publication_id=None,
            status="draft",
            revision=0,
        )
        try:
            async with session.begin_nested():
                session.add(proposed)
                await session.flush([proposed])
        except IntegrityError:
            winner = await self._relation_by_identity(
                session,
                organization_id,
                course_run_id,
                homework_id,
                for_update=True,
            )
            if winner is None:
                raise HomeworkRelationConflict(
                    "CourseRunHomework unique race has no tenant-consistent winner"
                ) from None
            return _relation_record(winner)
        return _relation_record(proposed)

    async def get_publication(
        self,
        organization_id: UUID,
        publication_id: UUID,
        *,
        transaction: object,
    ) -> HomeworkPublicationRecord | None:
        session = _session(transaction)
        result = await session.execute(
            select(
                CourseRunHomeworkPublication,
                CourseRunHomework.course_run_id,
                CourseRunHomework.current_publication_id,
            )
            .join(
                CourseRunHomework,
                (
                    CourseRunHomework.organization_id
                    == CourseRunHomeworkPublication.organization_id
                )
                & (
                    CourseRunHomework.id
                    == CourseRunHomeworkPublication.course_run_homework_id
                ),
            )
            .where(
                CourseRunHomeworkPublication.organization_id == organization_id,
                CourseRunHomeworkPublication.id == publication_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        publication, course_run_id, current_publication_id = row
        return _publication_record(
            publication,
            course_run_id=course_run_id,
            current_publication_id=current_publication_id,
        )

    async def next_publication_sequence(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        *,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        relation = await session.scalar(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == organization_id,
                CourseRunHomework.id == course_run_homework_id,
            )
            .with_for_update()
        )
        if relation is None:
            raise HomeworkRelationConflict("tenant-scoped CourseRunHomework was not found")
        latest = await session.scalar(
            select(CourseRunHomeworkPublication.publication_sequence)
            .where(
                CourseRunHomeworkPublication.organization_id == organization_id,
                CourseRunHomeworkPublication.course_run_homework_id
                == course_run_homework_id,
            )
            .order_by(
                CourseRunHomeworkPublication.publication_sequence.desc(),
                CourseRunHomeworkPublication.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
        return 1 if latest is None else latest + 1

    async def append_publication(
        self,
        publication: HomeworkPublicationRecord,
        *,
        transaction: object,
    ) -> None:
        session = _session(transaction)
        relation = await session.scalar(
            select(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == publication.organization_id,
                CourseRunHomework.id == publication.course_run_homework_id,
            )
            .with_for_update()
        )
        if (
            relation is None
            or relation.course_run_id != publication.course_run_id
            or relation.homework_id != publication.homework_id
        ):
            raise HomeworkRelationConflict("publication relation provenance mismatched")
        version_exists = await session.scalar(
            select(HomeworkVersion.id).where(
                HomeworkVersion.organization_id == publication.organization_id,
                HomeworkVersion.id == publication.homework_version_id,
                HomeworkVersion.homework_id == publication.homework_id,
            )
        )
        if version_exists is None:
            raise HomeworkRelationConflict("publication HomeworkVersion provenance mismatched")
        session.add(
            CourseRunHomeworkPublication(
                id=publication.publication_id,
                organization_id=publication.organization_id,
                course_run_homework_id=publication.course_run_homework_id,
                homework_id=publication.homework_id,
                homework_version_id=publication.homework_version_id,
                publication_sequence=publication.publication_sequence,
                submission_deadline=publication.submission_deadline,
                review_deadline=publication.review_deadline,
                published_at=publication.published_at,
            )
        )
        await session.flush()

    async def compare_and_set_current_publication(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        *,
        expected_revision: int,
        publication_id: UUID,
        transaction: object,
    ) -> bool:
        session = _session(transaction)
        if not _revision(expected_revision):
            return False
        publication_exists = await session.scalar(
            select(CourseRunHomeworkPublication.id).where(
                CourseRunHomeworkPublication.organization_id == organization_id,
                CourseRunHomeworkPublication.course_run_homework_id
                == course_run_homework_id,
                CourseRunHomeworkPublication.id == publication_id,
            )
        )
        if publication_exists is None:
            return False
        result = await session.execute(
            update(CourseRunHomework)
            .where(
                CourseRunHomework.organization_id == organization_id,
                CourseRunHomework.id == course_run_homework_id,
                CourseRunHomework.revision == expected_revision,
            )
            .values(
                current_publication_id=publication_id,
                status="active",
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def history(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        transaction: object,
    ) -> HomeworkHistory | None:
        session = _session(transaction)
        homework = await session.scalar(
            select(Homework).where(
                Homework.organization_id == organization_id,
                Homework.id == homework_id,
            )
        )
        if homework is None:
            return None
        version_rows = (
            await session.scalars(
                select(HomeworkVersion)
                .where(
                    HomeworkVersion.organization_id == organization_id,
                    HomeworkVersion.homework_id == homework_id,
                )
                .order_by(HomeworkVersion.version_number, HomeworkVersion.id)
            )
        ).all()
        versions = tuple(
            [await self._version_record(session, version) for version in version_rows]
        )
        relation_rows = (
            await session.scalars(
                select(CourseRunHomework)
                .where(
                    CourseRunHomework.organization_id == organization_id,
                    CourseRunHomework.homework_id == homework_id,
                )
                .order_by(CourseRunHomework.course_run_id, CourseRunHomework.id)
            )
        ).all()
        publication_rows = (
            await session.execute(
                select(CourseRunHomeworkPublication, CourseRunHomework.course_run_id)
                .join(
                    CourseRunHomework,
                    (
                        CourseRunHomework.organization_id
                        == CourseRunHomeworkPublication.organization_id
                    )
                    & (
                        CourseRunHomework.id
                        == CourseRunHomeworkPublication.course_run_homework_id
                    ),
                )
                .where(
                    CourseRunHomeworkPublication.organization_id == organization_id,
                    CourseRunHomeworkPublication.homework_id == homework_id,
                )
                .order_by(
                    CourseRunHomework.course_run_id,
                    CourseRunHomeworkPublication.publication_sequence,
                    CourseRunHomeworkPublication.id,
                )
            )
        ).all()
        current_by_relation = {
            relation.id: relation.current_publication_id for relation in relation_rows
        }
        return HomeworkHistory(
            homework=_homework_record(homework),
            versions=versions,
            course_run_homeworks=tuple(_relation_record(row) for row in relation_rows),
            publications=tuple(
                _publication_record(
                    publication,
                    course_run_id=course_run_id,
                    current_publication_id=current_by_relation.get(
                        publication.course_run_homework_id
                    ),
                )
                for publication, course_run_id in publication_rows
            ),
        )

    async def _version_record(
        self,
        session: AsyncSession,
        row: HomeworkVersion,
    ) -> HomeworkVersionRecord:
        criterion_set = await session.scalar(
            select(CriterionSet).where(
                CriterionSet.organization_id == row.organization_id,
                CriterionSet.homework_version_id == row.id,
            )
        )
        if criterion_set is None:
            raise HomeworkRelationConflict("HomeworkVersion has no tenant CriterionSet")
        criteria = (
            await session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == row.organization_id,
                    Criterion.criterion_set_id == criterion_set.id,
                )
                .order_by(Criterion.position, Criterion.id)
            )
        ).all()
        requirements = HomeworkRequirements(
            student_text=row.student_text,
            max_score=row.max_score,
            artifact_kinds=cast(list[ArtifactKind], row.artifact_kinds),
            estimated_review_minutes=row.estimated_review_minutes,
            criteria=tuple(
                CriterionRequirement(
                    key=criterion.stable_key,
                    title=criterion.title,
                    description=criterion.description,
                    max_points=criterion.max_points,
                    position=criterion.position,
                )
                for criterion in criteria
            ),
        )
        return HomeworkVersionRecord(
            organization_id=row.organization_id,
            version_id=row.id,
            homework_id=row.homework_id,
            version_number=row.version_number,
            revision=row.revision,
            criterion_set_id=criterion_set.id,
            requirements=requirements,
        )

    @staticmethod
    async def _relation_by_identity(
        session: AsyncSession,
        organization_id: UUID,
        course_run_id: UUID,
        homework_id: UUID,
        *,
        for_update: bool,
    ) -> CourseRunHomework | None:
        statement = select(CourseRunHomework).where(
            CourseRunHomework.organization_id == organization_id,
            CourseRunHomework.course_run_id == course_run_id,
            CourseRunHomework.homework_id == homework_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(CourseRunHomework | None, await session.scalar(statement))


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise InvalidHomeworkTransaction("homework repository requires caller-owned AsyncSession")
    return transaction


def _revision(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _homework_record(row: Homework) -> HomeworkRecord:
    return HomeworkRecord(
        organization_id=row.organization_id,
        homework_id=row.id,
        course_id=row.course_id,
        title=row.title,
        revision=row.revision,
    )


def _relation_record(row: CourseRunHomework) -> CourseRunHomeworkRecord:
    return CourseRunHomeworkRecord(
        organization_id=row.organization_id,
        course_run_homework_id=row.id,
        course_run_id=row.course_run_id,
        homework_id=row.homework_id,
        current_publication_id=row.current_publication_id,
        status=row.status,
        revision=row.revision,
    )


def _publication_record(
    row: CourseRunHomeworkPublication,
    *,
    course_run_id: UUID,
    current_publication_id: UUID | None,
) -> HomeworkPublicationHistoryRecord:
    return HomeworkPublicationHistoryRecord(
        organization_id=row.organization_id,
        publication_id=row.id,
        course_run_homework_id=row.course_run_homework_id,
        course_run_id=course_run_id,
        homework_id=row.homework_id,
        homework_version_id=row.homework_version_id,
        publication_sequence=row.publication_sequence,
        submission_deadline=row.submission_deadline,
        review_deadline=row.review_deadline,
        published_at=row.published_at,
        is_current=row.id == current_publication_id,
    )


_protocol_check: HomeworkRepository = SqlHomeworkRepository()


__all__ = [
    "HomeworkPublicationHistoryRecord",
    "HomeworkRelationConflict",
    "HomeworkRepositoryError",
    "InvalidHomeworkTransaction",
    "SqlHomeworkRepository",
]
