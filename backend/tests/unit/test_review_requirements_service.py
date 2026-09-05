from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import anyio
import pytest

from review_platform.application.audit import AuditEvent, AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.review_requirements import (
    ExistingRequirementsMigration,
    MigrateReviewRequirementsCommand,
    RequirementsCriterion,
    RequirementsMigrationContext,
    RequirementsTarget,
    ReviewRequirementsConflict,
    ReviewRequirementsMigrationService,
)
from review_platform.infrastructure.db.models.publication import ReviewIterationRelation
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionRecord,
    ReviewNoteRecord,
    ReviewRevisionDraft,
    ReviewRevisionRecord,
)

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000000001")
REVIEWER = UUID("00000000-0000-7000-8000-000000013001")
CASE = UUID("00000000-0000-7000-8000-000000013002")
PREDECESSOR = UUID("00000000-0000-7000-8000-000000013003")
SOURCE_REVISION = UUID("00000000-0000-7000-8000-000000013004")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000013005")
HOMEWORK = UUID("00000000-0000-7000-8000-000000013006")
OLD_HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000013007")
NEW_HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000013008")
OLD_CRITERION_SET = UUID("00000000-0000-7000-8000-000000013009")
NEW_CRITERION_SET = UUID("00000000-0000-7000-8000-000000013010")
OLD_MATCH = UUID("00000000-0000-7000-8000-000000013011")
OLD_REMOVED = UUID("00000000-0000-7000-8000-000000013012")
NEW_MATCH = UUID("00000000-0000-7000-8000-000000013013")
NEW_ADDED = UUID("00000000-0000-7000-8000-000000013014")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000013015")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000013016")
REQUEST = UUID("00000000-0000-7000-8000-000000013017")
TRACE = UUID("00000000-0000-7000-8000-000000013018")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class IDs:
    def __init__(self, value: int = 14000) -> None:
        self.value = value

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


class Transaction:
    def __init__(self, name: str) -> None:
        self.name = name
        self.successor: ReviewIteration | None = None
        self.relation: ReviewIterationRelation | None = None


class Guard:
    def __init__(self, actor: RequestActor) -> None:
        self.actor = actor
        self.early = 0
        self.final = 0

    def _snapshot(self) -> AuthVersionSnapshot:
        assert self.actor.user_id is not None
        assert self.actor.membership_revision is not None
        assert self.actor.auth_epoch is not None
        return AuthVersionSnapshot(
            organization_id=self.actor.organization_id,
            user_id=self.actor.user_id,
            roles=self.actor.roles,
            membership_revision=self.actor.membership_revision,
            auth_epoch=self.actor.auth_epoch,
            active=True,
        )

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        assert actor == self.actor
        self.early += 1
        return self._snapshot()

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert actor == self.actor
        assert isinstance(transaction, Transaction)
        self.final += 1
        return self._snapshot()


class AuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        assert isinstance(transaction, Transaction)
        self.events.append(event)


class RevisionRepository:
    def __init__(self, target: RequirementsTarget) -> None:
        self.target = target
        self.drafts: list[ReviewRevisionDraft] = []
        self.revision_ids: set[UUID] = set()

    async def next_revision_number(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
    ) -> int:
        assert organization_id == ORG and review_iteration_id != PREDECESSOR
        return 1

    async def append(self, draft: ReviewRevisionDraft) -> ReviewRevisionRecord:
        self.drafts.append(draft)
        self.revision_ids.add(draft.review_revision_id)
        target_keys = {
            criterion.criterion_id: criterion.stable_key
            for criterion in self.target.criteria
        }
        return ReviewRevisionRecord(
            organization_id=draft.organization_id,
            review_revision_id=draft.review_revision_id,
            review_iteration_id=draft.review_iteration_id,
            revision_number=draft.revision_number,
            author_user_id=draft.author_user_id,
            base_revision_id=draft.base_revision_id,
            feedback=draft.feedback,
            total_score=sum(
                (decision.points for decision in draft.decisions),
                start=Decimal("0"),
            ),
            created_at=draft.created_at,
            decisions=tuple(
                ReviewDecisionRecord(
                    decision_id=decision.decision_id,
                    criterion_id=decision.criterion_id,
                    criterion_key=target_keys[decision.criterion_id],
                    criterion_position=index,
                    points=decision.points,
                    decision=decision.decision,
                    reason=decision.reason,
                    evidence_ids=decision.evidence_ids,
                    ai_suggestion_id=decision.ai_suggestion_id,
                )
                for index, decision in enumerate(draft.decisions)
            ),
            notes=tuple(
                ReviewNoteRecord(
                    note_id=note.note_id,
                    criterion_id=note.criterion_id,
                    text=note.text,
                    author_user_id=note.author_user_id,
                    position=note.position,
                )
                for note in draft.notes
            ),
        )

    async def compare_and_set_current(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID | None,
        new_revision_id: UUID,
    ) -> bool:
        assert organization_id == ORG
        assert review_iteration_id != PREDECESSOR
        assert expected_iteration_revision == 0
        assert expected_current_revision_id is None
        assert new_revision_id in self.revision_ids
        return True


