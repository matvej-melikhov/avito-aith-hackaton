from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import (
    AuthVersionSnapshot,
    RequestActor,
)
from review_platform.application.services.homeworks import (
    CourseRunHomeworkRecord,
    HomeworkHistory,
    HomeworkPublicationRecord,
    HomeworkRecord,
    HomeworkRequirementsChanged,
    HomeworkRevisionConflict,
    HomeworkService,
    HomeworkVersionRecord,
)
from review_platform.domain.homework import CriterionRequirement, HomeworkRequirements

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000000901")
COURSE = UUID("00000000-0000-7000-8000-000000000902")
RUN_A = UUID("00000000-0000-7000-8000-000000000903")
RUN_B = UUID("00000000-0000-7000-8000-000000000904")
REQUEST = UUID("00000000-0000-7000-8000-000000000905")
TRACE = UUID("00000000-0000-7000-8000-000000000906")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class Guard:
    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        return self._snapshot(actor)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        return self._snapshot(actor)

    @staticmethod
    def _snapshot(actor: RequestActor) -> AuthVersionSnapshot:
        assert actor.user_id is not None
        assert actor.membership_revision is not None
        assert actor.auth_epoch is not None
        return AuthVersionSnapshot(
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            roles=actor.roles,
            membership_revision=actor.membership_revision,
            auth_epoch=actor.auth_epoch,
            active=True,
        )


class Audits:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, event: object, *, transaction: object) -> None:
        self.events.append(event)


class ArchivedGuard:
    async def require_active(self, **_: object) -> tuple[object, object]:
        return object(), object()


class Outbox:
    def __init__(self) -> None:
        self.events: list[HomeworkRequirementsChanged] = []

    async def append(self, event: HomeworkRequirementsChanged, *, transaction: object) -> None:
        self.events.append(event)


