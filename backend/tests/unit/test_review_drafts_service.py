from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.review_drafts import (
    ReviewDecisionInput,
    ReviewDraftConflict,
    ReviewDraftIncomplete,
    ReviewDraftService,
    ReviewNoteInput,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    CriterionSnapshot,
    ReviewDecisionRecord,
    ReviewIterationRevisionContext,
    ReviewNoteRecord,
    ReviewRevisionDraft,
    ReviewRevisionRecord,
    ReviewRevisionRepositoryError,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000001401")
ITERATION = UUID("00000000-0000-7000-8000-000000001402")
CRITERION_A = UUID("00000000-0000-7000-8000-000000001403")
CRITERION_B = UUID("00000000-0000-7000-8000-000000001404")
SUGGESTION = UUID("00000000-0000-7000-8000-000000001405")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class Guard:
    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        return self.snapshot(actor)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        return self.snapshot(actor)

    @staticmethod
    def snapshot(actor: RequestActor) -> AuthVersionSnapshot:
        assert actor.user_id is not None
        assert actor.membership_revision is not None
        assert actor.auth_epoch is not None
        return AuthVersionSnapshot(
            actor.organization_id,
            actor.user_id,
            actor.roles,
            actor.membership_revision,
            actor.auth_epoch,
            True,
        )


class Audits:
    async def append(self, event: object, *, transaction: object) -> None:
        return None


class Repository:
    def __init__(self) -> None:
        self.context = ReviewIterationRevisionContext(
            ORG,
            ITERATION,
            UUID(int=20),
            UUID(int=21),
            None,
            0,
            Decimal("10"),
            (
                CriterionSnapshot(CRITERION_A, "a", 1, "A", Decimal("6")),
                CriterionSnapshot(CRITERION_B, "b", 2, "B", Decimal("4")),
            ),
        )
        self.records: list[ReviewRevisionRecord] = []
        self.ai_state: dict[UUID, dict[str, object]] = {
            SUGGESTION: {"points": Decimal("5")}
        }

    async def lock_iteration(
        self, organization_id: UUID, review_iteration_id: UUID
    ) -> ReviewIterationRevisionContext | None:
        return self.context if (organization_id, review_iteration_id) == (ORG, ITERATION) else None

    async def next_revision_number(self, organization_id: UUID, review_iteration_id: UUID) -> int:
        return len(self.records) + 1

    async def append(self, draft: ReviewRevisionDraft) -> ReviewRevisionRecord:
        limits = {CRITERION_A: Decimal("6"), CRITERION_B: Decimal("4")}
        for decision in draft.decisions:
            if decision.points < 0 or decision.points > limits[decision.criterion_id]:
                raise ReviewRevisionRepositoryError("points are outside criterion range")
            if not decision.reason:
                raise ReviewRevisionRepositoryError("reason is required")
        total = sum((decision.points for decision in draft.decisions), Decimal("0"))
        record = ReviewRevisionRecord(
            organization_id=draft.organization_id,
            review_revision_id=draft.review_revision_id,
            review_iteration_id=draft.review_iteration_id,
            revision_number=draft.revision_number,
            author_user_id=draft.author_user_id,
            base_revision_id=draft.base_revision_id,
            feedback=draft.feedback,
            total_score=total,
            created_at=draft.created_at,
            decisions=tuple(
                ReviewDecisionRecord(
                    item.decision_id,
                    item.criterion_id,
                    "a" if item.criterion_id == CRITERION_A else "b",
                    1 if item.criterion_id == CRITERION_A else 2,
                    item.points,
                    item.decision,
                    item.reason,
                    item.evidence_ids,
                    item.ai_suggestion_id,
                )
                for item in draft.decisions
            ),
            notes=tuple(
                ReviewNoteRecord(
                    item.note_id,
                    item.criterion_id,
                    item.text,
                    item.author_user_id,
                    item.position,
                )
                for item in draft.notes
            ),
        )
        self.records.append(record)
        return record

    async def compare_and_set_current(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID | None,
        new_revision_id: UUID,
    ) -> bool:
        if (
            self.context.iteration_revision != expected_iteration_revision
            or self.context.current_revision_id != expected_current_revision_id
        ):
            return False
        self.context = ReviewIterationRevisionContext(
            self.context.organization_id,
            self.context.review_iteration_id,
            self.context.homework_version_id,
            self.context.criterion_set_id,
            new_revision_id,
            expected_iteration_revision + 1,
            self.context.homework_max_score,
            self.context.criteria,
        )
        return True


def _actor() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )


def _service(repository: Repository) -> ReviewDraftService:
    ids = iter(UUID(int=value) for value in range(100, 1000))
    return ReviewDraftService(
        repository=repository,
        authorizer=Authorizer(Guard(), clock=lambda: NOW),
        audit=AuditRecorder(Audits(), event_id_factory=lambda: next(ids), clock=lambda: NOW),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
    )


def _decisions(points_a: str = "6") -> tuple[ReviewDecisionInput, ...]:
    return (
        ReviewDecisionInput(
            CRITERION_A,
            Decimal(points_a),
            "accepted",
            "Evidence supports it",
            ("artifact:path:1",),
            SUGGESTION,
        ),
        ReviewDecisionInput(CRITERION_B, Decimal("4"), "manual", "Human decision"),
    )


@pytest.mark.anyio
async def test_human_revision_and_notes_remain_byte_stable_across_late_ai_change() -> None:
    repository = Repository()
    record = await _service(repository).save(
        transaction=object(),
        organization_id=ORG,
        review_iteration_id=ITERATION,
        expected_iteration_revision=0,
        expected_current_revision_id=None,
        feedback="Human feedback",
        decisions=_decisions(),
        notes=(
            ReviewNoteInput("Criterion note", CRITERION_A),
            ReviewNoteInput("Global note"),
        ),
        actor=_actor(),
        request_id=UUID(int=1),
        trace_id=UUID(int=2),
    )
    before = repr(record)
    repository.ai_state[SUGGESTION] = {"points": Decimal("0"), "late": True}

    assert repr(repository.records[0]) == before
    assert record.decisions[0].ai_suggestion_id == SUGGESTION
    assert [note.criterion_id for note in record.notes] == [CRITERION_A, None]
    assert record.total_score == Decimal("10")
    assert not hasattr(record, "publication_state")


@pytest.mark.anyio
async def test_stale_concurrent_save_has_one_winner() -> None:
    repository = Repository()
    service = _service(repository)
    first = await service.save(
        transaction=object(),
        organization_id=ORG,
        review_iteration_id=ITERATION,
        expected_iteration_revision=0,
        expected_current_revision_id=None,
        feedback="first",
        decisions=_decisions(),
        notes=(),
        actor=_actor(),
        request_id=UUID(int=3),
        trace_id=UUID(int=4),
    )
    with pytest.raises(ReviewDraftConflict):
        await service.save(
            transaction=object(),
            organization_id=ORG,
            review_iteration_id=ITERATION,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            feedback="stale",
            decisions=_decisions(),
            notes=(),
            actor=_actor(),
            request_id=UUID(int=5),
            trace_id=UUID(int=6),
        )
    assert repository.context.current_revision_id == first.review_revision_id
    assert len(repository.records) == 1


@pytest.mark.anyio
async def test_complete_criteria_and_repository_score_bounds_are_typed() -> None:
    repository = Repository()
    service = _service(repository)
    with pytest.raises(ReviewDraftIncomplete, match="exactly one"):
        await service.save(
            transaction=object(),
            organization_id=ORG,
            review_iteration_id=ITERATION,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            feedback="missing",
            decisions=_decisions()[:1],
            notes=(),
            actor=_actor(),
            request_id=UUID(int=7),
            trace_id=UUID(int=8),
        )
    with pytest.raises(ReviewRevisionRepositoryError, match="points"):
        await service.save(
            transaction=object(),
            organization_id=ORG,
            review_iteration_id=ITERATION,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            feedback="invalid",
            decisions=_decisions("7"),
            notes=(),
            actor=_actor(),
            request_id=UUID(int=9),
            trace_id=UUID(int=10),
        )
