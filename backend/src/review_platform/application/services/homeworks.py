"""Immutable homework versioning and CourseRun-local publication orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.application.services.courses import GuardedCourseAction
from review_platform.domain.homework import HomeworkRequirements
from review_platform.domain.primitives import require_utc, utc_now, uuid7

_METHODOLOGIST = AuthorizationPolicy(required_roles=frozenset({"methodologist"}))
CONTRACT_VERSION = "1.1.0"


class HomeworkServiceError(RuntimeError):
    """Base typed homework orchestration error."""


class HomeworkNotFound(HomeworkServiceError):
    pass


class HomeworkVersionNotFound(HomeworkServiceError):
    pass


class HomeworkRevisionConflict(HomeworkServiceError):
    pass


class HomeworkPublicationConflict(HomeworkServiceError):
    pass


@dataclass(frozen=True, slots=True)
class HomeworkRecord:
    organization_id: UUID
    homework_id: UUID
    course_id: UUID
    title: str
    revision: int


@dataclass(frozen=True, slots=True)
class HomeworkVersionRecord:
    organization_id: UUID
    version_id: UUID
    homework_id: UUID
    version_number: int
    revision: int
    criterion_set_id: UUID
    requirements: HomeworkRequirements


@dataclass(frozen=True, slots=True)
class CourseRunHomeworkRecord:
    organization_id: UUID
    course_run_homework_id: UUID
    course_run_id: UUID
    homework_id: UUID
    current_publication_id: UUID | None
    status: str
    revision: int


@dataclass(frozen=True, slots=True)
class HomeworkPublicationRecord:
    organization_id: UUID
    publication_id: UUID
    course_run_homework_id: UUID
    course_run_id: UUID
    homework_id: UUID
    homework_version_id: UUID
    publication_sequence: int
    submission_deadline: datetime
    review_deadline: datetime
    published_at: datetime


@dataclass(frozen=True, slots=True)
class HomeworkRequirementsChanged:
    organization_id: UUID
    course_run_id: UUID
    course_run_homework_id: UUID
    homework_id: UUID
    previous_homework_version_id: UUID | None
    current_homework_version_id: UUID
    previous_publication_id: UUID | None
    current_publication_id: UUID
    publication_sequence: int
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class HomeworkHistory:
    homework: HomeworkRecord
    versions: tuple[HomeworkVersionRecord, ...]
    course_run_homeworks: tuple[CourseRunHomeworkRecord, ...]
    publications: tuple[HomeworkPublicationRecord, ...]


class HomeworkRepository(Protocol):
    """Tenant-scoped persistence port implemented by T072."""

    async def lock_course_run(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> UUID | None: ...

    async def add_homework(
        self, homework: HomeworkRecord, relation: CourseRunHomeworkRecord, *, transaction: object
    ) -> None: ...

    async def lock_homework(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> HomeworkRecord | None: ...

    async def append_version(
        self, version: HomeworkVersionRecord, *, transaction: object
    ) -> None: ...

    async def next_version_number(
        self, organization_id: UUID, homework_id: UUID, *, transaction: object
    ) -> int: ...

    async def increment_homework_revision(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool: ...

    async def lock_version(
        self,
        organization_id: UUID,
        version_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> HomeworkVersionRecord | None: ...

    async def lock_or_create_course_run_homework(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        homework_id: UUID,
        *,
        new_relation_id: UUID,
        transaction: object,
    ) -> CourseRunHomeworkRecord | None: ...

    async def get_publication(
        self, organization_id: UUID, publication_id: UUID, *, transaction: object
    ) -> HomeworkPublicationRecord | None: ...

    async def next_publication_sequence(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        *,
        transaction: object,
    ) -> int: ...

    async def append_publication(
        self, publication: HomeworkPublicationRecord, *, transaction: object
    ) -> None: ...

    async def compare_and_set_current_publication(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        *,
        expected_revision: int,
        publication_id: UUID,
        transaction: object,
    ) -> bool: ...

    async def history(
        self, organization_id: UUID, homework_id: UUID, *, transaction: object
    ) -> HomeworkHistory | None: ...


class ArchivedCourseGuard(Protocol):
    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        action: GuardedCourseAction,
    ) -> tuple[object, object]: ...


class HomeworkRequirementsOutbox(Protocol):
    async def append(self, event: HomeworkRequirementsChanged, *, transaction: object) -> None: ...


class HomeworkService:
    def __init__(
        self,
        *,
        repository: HomeworkRepository,
        archived_guard: ArchivedCourseGuard,
        outbox: HomeworkRequirementsOutbox,
        authorizer: Authorizer,
        audit: AuditRecorder,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._archived_guard = archived_guard
        self._outbox = outbox
        self._authorizer = authorizer
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def create_homework(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        course_run_id: UUID,
        expected_course_run_revision: int,
        title: str,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> tuple[HomeworkRecord, CourseRunHomeworkRecord]:
        grant = await self._authorizer.authorize(
            actor=actor, organization_id=organization_id, policy=_METHODOLOGIST
        )
        if not 1 <= len(title) <= 512:
            raise HomeworkServiceError("homework title length must be between 1 and 512")
        await self._archived_guard.require_active(
            organization_id=organization_id,
            course_run_id=course_run_id,
            action="publication",
        )
        course_id = await self._repository.lock_course_run(
            organization_id,
            course_run_id,
            expected_revision=expected_course_run_revision,
            transaction=transaction,
        )
        if course_id is None:
            raise HomeworkRevisionConflict("CourseRun is missing or revision is stale")
        homework = HomeworkRecord(organization_id, self._id_factory(), course_id, title, 0)
        relation = CourseRunHomeworkRecord(
            organization_id,
            self._id_factory(),
            course_run_id,
            homework.homework_id,
            None,
            "draft",
            0,
        )
        await self._repository.add_homework(homework, relation, transaction=transaction)
        await self._audit_event(
            transaction,
            actor,
            "create_homework",
            homework.homework_id,
            None,
            0,
            request_id,
            trace_id,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return homework, relation

    async def create_version(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        homework_id: UUID,
        expected_homework_revision: int,
        requirements: HomeworkRequirements,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> HomeworkVersionRecord:
        grant = await self._authorizer.authorize(
            actor=actor, organization_id=organization_id, policy=_METHODOLOGIST
        )
        homework = await self._repository.lock_homework(
            organization_id,
            homework_id,
            expected_revision=expected_homework_revision,
            transaction=transaction,
        )
        if homework is None:
            raise HomeworkRevisionConflict("Homework is missing or revision is stale")
        version = HomeworkVersionRecord(
            organization_id=organization_id,
            version_id=self._id_factory(),
            homework_id=homework_id,
            version_number=await self._repository.next_version_number(
                organization_id, homework_id, transaction=transaction
            ),
            revision=0,
            criterion_set_id=self._id_factory(),
            requirements=requirements,
        )
        await self._repository.append_version(version, transaction=transaction)
        if not await self._repository.increment_homework_revision(
            organization_id,
            homework_id,
            expected_revision=expected_homework_revision,
            transaction=transaction,
        ):
            raise HomeworkRevisionConflict("Homework version CAS lost a race")
        await self._audit_event(
            transaction,
            actor,
            "create_homework_version",
            version.version_id,
            None,
            0,
            request_id,
            trace_id,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return version

    async def publish_version(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        course_run_id: UUID,
        homework_version_id: UUID,
        expected_version_revision: int,
        submission_deadline: datetime,
        review_deadline: datetime,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> HomeworkPublicationRecord:
        grant = await self._authorizer.authorize(
            actor=actor, organization_id=organization_id, policy=_METHODOLOGIST
        )
        submission = require_utc(submission_deadline)
        review = require_utc(review_deadline)
        if submission > review:
            raise HomeworkPublicationConflict("submission deadline exceeds review deadline")
        version = await self._repository.lock_version(
            organization_id,
            homework_version_id,
            expected_revision=expected_version_revision,
            transaction=transaction,
        )
        if version is None:
            raise HomeworkVersionNotFound("version is missing or revision is stale")
        await self._archived_guard.require_active(
            organization_id=organization_id,
            course_run_id=course_run_id,
            action="publication",
        )
        relation = await self._repository.lock_or_create_course_run_homework(
            organization_id,
            course_run_id,
            version.homework_id,
            new_relation_id=self._id_factory(),
            transaction=transaction,
        )
        if relation is None:
            raise HomeworkNotFound("Homework is not scoped to this CourseRun")
        previous = None
        if relation.current_publication_id is not None:
            previous = await self._repository.get_publication(
                organization_id, relation.current_publication_id, transaction=transaction
            )
            if previous is None:
                raise HomeworkPublicationConflict("current publication pointer is broken")
        publication = HomeworkPublicationRecord(
            organization_id=organization_id,
            publication_id=self._id_factory(),
            course_run_homework_id=relation.course_run_homework_id,
            course_run_id=course_run_id,
            homework_id=version.homework_id,
            homework_version_id=version.version_id,
            publication_sequence=await self._repository.next_publication_sequence(
                organization_id, relation.course_run_homework_id, transaction=transaction
            ),
            submission_deadline=submission,
            review_deadline=review,
            published_at=require_utc(self._clock()),
        )
        await self._repository.append_publication(publication, transaction=transaction)
        if not await self._repository.compare_and_set_current_publication(
            organization_id,
            relation.course_run_homework_id,
            expected_revision=relation.revision,
            publication_id=publication.publication_id,
            transaction=transaction,
        ):
            raise HomeworkRevisionConflict("CourseRunHomework current pointer CAS lost a race")
        await self._outbox.append(
            HomeworkRequirementsChanged(
                organization_id=organization_id,
                course_run_id=course_run_id,
                course_run_homework_id=relation.course_run_homework_id,
                homework_id=version.homework_id,
                previous_homework_version_id=(
                    previous.homework_version_id if previous is not None else None
                ),
                current_homework_version_id=version.version_id,
                previous_publication_id=(previous.publication_id if previous is not None else None),
                current_publication_id=publication.publication_id,
                publication_sequence=publication.publication_sequence,
            ),
            transaction=transaction,
        )
        await self._audit_event(
            transaction,
            actor,
            "publish_homework_version",
            publication.publication_id,
            relation.revision,
            relation.revision + 1,
            request_id,
            trace_id,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return publication

    async def history(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        homework_id: UUID,
        actor: RequestActor,
    ) -> HomeworkHistory:
        await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=AuthorizationPolicy(
                required_roles=frozenset({"methodologist", "reviewer", "student"})
            ),
        )
        history = await self._repository.history(
            organization_id, homework_id, transaction=transaction
        )
        if history is None:
            raise HomeworkNotFound("tenant-scoped Homework was not found")
        return history

    async def _audit_event(
        self,
        transaction: object,
        actor: RequestActor,
        action: str,
        entity_id: UUID,
        before_revision: int | None,
        after_revision: int | None,
        request_id: UUID,
        trace_id: UUID,
    ) -> None:
        await self._audit.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action=action,
                entity_type="homework",
                entity_id=entity_id,
                before_revision=before_revision,
                after_revision=after_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={},
            ),
            transaction=transaction,
        )


__all__ = [
    "ArchivedCourseGuard",
    "CourseRunHomeworkRecord",
    "HomeworkHistory",
    "HomeworkNotFound",
    "HomeworkPublicationConflict",
    "HomeworkPublicationRecord",
    "HomeworkRecord",
    "HomeworkRepository",
    "HomeworkRequirementsChanged",
    "HomeworkRequirementsOutbox",
    "HomeworkRevisionConflict",
    "HomeworkService",
    "HomeworkServiceError",
    "HomeworkVersionNotFound",
    "HomeworkVersionRecord",
]