class Repository:
    def __init__(self) -> None:
        self.run_revisions = {RUN_A: 0, RUN_B: 0}
        self.homeworks: dict[UUID, HomeworkRecord] = {}
        self.versions: dict[UUID, HomeworkVersionRecord] = {}
        self.relations: dict[tuple[UUID, UUID], CourseRunHomeworkRecord] = {}
        self.publications: dict[UUID, HomeworkPublicationRecord] = {}
        self.fail_next_publication_cas = False

    async def lock_course_run(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> UUID | None:
        if organization_id != ORG or self.run_revisions.get(course_run_id) != expected_revision:
            return None
        return COURSE

    async def add_homework(
        self, homework: HomeworkRecord, relation: CourseRunHomeworkRecord, *, transaction: object
    ) -> None:
        self.homeworks[homework.homework_id] = homework
        self.relations[(relation.course_run_id, relation.homework_id)] = relation

    async def lock_homework(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> HomeworkRecord | None:
        row = self.homeworks.get(homework_id)
        return (
            row
            if row and row.organization_id == organization_id and row.revision == expected_revision
            else None
        )

    async def append_version(self, version: HomeworkVersionRecord, *, transaction: object) -> None:
        self.versions[version.version_id] = version

    async def next_version_number(
        self, organization_id: UUID, homework_id: UUID, *, transaction: object
    ) -> int:
        return 1 + sum(v.homework_id == homework_id for v in self.versions.values())

    async def increment_homework_revision(
        self,
        organization_id: UUID,
        homework_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool:
        row = self.homeworks.get(homework_id)
        if (
            row is None
            or row.organization_id != organization_id
            or row.revision != expected_revision
        ):
            return False
        self.homeworks[homework_id] = replace(row, revision=row.revision + 1)
        return True

    async def lock_version(
        self,
        organization_id: UUID,
        version_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> HomeworkVersionRecord | None:
        row = self.versions.get(version_id)
        return (
            row
            if row and row.organization_id == organization_id and row.revision == expected_revision
            else None
        )

    async def lock_or_create_course_run_homework(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        homework_id: UUID,
        *,
        new_relation_id: UUID,
        transaction: object,
    ) -> CourseRunHomeworkRecord | None:
        row = self.relations.get((course_run_id, homework_id))
        if row is not None:
            return row if row.organization_id == organization_id else None
        if course_run_id not in self.run_revisions or homework_id not in self.homeworks:
            return None
        row = CourseRunHomeworkRecord(
            organization_id,
            new_relation_id,
            course_run_id,
            homework_id,
            None,
            "draft",
            0,
        )
        self.relations[(course_run_id, homework_id)] = row
        return row

    async def get_publication(
        self, organization_id: UUID, publication_id: UUID, *, transaction: object
    ) -> HomeworkPublicationRecord | None:
        row = self.publications.get(publication_id)
        return row if row and row.organization_id == organization_id else None

    async def next_publication_sequence(
        self, organization_id: UUID, course_run_homework_id: UUID, *, transaction: object
    ) -> int:
        return 1 + sum(
            row.course_run_homework_id == course_run_homework_id
            for row in self.publications.values()
        )

    async def append_publication(
        self, publication: HomeworkPublicationRecord, *, transaction: object
    ) -> None:
        self.publications[publication.publication_id] = publication

    async def compare_and_set_current_publication(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        *,
        expected_revision: int,
        publication_id: UUID,
        transaction: object,
    ) -> bool:
        if self.fail_next_publication_cas:
            self.fail_next_publication_cas = False
            return False
        key = next(
            (
                key
                for key, row in self.relations.items()
                if row.course_run_homework_id == course_run_homework_id
            ),
            None,
        )
        if key is None:
            return False
        row = self.relations[key]
        if row.organization_id != organization_id or row.revision != expected_revision:
            return False
        self.relations[key] = replace(
            row,
            current_publication_id=publication_id,
            status="active",
            revision=row.revision + 1,
        )
        return True

    async def history(
        self, organization_id: UUID, homework_id: UUID, *, transaction: object
    ) -> HomeworkHistory | None:
        homework = self.homeworks.get(homework_id)
        if homework is None or homework.organization_id != organization_id:
            return None
        return HomeworkHistory(
            homework=homework,
            versions=tuple(
                sorted(
                    (row for row in self.versions.values() if row.homework_id == homework_id),
                    key=lambda row: row.version_number,
                )
            ),
            course_run_homeworks=tuple(
                row for row in self.relations.values() if row.homework_id == homework_id
            ),
            publications=tuple(
                sorted(
                    (row for row in self.publications.values() if row.homework_id == homework_id),
                    key=lambda row: (str(row.course_run_id), row.publication_sequence),
                )
            ),
        )


def _actor() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist"},
        membership_revision=0,
        auth_epoch=0,
    )


def _requirements(text: str, score: str = "10") -> HomeworkRequirements:
    return HomeworkRequirements(
        student_text=text,
        max_score=Decimal(score),
        artifact_kinds=("github",),
        estimated_review_minutes=20,
        criteria=(
            CriterionRequirement(
                key="correctness",
                title="Correctness",
                description="Works",
                max_points=Decimal(score),
            ),
        ),
    )


def _service(repository: Repository, outbox: Outbox) -> HomeworkService:
    audit = Audits()
    counter = 0

    def next_id() -> UUID:
        nonlocal counter
        counter += 1
        return UUID(f"00000000-0000-7000-8000-{counter:012d}")

    return HomeworkService(
        repository=repository,
        archived_guard=ArchivedGuard(),
        outbox=outbox,
        authorizer=Authorizer(Guard(), clock=lambda: NOW),
        audit=AuditRecorder(audit, event_id_factory=next_id, clock=lambda: NOW),
        id_factory=next_id,
        clock=lambda: NOW,
    )


async def _create(
    repository: Repository, service: HomeworkService, run: UUID = RUN_A
) -> HomeworkRecord:
    homework, relation = await service.create_homework(
        transaction=object(),
        organization_id=ORG,
        course_run_id=run,
        expected_course_run_revision=0,
        title="Homework",
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    assert relation.current_publication_id is None
    assert relation.status == "draft"
    return homework


@pytest.mark.anyio
async def test_create_draft_and_full_immutable_version_round_trip() -> None:
    repository, outbox = Repository(), Outbox()
    service = _service(repository, outbox)
    homework = await _create(repository, service)
    requirements = _requirements("Version one")
    version = await service.create_version(
        transaction=object(),
        organization_id=ORG,
        homework_id=homework.homework_id,
        expected_homework_revision=0,
        requirements=requirements,
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    history = await service.history(
        transaction=object(),
        organization_id=ORG,
        homework_id=homework.homework_id,
        actor=_actor(),
    )

    assert history.versions == (version,)
    assert version.requirements == requirements
    assert version.requirements.requirements_digest == requirements.requirements_digest
    assert history.course_run_homeworks[0].current_publication_id is None
    assert outbox.events == []


@pytest.mark.anyio
async def test_two_runs_keep_independent_current_and_republish_emits_exact_event() -> None:
    repository, outbox = Repository(), Outbox()
    service = _service(repository, outbox)
    homework = await _create(repository, service, RUN_A)
    first = await service.create_version(
        transaction=object(),
        organization_id=ORG,
        homework_id=homework.homework_id,
        expected_homework_revision=0,
        requirements=_requirements("one"),
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    second = await service.create_version(
        transaction=object(),
        organization_id=ORG,
        homework_id=homework.homework_id,
        expected_homework_revision=1,
        requirements=_requirements("two"),
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    p_a1 = await service.publish_version(
        transaction=object(),
        organization_id=ORG,
        course_run_id=RUN_A,
        homework_version_id=first.version_id,
        expected_version_revision=0,
        submission_deadline=NOW + timedelta(days=1),
        review_deadline=NOW + timedelta(days=2),
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    p_b1 = await service.publish_version(
        transaction=object(),
        organization_id=ORG,
        course_run_id=RUN_B,
        homework_version_id=first.version_id,
        expected_version_revision=0,
        submission_deadline=NOW + timedelta(days=1),
        review_deadline=NOW + timedelta(days=2),
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    prior_bytes = repr(repository.publications[p_a1.publication_id])
    p_a2 = await service.publish_version(
        transaction=object(),
        organization_id=ORG,
        course_run_id=RUN_A,
        homework_version_id=second.version_id,
        expected_version_revision=0,
        submission_deadline=NOW + timedelta(days=3),
        review_deadline=NOW + timedelta(days=4),
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )

    assert (
        repository.relations[(RUN_A, homework.homework_id)].current_publication_id
        == p_a2.publication_id
    )
    assert (
        repository.relations[(RUN_B, homework.homework_id)].current_publication_id
        == p_b1.publication_id
    )
    assert repr(repository.publications[p_a1.publication_id]) == prior_bytes
    assert outbox.events[0].previous_homework_version_id is None
    assert outbox.events[0].previous_publication_id is None
    event = outbox.events[-1]
    assert event == HomeworkRequirementsChanged(
        organization_id=ORG,
        course_run_id=RUN_A,
        course_run_homework_id=p_a2.course_run_homework_id,
        homework_id=homework.homework_id,
        previous_homework_version_id=first.version_id,
        current_homework_version_id=second.version_id,
        previous_publication_id=p_a1.publication_id,
        current_publication_id=p_a2.publication_id,
        publication_sequence=2,
    )


@pytest.mark.anyio
async def test_stale_homework_and_publication_cas_fail_closed() -> None:
    repository, outbox = Repository(), Outbox()
    service = _service(repository, outbox)
    homework = await _create(repository, service)
    with pytest.raises(HomeworkRevisionConflict):
        await service.create_version(
            transaction=object(),
            organization_id=ORG,
            homework_id=homework.homework_id,
            expected_homework_revision=9,
            requirements=_requirements("stale"),
            actor=_actor(),
            request_id=REQUEST,
            trace_id=TRACE,
        )
    version = await service.create_version(
        transaction=object(),
        organization_id=ORG,
        homework_id=homework.homework_id,
        expected_homework_revision=0,
        requirements=_requirements("valid"),
        actor=_actor(),
        request_id=REQUEST,
        trace_id=TRACE,
    )
    repository.fail_next_publication_cas = True
    with pytest.raises(HomeworkRevisionConflict, match="current pointer CAS"):
        await service.publish_version(
            transaction=object(),
            organization_id=ORG,
            course_run_id=RUN_A,
            homework_version_id=version.version_id,
            expected_version_revision=0,
            submission_deadline=NOW + timedelta(days=1),
            review_deadline=NOW + timedelta(days=2),
            actor=_actor(),
            request_id=REQUEST,
            trace_id=TRACE,
        )
