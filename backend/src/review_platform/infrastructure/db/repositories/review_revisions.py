"""Tenant-scoped immutable human review revision persistence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.infrastructure.db.models.ai_review import (
    AICriterionSuggestion,
    AIReviewRun,
)
from review_platform.infrastructure.db.models.homework import Criterion, HomeworkVersion
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)

type ReviewDecisionKind = Literal["accepted", "changed", "manual"]


class ReviewRevisionRepositoryError(RuntimeError):
    """A human revision violated tenant, iteration, or criterion affinity."""


class InvalidReviewRevisionTransaction(ReviewRevisionRepositoryError):
    pass


class ReviewRevisionConflict(ReviewRevisionRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class CriterionSnapshot:
    criterion_id: UUID
    stable_key: str
    position: int
    title: str
    max_points: Decimal


@dataclass(frozen=True, slots=True)
class ReviewIterationRevisionContext:
    organization_id: UUID
    review_iteration_id: UUID
    homework_version_id: UUID
    criterion_set_id: UUID
    current_revision_id: UUID | None
    iteration_revision: int
    homework_max_score: Decimal
    criteria: tuple[CriterionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ReviewDecisionDraft:
    decision_id: UUID
    criterion_id: UUID
    points: Decimal
    decision: ReviewDecisionKind
    reason: str
    evidence_ids: tuple[str, ...]
    ai_suggestion_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReviewNoteDraft:
    note_id: UUID
    criterion_id: UUID | None
    text: str
    author_user_id: UUID
    position: int


@dataclass(frozen=True, slots=True)
class ReviewRevisionDraft:
    organization_id: UUID
    review_revision_id: UUID
    review_iteration_id: UUID
    revision_number: int
    author_user_id: UUID
    base_revision_id: UUID | None
    feedback: str
    decisions: tuple[ReviewDecisionDraft, ...]
    notes: tuple[ReviewNoteDraft, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewDecisionRecord:
    decision_id: UUID
    criterion_id: UUID
    criterion_key: str
    criterion_position: int
    points: Decimal
    decision: ReviewDecisionKind
    reason: str
    evidence_ids: tuple[str, ...]
    ai_suggestion_id: UUID | None


@dataclass(frozen=True, slots=True)
class ReviewNoteRecord:
    note_id: UUID
    criterion_id: UUID | None
    text: str
    author_user_id: UUID
    position: int


@dataclass(frozen=True, slots=True)
class ReviewRevisionRecord:
    organization_id: UUID
    review_revision_id: UUID
    review_iteration_id: UUID
    revision_number: int
    author_user_id: UUID
    base_revision_id: UUID | None
    feedback: str
    total_score: Decimal
    created_at: datetime
    decisions: tuple[ReviewDecisionRecord, ...]
    notes: tuple[ReviewNoteRecord, ...]


class SqlReviewRevisionRepository:
    """Caller-owned AsyncSession adapter; immutable rows have no mutation API."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_iteration(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
    ) -> ReviewIterationRevisionContext | None:
        iteration = await self._session.scalar(
            select(ReviewIteration)
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.id == review_iteration_id,
            )
            .with_for_update()
        )
        if iteration is None:
            return None
        homework_version = await self._session.scalar(
            select(HomeworkVersion).where(
                HomeworkVersion.organization_id == organization_id,
                HomeworkVersion.id == iteration.homework_version_id,
            )
        )
        if homework_version is None:
            raise ReviewRevisionRepositoryError(
                "ReviewIteration homework version provenance is missing"
            )
        criteria = (
            await self._session.scalars(
                select(Criterion)
                .where(
                    Criterion.organization_id == organization_id,
                    Criterion.criterion_set_id == iteration.criterion_set_id,
                    Criterion.active.is_(True),
                )
                .order_by(Criterion.position, Criterion.id)
            )
        ).all()
        if iteration.current_revision_id is not None:
            current = await self._session.scalar(
                select(ReviewRevision.id).where(
                    ReviewRevision.organization_id == organization_id,
                    ReviewRevision.review_iteration_id == review_iteration_id,
                    ReviewRevision.id == iteration.current_revision_id,
                )
            )
            if current is None:
                raise ReviewRevisionRepositoryError(
                    "ReviewIteration current revision pointer is invalid"
                )
        return ReviewIterationRevisionContext(
            organization_id=iteration.organization_id,
            review_iteration_id=iteration.id,
            homework_version_id=iteration.homework_version_id,
            criterion_set_id=iteration.criterion_set_id,
            current_revision_id=iteration.current_revision_id,
            iteration_revision=iteration.revision,
            homework_max_score=homework_version.max_score,
            criteria=tuple(
                CriterionSnapshot(
                    criterion_id=criterion.id,
                    stable_key=criterion.stable_key,
                    position=criterion.position,
                    title=criterion.title,
                    max_points=criterion.max_points,
                )
                for criterion in criteria
            ),
        )

    async def next_revision_number(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
    ) -> int:
        latest = await self._session.scalar(
            select(func.max(ReviewRevision.revision_number)).where(
                ReviewRevision.organization_id == organization_id,
                ReviewRevision.review_iteration_id == review_iteration_id,
            )
        )
        return int(latest or 0) + 1

    async def append(self, draft: ReviewRevisionDraft) -> ReviewRevisionRecord:
        context = await self.lock_iteration(
            draft.organization_id,
            draft.review_iteration_id,
        )
        if context is None:
            raise ReviewRevisionRepositoryError("tenant ReviewIteration was not found")
        expected_number = await self.next_revision_number(
            draft.organization_id,
            draft.review_iteration_id,
        )
        if draft.revision_number != expected_number:
            raise ReviewRevisionConflict(
                f"revision number must be {expected_number}, got {draft.revision_number}"
            )
        await self._validate_base(draft)
        criteria = {criterion.criterion_id: criterion for criterion in context.criteria}
        await self._validate_decisions(draft, criteria=criteria)
        self._validate_notes(draft, criteria=criteria)
        total_score = sum(
            (decision.points for decision in draft.decisions),
            start=Decimal("0"),
        )
        if total_score > context.homework_max_score:
            raise ReviewRevisionRepositoryError(
                "human decision total exceeds immutable HomeworkVersion maximum"
            )
        revision = ReviewRevision(
            id=draft.review_revision_id,
            organization_id=draft.organization_id,
            review_iteration_id=draft.review_iteration_id,
            revision_number=draft.revision_number,
            author_user_id=draft.author_user_id,
            base_revision_id=draft.base_revision_id,
            feedback=draft.feedback,
            total_score=total_score,
            created_at=draft.created_at,
        )
        self._session.add(revision)
        await self._session.flush([revision])
        self._session.add_all(
            [
                ReviewCriterionDecision(
                    id=decision.decision_id,
                    organization_id=draft.organization_id,
                    review_revision_id=draft.review_revision_id,
                    criterion_id=decision.criterion_id,
                    ai_suggestion_id=decision.ai_suggestion_id,
                    points=decision.points,
                    decision=decision.decision,
                    reason=decision.reason,
                    evidence_ids=list(decision.evidence_ids),
                )
                for decision in draft.decisions
            ]
            + [
                ReviewNote(
                    id=note.note_id,
                    organization_id=draft.organization_id,
                    review_revision_id=draft.review_revision_id,
                    criterion_id=note.criterion_id,
                    text=note.text,
                    author_user_id=note.author_user_id,
                    position=note.position,
                )
                for note in draft.notes
            ]
        )
        await self._session.flush()
        record = await self.get_revision(
            draft.organization_id,
            draft.review_iteration_id,
            draft.review_revision_id,
        )
        if record is None:
            raise ReviewRevisionRepositoryError("appended ReviewRevision could not be read")
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
        revision = await self._session.scalar(
            select(ReviewRevision.id).where(
                ReviewRevision.organization_id == organization_id,
                ReviewRevision.review_iteration_id == review_iteration_id,
                ReviewRevision.id == new_revision_id,
            )
        )
        if revision is None:
            return False
        current_predicate = (
            ReviewIteration.current_revision_id.is_(None)
            if expected_current_revision_id is None
            else ReviewIteration.current_revision_id == expected_current_revision_id
        )
        result = await self._session.execute(
            update(ReviewIteration)
            .where(
                ReviewIteration.organization_id == organization_id,
                ReviewIteration.id == review_iteration_id,
                ReviewIteration.revision == expected_iteration_revision,
                current_predicate,
            )
            .values(
                current_revision_id=new_revision_id,
                revision=expected_iteration_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def get_revision(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        review_revision_id: UUID,
    ) -> ReviewRevisionRecord | None:
        row = await self._session.scalar(
            select(ReviewRevision).where(
                ReviewRevision.organization_id == organization_id,
                ReviewRevision.review_iteration_id == review_iteration_id,
                ReviewRevision.id == review_revision_id,
            )
        )
        if row is None:
            return None
        return await self._record(row)

    async def history(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
    ) -> tuple[ReviewRevisionRecord, ...]:
        rows = (
            await self._session.scalars(
                select(ReviewRevision)
                .where(
                    ReviewRevision.organization_id == organization_id,
                    ReviewRevision.review_iteration_id == review_iteration_id,
                )
                .order_by(ReviewRevision.revision_number, ReviewRevision.id)
            )
        ).all()
        return tuple([await self._record(row) for row in rows])

    async def _validate_base(self, draft: ReviewRevisionDraft) -> None:
        if draft.base_revision_id is None:
            return
        base = await self._session.scalar(
            select(ReviewRevision.id).where(
                ReviewRevision.organization_id == draft.organization_id,
                ReviewRevision.review_iteration_id == draft.review_iteration_id,
                ReviewRevision.id == draft.base_revision_id,
            )
        )
        if base is None:
            raise ReviewRevisionRepositoryError(
                "base ReviewRevision does not belong to tenant ReviewIteration"
            )

    async def _validate_decisions(
        self,
        draft: ReviewRevisionDraft,
        *,
        criteria: Mapping[UUID, CriterionSnapshot],
    ) -> None:
        decision_ids = [decision.decision_id for decision in draft.decisions]
        criterion_ids = [decision.criterion_id for decision in draft.decisions]
        if len(set(decision_ids)) != len(decision_ids):
            raise ReviewRevisionRepositoryError("duplicate human decision identity")
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ReviewRevisionRepositoryError("duplicate criterion decision")
        for decision in draft.decisions:
            criterion = criteria.get(decision.criterion_id)
            if criterion is None:
                raise ReviewRevisionRepositoryError(
                    "decision criterion is not active in ReviewIteration snapshot"
                )
            if decision.points < 0 or decision.points > criterion.max_points:
                raise ReviewRevisionRepositoryError(
                    "human decision points are outside criterion range"
                )
            if decision.decision not in {"accepted", "changed", "manual"}:
                raise ReviewRevisionRepositoryError("human decision kind is invalid")
            _validate_evidence_ids(decision.evidence_ids)
            if decision.ai_suggestion_id is not None:
                suggestion = await self._session.scalar(
                    select(AICriterionSuggestion.id)
                    .join(
                        AIReviewRun,
                        (AIReviewRun.organization_id == AICriterionSuggestion.organization_id)
                        & (AIReviewRun.id == AICriterionSuggestion.ai_review_run_id),
                    )
                    .where(
                        AICriterionSuggestion.organization_id == draft.organization_id,
                        AICriterionSuggestion.id == decision.ai_suggestion_id,
                        AICriterionSuggestion.criterion_id == decision.criterion_id,
                        AIReviewRun.review_iteration_id == draft.review_iteration_id,
                    )
                )
                if suggestion is None:
                    raise ReviewRevisionRepositoryError(
                        "AI suggestion does not match tenant iteration criterion"
                    )

    @staticmethod
    def _validate_notes(
        draft: ReviewRevisionDraft,
        *,
        criteria: Mapping[UUID, CriterionSnapshot],
    ) -> None:
        note_ids = [note.note_id for note in draft.notes]
        positions = [note.position for note in draft.notes]
        if len(set(note_ids)) != len(note_ids) or len(set(positions)) != len(positions):
            raise ReviewRevisionRepositoryError("duplicate human note identity or position")
        for note in draft.notes:
            if note.position < 0 or not note.text:
                raise ReviewRevisionRepositoryError("human note position/text is invalid")
            if note.author_user_id != draft.author_user_id:
                raise ReviewRevisionRepositoryError(
                    "human note author differs from ReviewRevision author"
                )
            if note.criterion_id is not None and note.criterion_id not in criteria:
                raise ReviewRevisionRepositoryError(
                    "note criterion is not active in ReviewIteration snapshot"
                )

    async def _record(self, row: ReviewRevision) -> ReviewRevisionRecord:
        decision_rows = (
            await self._session.execute(
                select(ReviewCriterionDecision, Criterion)
                .join(
                    Criterion,
                    (Criterion.organization_id == ReviewCriterionDecision.organization_id)
                    & (Criterion.id == ReviewCriterionDecision.criterion_id),
                )
                .where(
                    ReviewCriterionDecision.organization_id == row.organization_id,
                    ReviewCriterionDecision.review_revision_id == row.id,
                )
                .order_by(Criterion.position, ReviewCriterionDecision.id)
            )
        ).all()
        note_rows = (
            await self._session.scalars(
                select(ReviewNote)
                .where(
                    ReviewNote.organization_id == row.organization_id,
                    ReviewNote.review_revision_id == row.id,
                )
                .order_by(ReviewNote.position, ReviewNote.id)
            )
        ).all()
        return ReviewRevisionRecord(
            organization_id=row.organization_id,
            review_revision_id=row.id,
            review_iteration_id=row.review_iteration_id,
            revision_number=row.revision_number,
            author_user_id=row.author_user_id,
            base_revision_id=row.base_revision_id,
            feedback=row.feedback,
            total_score=row.total_score,
            created_at=row.created_at,
            decisions=tuple(
                ReviewDecisionRecord(
                    decision_id=decision.id,
                    criterion_id=decision.criterion_id,
                    criterion_key=criterion.stable_key,
                    criterion_position=criterion.position,
                    points=decision.points,
                    decision=cast(ReviewDecisionKind, decision.decision),
                    reason=decision.reason,
                    evidence_ids=tuple(cast(Sequence[str], decision.evidence_ids)),
                    ai_suggestion_id=decision.ai_suggestion_id,
                )
                for decision, criterion in decision_rows
            ),
            notes=tuple(
                ReviewNoteRecord(
                    note_id=note.id,
                    criterion_id=note.criterion_id,
                    text=note.text,
                    author_user_id=note.author_user_id,
                    position=note.position,
                )
                for note in note_rows
            ),
        )


def _validate_evidence_ids(values: Sequence[str]) -> None:
    if len(set(values)) != len(values) or any(
        not value or len(value) > 1024 for value in values
    ):
        raise ReviewRevisionRepositoryError(
            "evidence IDs must be unique non-empty bounded strings"
        )


def require_review_revision_repository(transaction: object) -> SqlReviewRevisionRepository:
    if not isinstance(transaction, AsyncSession):
        raise InvalidReviewRevisionTransaction(
            "review revision repository requires caller-owned AsyncSession"
        )
    return SqlReviewRevisionRepository(transaction)


__all__ = [
    "CriterionSnapshot",
    "InvalidReviewRevisionTransaction",
    "ReviewDecisionDraft",
    "ReviewDecisionKind",
    "ReviewDecisionRecord",
    "ReviewIterationRevisionContext",
    "ReviewNoteDraft",
    "ReviewNoteRecord",
    "ReviewRevisionConflict",
    "ReviewRevisionDraft",
    "ReviewRevisionRecord",
    "ReviewRevisionRepositoryError",
    "SqlReviewRevisionRepository",
    "require_review_revision_repository",
]
