from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import anyio
import pytest

from review_platform.application.audit import AuditEvent, AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.review_corrections import (
    CreateReviewCorrectionCommand,
    ExistingReviewCorrection,
    PublishedReviewSnapshot,
    ReviewCorrectionConflict,
    ReviewCorrectionNotFound,
    ReviewCorrectionService,
)
from review_platform.application.services.review_requirements import (
    ExistingRequirementsMigration,
    RequirementsCriterion,
    RequirementsMigrationContext,
    RequirementsTarget,
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
REVIEWER = UUID("00000000-0000-7000-8000-000000014001")
CASE = UUID("00000000-0000-7000-8000-000000014002")
PREDECESSOR = UUID("00000000-0000-7000-8000-000000014003")
PUBLISHED_REVISION = UUID("00000000-0000-7000-8000-000000014004")
PUBLICATION = UUID("00000000-0000-7000-8000-000000014005")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000014006")
HOMEWORK = UUID("00000000-0000-7000-8000-000000014007")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000014008")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000014009")
CRITERION_A = UUID("00000000-0000-7000-8000-000000014010")
CRITERION_B = UUID("00000000-0000-7000-8000-000000014011")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000014012")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000014013")
REQUEST = UUID("00000000-0000-7000-8000-000000014014")
TRACE = UUID("00000000-0000-7000-8000-000000014015")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
REASON = "Correct the already published human result"


class IDs:
    def __init__(self, value: int = 15000) -> None:
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
    def __init__(self) -> None:
        self.early = 0
        self.final = 0

    @staticmethod
    def _snapshot() -> AuthVersionSnapshot:
        return AuthVersionSnapshot(
            organization_id=ORG,
            user_id=REVIEWER,
            roles=frozenset({"reviewer"}),
            membership_revision=0,
            auth_epoch=0,
            active=True,
        )

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        assert actor == _actor()
        self.early += 1
        return self._snapshot()

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert actor == _actor() and isinstance(transaction, Transaction)
        self.final += 1
        return self._snapshot()


class AuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        assert isinstance(transaction, Transaction)
        self.events.append(event)