class Repository:
    def __init__(self, context: RequirementsMigrationContext, target: RequirementsTarget) -> None:
        self.context = context
        self.target = target
        self.current_iteration = PREDECESSOR
        self.review_case_revision = 0
        self.existing: ExistingRequirementsMigration | None = None
        self.committed_successors: list[ReviewIteration] = []
        self.committed_relations: list[ReviewIterationRelation] = []
        self.race_barrier = False
        self._find_calls = 0
        self._both_find = anyio.Event()
        self._cas_lock = anyio.Lock()

    async def find_existing_migration(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        target_homework_version_id: UUID,
        target_criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> ExistingRequirementsMigration | None:
        assert organization_id == ORG
        assert predecessor_iteration_id == PREDECESSOR
        assert target_homework_version_id in {
            OLD_HOMEWORK_VERSION,
            NEW_HOMEWORK_VERSION,
        }
        assert target_criterion_set_id in {OLD_CRITERION_SET, NEW_CRITERION_SET}
        assert isinstance(transaction, Transaction)
        if self.race_barrier and self._find_calls < 2:
            self._find_calls += 1
            if self._find_calls == 2:
                self._both_find.set()
            await self._both_find.wait()
            return None
        return self.existing

    async def lock_current_predecessor(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        *,
        expected_predecessor_revision: int,
        transaction: object,
    ) -> RequirementsMigrationContext | None:
        assert organization_id == ORG
        assert predecessor_iteration_id == PREDECESSOR
        assert isinstance(transaction, Transaction)
        if (
            self.current_iteration != PREDECESSOR
            or expected_predecessor_revision != 0
        ):
            return None
        return self.context

    async def load_target(
        self,
        organization_id: UUID,
        homework_id: UUID,
        homework_version_id: UUID,
        criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> RequirementsTarget | None:
        assert organization_id == ORG
        assert homework_id == HOMEWORK
        assert homework_version_id == self.target.homework_version_id
        assert criterion_set_id == self.target.criterion_set_id
        assert isinstance(transaction, Transaction)
        return self.target

    async def next_iteration_number(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        transaction: object,
    ) -> int:
        assert organization_id == ORG
        assert review_case_id == CASE
        assert isinstance(transaction, Transaction)
        return 2

    async def append_successor(
        self,
        successor: ReviewIteration,
        relation: ReviewIterationRelation,
        *,
        transaction: object,
    ) -> None:
        assert isinstance(transaction, Transaction)
        transaction.successor = successor
        transaction.relation = relation

    async def compare_and_set_current_iteration(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        expected_review_case_revision: int,
        expected_current_iteration_id: UUID,
        new_iteration_id: UUID,
        transaction: object,
    ) -> bool:
        assert organization_id == ORG
        assert review_case_id == CASE
        assert isinstance(transaction, Transaction)
        async with self._cas_lock:
            if (
                self.current_iteration != expected_current_iteration_id
                or self.review_case_revision != expected_review_case_revision
            ):
                return False
            assert transaction.successor is not None
            assert transaction.relation is not None
            self.current_iteration = new_iteration_id
            self.review_case_revision += 1
            self.committed_successors.append(transaction.successor)
            self.committed_relations.append(transaction.relation)
            return True

    def record_existing(self, result: Any) -> None:
        self.existing = ExistingRequirementsMigration(
            organization_id=result.organization_id,
            review_case_id=result.review_case_id,
            predecessor_iteration_id=result.predecessor_iteration_id,
            successor_iteration_id=result.successor_iteration_id,
            successor_iteration_revision=result.successor_iteration_revision,
            successor_revision_id=result.successor_revision_id,
            target_homework_version_id=NEW_HOMEWORK_VERSION,
            target_criterion_set_id=NEW_CRITERION_SET,
            transferred_decision_count=result.transferred_decision_count,
        )


def _source_revision() -> ReviewRevisionRecord:
    return ReviewRevisionRecord(
        organization_id=ORG,
        review_revision_id=SOURCE_REVISION,
        review_iteration_id=PREDECESSOR,
        revision_number=1,
        author_user_id=REVIEWER,
        base_revision_id=None,
        feedback="Preserved human feedback",
        total_score=Decimal("8"),
        created_at=NOW,
        decisions=(
            ReviewDecisionRecord(
                UUID("00000000-0000-7000-8000-000000013101"),
                OLD_MATCH,
                "correctness",
                0,
                Decimal("4"),
                "manual",
                "Matching human decision",
                ("evidence:match",),
                UUID("00000000-0000-7000-8000-000000013102"),
            ),
            ReviewDecisionRecord(
                UUID("00000000-0000-7000-8000-000000013103"),
                OLD_REMOVED,
                "removed",
                1,
                Decimal("4"),
                "manual",
                "Removed human decision",
                ("evidence:removed",),
                None,
            ),
        ),
        notes=(
            ReviewNoteRecord(
                UUID("00000000-0000-7000-8000-000000013104"),
                OLD_MATCH,
                "Matching criterion note",
                REVIEWER,
                0,
            ),
            ReviewNoteRecord(
                UUID("00000000-0000-7000-8000-000000013105"),
                OLD_REMOVED,
                "Removed criterion note",
                REVIEWER,
                1,
            ),
            ReviewNoteRecord(
                UUID("00000000-0000-7000-8000-000000013106"),
                None,
                "Global note",
                REVIEWER,
                2,
            ),
        ),
    )


def _context() -> RequirementsMigrationContext:
    return RequirementsMigrationContext(
        organization_id=ORG,
        review_case_id=CASE,
        review_case_revision=0,
        current_iteration_id=PREDECESSOR,
        predecessor_iteration_id=PREDECESSOR,
        predecessor_iteration_revision=0,
        iteration_number=1,
        course_run_id=COURSE_RUN,
        homework_id=HOMEWORK,
        student_id=REVIEWER,
        submission_version_id=SUBMISSION_VERSION,
        artifact_version_id=ARTIFACT_VERSION,
        effective_deadline=NOW + timedelta(days=1),
        responsible_reviewer_id=REVIEWER,
        status="published",
        predecessor_homework_version_id=OLD_HOMEWORK_VERSION,
        predecessor_criterion_set_id=OLD_CRITERION_SET,
        current_human_revision=_source_revision(),
        predecessor_criteria=(
            RequirementsCriterion(OLD_MATCH, "correctness", 0, Decimal("5")),
            RequirementsCriterion(OLD_REMOVED, "removed", 1, Decimal("5")),
        ),
    )


def _target() -> RequirementsTarget:
    return RequirementsTarget(
        organization_id=ORG,
        homework_id=HOMEWORK,
        homework_version_id=NEW_HOMEWORK_VERSION,
        criterion_set_id=NEW_CRITERION_SET,
        criteria=(
            RequirementsCriterion(NEW_MATCH, "correctness", 0, Decimal("5")),
            RequirementsCriterion(NEW_ADDED, "added", 1, Decimal("5")),
        ),
    )


def _actor() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )


def _command() -> MigrateReviewRequirementsCommand:
    return MigrateReviewRequirementsCommand(
        organization_id=ORG,
        predecessor_iteration_id=PREDECESSOR,
        expected_predecessor_revision=0,
        target_homework_version_id=NEW_HOMEWORK_VERSION,
        target_criterion_set_id=NEW_CRITERION_SET,
        request_id=REQUEST,
        trace_id=TRACE,
    )


def _service(
    repository: Repository,
    revisions: RevisionRepository,
    ids: IDs,
) -> tuple[ReviewRequirementsMigrationService, Guard, AuditRepository]:
    actor = _actor()
    guard = Guard(actor)
    audit = AuditRepository()
    return (
        ReviewRequirementsMigrationService(
            repository=repository,
            revisions=revisions,
            authorizer=Authorizer(guard, clock=lambda: NOW),
            audit=AuditRecorder(
                audit,
                event_id_factory=ids,
                clock=lambda: NOW,
            ),
            id_factory=ids,
            clock=lambda: NOW,
        ),
        guard,
        audit,
    )


async def test_migration_transfers_only_stable_key_intersection_without_placeholders() -> None:
    context = _context()
    assert context.current_human_revision is not None
    source_bytes = asdict(context.current_human_revision)
    target = _target()
    repository = Repository(context, target)
    revisions = RevisionRepository(target)
    service, guard, audit = _service(repository, revisions, IDs())
    transaction = Transaction("first")

    result = await service.migrate(_command(), actor=_actor(), transaction=transaction)

    assert result.transferred_decision_count == 1
    assert result.successor_iteration_revision == 1
    assert not result.replayed
    assert len(repository.committed_successors) == len(repository.committed_relations) == 1
    successor = repository.committed_successors[0]
    relation = repository.committed_relations[0]
    assert successor.origin == relation.kind == "requirements_migration"
    assert successor.predecessor_iteration_id == PREDECESSOR
    assert successor.homework_version_id == NEW_HOMEWORK_VERSION
    assert successor.criterion_set_id == NEW_CRITERION_SET
    draft = revisions.drafts[0]
    assert [decision.criterion_id for decision in draft.decisions] == [NEW_MATCH]
    assert draft.decisions[0].points == Decimal("4")
    assert draft.decisions[0].evidence_ids == ("evidence:match",)
    assert draft.decisions[0].ai_suggestion_id is None
    assert NEW_ADDED not in {decision.criterion_id for decision in draft.decisions}
    assert [note.criterion_id for note in draft.notes] == [NEW_MATCH, None]
    assert context.current_human_revision is not None
    assert asdict(context.current_human_revision) == source_bytes
    assert guard.early == guard.final == 1
    assert len(audit.events) == 1
    assert audit.events[0].action == "migrate_review_requirements"


async def test_sequential_replay_returns_same_successor_without_new_rows_or_audit() -> None:
    repository = Repository(_context(), _target())
    revisions = RevisionRepository(_target())
    ids = IDs(15000)
    service, guard, audit = _service(repository, revisions, ids)
    first = await service.migrate(
        _command(),
        actor=_actor(),
        transaction=Transaction("first"),
    )
    repository.record_existing(first)

    replay = await service.migrate(
        _command(),
        actor=_actor(),
        transaction=Transaction("replay"),
    )

    assert replay.replayed
    assert replay.successor_iteration_id == first.successor_iteration_id
    assert replay.successor_revision_id == first.successor_revision_id
    assert len(repository.committed_successors) == 1
    assert len(revisions.drafts) == 1
    assert len(audit.events) == 1
    assert guard.early == guard.final == 2


async def test_two_concurrent_commands_have_one_successor_cas_winner() -> None:
    repository = Repository(_context(), _target())
    repository.race_barrier = True
    revisions = RevisionRepository(_target())
    service, _, audit = _service(repository, revisions, IDs(16000))
    results: list[Any] = []
    conflicts: list[ReviewRequirementsConflict] = []

    async def migrate(name: str) -> None:
        try:
            results.append(
                await service.migrate(
                    _command(),
                    actor=_actor(),
                    transaction=Transaction(name),
                )
            )
        except ReviewRequirementsConflict as error:
            conflicts.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(migrate, "a")
        tasks.start_soon(migrate, "b")

    assert len(results) == len(conflicts) == 1
    assert len(repository.committed_successors) == 1
    assert len(repository.committed_relations) == 1
    assert repository.current_iteration == results[0].successor_iteration_id
    assert len(audit.events) == 1


async def test_stale_current_or_same_target_requirements_fail_closed() -> None:
    context = _context()
    repository = Repository(context, _target())
    repository.current_iteration = UUID("00000000-0000-7000-8000-000000013099")
    service, _, _ = _service(repository, RevisionRepository(_target()), IDs(17000))
    with pytest.raises(ReviewRequirementsConflict, match="stale"):
        await service.migrate(
            _command(),
            actor=_actor(),
            transaction=Transaction("stale"),
        )

    same_target = RequirementsTarget(
        organization_id=ORG,
        homework_id=HOMEWORK,
        homework_version_id=OLD_HOMEWORK_VERSION,
        criterion_set_id=OLD_CRITERION_SET,
        criteria=context.predecessor_criteria,
    )
    repository = Repository(context, same_target)
    service, _, _ = _service(
        repository,
        RevisionRepository(same_target),
        IDs(18000),
    )
    same_command = MigrateReviewRequirementsCommand(
        organization_id=ORG,
        predecessor_iteration_id=PREDECESSOR,
        expected_predecessor_revision=0,
        target_homework_version_id=OLD_HOMEWORK_VERSION,
        target_criterion_set_id=OLD_CRITERION_SET,
        request_id=REQUEST,
        trace_id=TRACE,
    )
    with pytest.raises(ReviewRequirementsConflict, match="target requirements"):
        await service.migrate(
            same_command,
            actor=_actor(),
            transaction=Transaction("same"),
        )