class RevisionRepository:
    def __init__(self) -> None:
        self.drafts: list[ReviewRevisionDraft] = []
        self.identities: set[UUID] = set()

    async def next_revision_number(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
    ) -> int:
        assert organization_id == ORG and review_iteration_id != PREDECESSOR
        return 1

    async def append(self, draft: ReviewRevisionDraft) -> ReviewRevisionRecord:
        self.drafts.append(draft)
        self.identities.add(draft.review_revision_id)
        positions = {CRITERION_A: 0, CRITERION_B: 1}
        return ReviewRevisionRecord(
            organization_id=draft.organization_id,
            review_revision_id=draft.review_revision_id,
            review_iteration_id=draft.review_iteration_id,
            revision_number=1,
            author_user_id=draft.author_user_id,
            base_revision_id=None,
            feedback=draft.feedback,
            total_score=sum(
                (decision.points for decision in draft.decisions),
                start=Decimal("0"),
            ),
            created_at=draft.created_at,
            decisions=tuple(
                ReviewDecisionRecord(
                    decision.decision_id,
                    decision.criterion_id,
                    "correctness" if decision.criterion_id == CRITERION_A else "quality",
                    positions[decision.criterion_id],
                    decision.points,
                    decision.decision,
                    decision.reason,
                    decision.evidence_ids,
                    decision.ai_suggestion_id,
                )
                for decision in draft.decisions
            ),
            notes=tuple(
                ReviewNoteRecord(
                    note.note_id,
                    note.criterion_id,
                    note.text,
                    note.author_user_id,
                    note.position,
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
        assert organization_id == ORG and review_iteration_id != PREDECESSOR
        assert expected_iteration_revision == 0 and expected_current_revision_id is None
        return new_revision_id in self.identities


class SuccessorRepository:
    def __init__(self) -> None:
        self.context = _context()
        self.current = PREDECESSOR
        self.case_revision = 0
        self.committed: list[tuple[ReviewIteration, ReviewIterationRelation]] = []
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
        del target_homework_version_id, target_criterion_set_id
        assert organization_id == ORG and predecessor_iteration_id == PREDECESSOR
        assert isinstance(transaction, Transaction)
        return None

    async def lock_current_predecessor(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        *,
        expected_predecessor_revision: int,
        transaction: object,
    ) -> RequirementsMigrationContext | None:
        assert organization_id == ORG and predecessor_iteration_id == PREDECESSOR
        assert expected_predecessor_revision == 0 and isinstance(transaction, Transaction)
        return self.context if self.current == PREDECESSOR else None

    async def load_target(
        self,
        organization_id: UUID,
        homework_id: UUID,
        homework_version_id: UUID,
        criterion_set_id: UUID,
        *,
        transaction: object,
    ) -> RequirementsTarget | None:
        del organization_id, homework_id, homework_version_id, criterion_set_id, transaction
        return None

    async def next_iteration_number(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        transaction: object,
    ) -> int:
        assert organization_id == ORG and review_case_id == CASE
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
        assert organization_id == ORG and review_case_id == CASE
        assert isinstance(transaction, Transaction)
        async with self._cas_lock:
            if (
                self.current != expected_current_iteration_id
                or self.case_revision != expected_review_case_revision
            ):
                return False
            assert transaction.successor is not None and transaction.relation is not None
            assert transaction.successor.id == new_iteration_id
            self.current = new_iteration_id
            self.case_revision += 1
            self.committed.append((transaction.successor, transaction.relation))
            return True


class PublicationRepository:
    def __init__(self) -> None:
        self.snapshot: PublishedReviewSnapshot | None = PublishedReviewSnapshot(
            ORG,
            PREDECESSOR,
            PUBLICATION,
            _published_revision(),
        )
        self.existing: ExistingReviewCorrection | None = None
        self.race_barrier = False
        self._find_calls = 0
        self._both_find = anyio.Event()

    async def find_existing_correction(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        published_review_revision_id: UUID,
        reason: str,
        *,
        transaction: object,
    ) -> ExistingReviewCorrection | None:
        assert organization_id == ORG
        assert predecessor_iteration_id == PREDECESSOR
        assert published_review_revision_id == PUBLISHED_REVISION
        assert reason == REASON and isinstance(transaction, Transaction)
        if self.race_barrier and self._find_calls < 2:
            self._find_calls += 1
            if self._find_calls == 2:
                self._both_find.set()
            await self._both_find.wait()
            return None
        return self.existing

    async def require_published_revision(
        self,
        organization_id: UUID,
        predecessor_iteration_id: UUID,
        published_review_revision_id: UUID,
        *,
        transaction: object,
    ) -> PublishedReviewSnapshot | None:
        assert organization_id == ORG
        assert predecessor_iteration_id == PREDECESSOR
        assert published_review_revision_id == PUBLISHED_REVISION
        assert isinstance(transaction, Transaction)
        return self.snapshot

    def record_existing(self, result: Any) -> None:
        self.existing = ExistingReviewCorrection(
            result.organization_id,
            result.review_case_id,
            result.predecessor_iteration_id,
            result.published_review_revision_id,
            REASON,
            result.successor_iteration_id,
            result.successor_iteration_revision,
            result.successor_revision_id,
        )


def _published_revision() -> ReviewRevisionRecord:
    return ReviewRevisionRecord(
        organization_id=ORG,
        review_revision_id=PUBLISHED_REVISION,
        review_iteration_id=PREDECESSOR,
        revision_number=2,
        author_user_id=REVIEWER,
        base_revision_id=None,
        feedback="Exact published feedback",
        total_score=Decimal("8"),
        created_at=NOW,
        decisions=(
            ReviewDecisionRecord(
                UUID("00000000-0000-7000-8000-000000014101"),
                CRITERION_A,
                "correctness",
                0,
                Decimal("5"),
                "accepted",
                "Published decision A",
                ("evidence:a",),
                UUID("00000000-0000-7000-8000-000000014102"),
            ),
            ReviewDecisionRecord(
                UUID("00000000-0000-7000-8000-000000014103"),
                CRITERION_B,
                "quality",
                1,
                Decimal("3"),
                "manual",
                "Published decision B",
                ("evidence:b",),
                None,
            ),
        ),
        notes=(
            ReviewNoteRecord(
                UUID("00000000-0000-7000-8000-000000014104"),
                CRITERION_A,
                "Criterion note",
                REVIEWER,
                0,
            ),
            ReviewNoteRecord(
                UUID("00000000-0000-7000-8000-000000014105"),
                None,
                "Global note",
                REVIEWER,
                1,
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
        predecessor_homework_version_id=HOMEWORK_VERSION,
        predecessor_criterion_set_id=CRITERION_SET,
        current_human_revision=None,
        predecessor_criteria=(
            RequirementsCriterion(CRITERION_A, "correctness", 0, Decimal("5")),
            RequirementsCriterion(CRITERION_B, "quality", 1, Decimal("5")),
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


def _command() -> CreateReviewCorrectionCommand:
    return CreateReviewCorrectionCommand(
        organization_id=ORG,
        predecessor_iteration_id=PREDECESSOR,
        expected_predecessor_revision=0,
        published_review_revision_id=PUBLISHED_REVISION,
        reason=REASON,
        request_id=REQUEST,
        trace_id=TRACE,
    )


def _service(
    successors: SuccessorRepository,
    publications: PublicationRepository,
    revisions: RevisionRepository,
    ids: IDs,
) -> tuple[ReviewCorrectionService, Guard, AuditRepository]:
    guard = Guard()
    audit = AuditRepository()
    return (
        ReviewCorrectionService(
            successors=successors,
            publications=publications,
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


async def test_correction_copies_exact_published_snapshot_and_preserves_source_bytes() -> None:
    successors = SuccessorRepository()
    publications = PublicationRepository()
    revisions = RevisionRepository()
    service, guard, audit = _service(successors, publications, revisions, IDs())
    assert publications.snapshot is not None
    source_bytes = asdict(publications.snapshot)

    result = await service.create(
        _command(),
        actor=_actor(),
        transaction=Transaction("first"),
    )

    assert not result.replayed
    assert len(successors.committed) == 1
    successor, relation = successors.committed[0]
    assert successor.origin == relation.kind == "correction"
    assert successor.predecessor_iteration_id == PREDECESSOR
    assert successor.homework_version_id == HOMEWORK_VERSION
    assert successor.criterion_set_id == CRITERION_SET
    draft = revisions.drafts[0]
    assert draft.feedback == "Exact published feedback"
    assert [decision.points for decision in draft.decisions] == [
        Decimal("5"),
        Decimal("3"),
    ]
    assert [decision.evidence_ids for decision in draft.decisions] == [
        ("evidence:a",),
        ("evidence:b",),
    ]
    assert all(decision.ai_suggestion_id is None for decision in draft.decisions)
    assert [note.text for note in draft.notes] == ["Criterion note", "Global note"]
    assert publications.snapshot is not None
    assert asdict(publications.snapshot) == source_bytes
    assert guard.early == guard.final == 1
    assert len(audit.events) == 1
    assert audit.events[0].action == "create_review_correction"
    assert audit.events[0].sanitized_details["reason"] == REASON


async def test_sequential_replay_returns_same_correction_without_new_snapshot() -> None:
    successors = SuccessorRepository()
    publications = PublicationRepository()
    revisions = RevisionRepository()
    service, guard, audit = _service(successors, publications, revisions, IDs(16000))
    first = await service.create(
        _command(),
        actor=_actor(),
        transaction=Transaction("first"),
    )
    publications.record_existing(first)

    replay = await service.create(
        _command(),
        actor=_actor(),
        transaction=Transaction("replay"),
    )

    assert replay.replayed
    assert replay.successor_iteration_id == first.successor_iteration_id
    assert replay.successor_revision_id == first.successor_revision_id
    assert len(successors.committed) == len(revisions.drafts) == len(audit.events) == 1
    assert guard.early == guard.final == 2


async def test_concurrent_correction_has_one_review_case_cas_winner() -> None:
    successors = SuccessorRepository()
    publications = PublicationRepository()
    publications.race_barrier = True
    revisions = RevisionRepository()
    service, _, audit = _service(successors, publications, revisions, IDs(17000))
    results: list[Any] = []
    conflicts: list[ReviewCorrectionConflict] = []

    async def correct(name: str) -> None:
        try:
            results.append(
                await service.create(
                    _command(),
                    actor=_actor(),
                    transaction=Transaction(name),
                )
            )
        except ReviewCorrectionConflict as error:
            conflicts.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(correct, "a")
        tasks.start_soon(correct, "b")

    assert len(results) == len(conflicts) == 1
    assert len(successors.committed) == 1
    assert successors.current == results[0].successor_iteration_id
    assert len(audit.events) == 1


async def test_unpublished_or_incomplete_exact_revision_is_rejected() -> None:
    successors = SuccessorRepository()
    publications = PublicationRepository()
    publications.snapshot = None
    service, _, _ = _service(
        successors,
        publications,
        RevisionRepository(),
        IDs(18000),
    )
    with pytest.raises(ReviewCorrectionNotFound, match="published"):
        await service.create(
            _command(),
            actor=_actor(),
            transaction=Transaction("unpublished"),
        )

    incomplete = _published_revision()
    incomplete = replace(
        incomplete,
        decisions=incomplete.decisions[:1],
        total_score=Decimal("5"),
    )
    publications = PublicationRepository()
    publications.snapshot = PublishedReviewSnapshot(
        ORG,
        PREDECESSOR,
        PUBLICATION,
        incomplete,
    )
    service, _, _ = _service(
        SuccessorRepository(),
        publications,
        RevisionRepository(),
        IDs(19000),
    )
    with pytest.raises(ReviewCorrectionConflict, match="exact immutable"):
        await service.create(
            _command(),
            actor=_actor(),
            transaction=Transaction("incomplete"),
        )
