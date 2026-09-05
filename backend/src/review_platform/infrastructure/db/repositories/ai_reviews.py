"""Tenant-scoped persistence primitives for AI review orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.domain.primitives import sanitize_error, utc_now
from review_platform.infrastructure.db.models.ai_review import (
    AICriterionSuggestion,
    AIReviewAttempt,
    AIReviewEventReceipt,
    AIReviewRun,
    AISignal,
)

RUN_TERMINAL = frozenset({"succeeded", "action_required", "stale"})
ATTEMPT_TERMINAL = frozenset({"succeeded", "action_required", "stale"})
RUN_TRANSITIONS = {
    "pending": {"running", "action_required", "stale"},
    "running": {"partial", "succeeded", "retryable_failed", "action_required", "stale"},
    "partial": {"running", "partial", "succeeded", "retryable_failed", "action_required", "stale"},
    "retryable_failed": {"running", "action_required", "stale"},
}
ATTEMPT_TRANSITIONS = {
    "pending": {"running", "action_required", "stale"},
    "running": {"partial", "succeeded", "retryable_failed", "action_required", "stale"},
    "partial": {"partial", "succeeded", "retryable_failed", "action_required", "stale"},
    "retryable_failed": {"running", "action_required", "stale"},
}


class AIReviewRepositoryError(RuntimeError):
    pass


class AIReviewCollision(AIReviewRepositoryError):
    pass


class AIReviewStateConflict(AIReviewRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class AIReviewHistory:
    run: AIReviewRun
    attempts: tuple[AIReviewAttempt, ...]
    events: tuple[AIReviewEventReceipt, ...]
    suggestions: tuple[AICriterionSuggestion, ...]
    signals: tuple[AISignal, ...]


class AIReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_run(
        self, organization_id: UUID, run_id: UUID, *, for_update: bool = False
    ) -> AIReviewRun | None:
        statement = select(AIReviewRun).where(
            AIReviewRun.organization_id == organization_id,
            AIReviewRun.id == run_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_fingerprint(
        self, organization_id: UUID, input_fingerprint: str, *, for_update: bool = False
    ) -> AIReviewRun | None:
        statement = select(AIReviewRun).where(
            AIReviewRun.organization_id == organization_id,
            AIReviewRun.input_fingerprint == input_fingerprint,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def create_or_get_run(self, candidate: AIReviewRun) -> tuple[AIReviewRun, bool]:
        existing = await self.get_by_fingerprint(
            candidate.organization_id, candidate.input_fingerprint
        )
        if existing is not None:
            self._require_same_provenance(existing, candidate)
            return existing, False
        try:
            async with self._session.begin_nested():
                self._session.add(candidate)
                await self._session.flush([candidate])
        except IntegrityError:
            existing = await self.get_by_fingerprint(
                candidate.organization_id,
                candidate.input_fingerprint,
                for_update=True,
            )
            if existing is None:
                raise
            self._require_same_provenance(existing, candidate)
            return existing, False
        return candidate, True

    async def add_attempt(self, attempt: AIReviewAttempt) -> AIReviewAttempt:
        self._session.add(attempt)
        await self._session.flush([attempt])
        return attempt

    async def get_attempt(
        self, organization_id: UUID, attempt_id: UUID, *, for_update: bool = False
    ) -> AIReviewAttempt | None:
        statement = select(AIReviewAttempt).where(
            AIReviewAttempt.organization_id == organization_id,
            AIReviewAttempt.id == attempt_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def compare_and_set_current_attempt(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        expected_revision: int,
        expected_current_attempt_no: int,
        new_attempt_no: int,
    ) -> bool:
        if new_attempt_no != expected_current_attempt_no + 1:
            raise AIReviewStateConflict("new attempt number must increment current attempt")
        result = await self._session.execute(
            update(AIReviewRun)
            .where(
                AIReviewRun.organization_id == organization_id,
                AIReviewRun.id == run_id,
                AIReviewRun.revision == expected_revision,
                AIReviewRun.current_attempt_no == expected_current_attempt_no,
                AIReviewRun.status.not_in(RUN_TERMINAL),
            )
            .values(
                current_attempt_no=new_attempt_no,
                status="running",
                revision=expected_revision + 1,
            )
        )
        return result.rowcount == 1

    async def reserve_event(
        self, candidate: AIReviewEventReceipt
    ) -> tuple[AIReviewEventReceipt, bool]:
        existing = await self._session.get(AIReviewEventReceipt, candidate.event_id)
        if existing is not None:
            self._require_same_event(existing, candidate)
            return existing, False
        try:
            async with self._session.begin_nested():
                self._session.add(candidate)
                await self._session.flush([candidate])
        except IntegrityError:
            existing = await self._session.get(
                AIReviewEventReceipt,
                candidate.event_id,
                populate_existing=True,
            )
            if existing is None:
                raise
            self._require_same_event(existing, candidate)
            return existing, False
        return candidate, True

    async def append_outputs(
        self,
        suggestions: Sequence[AICriterionSuggestion],
        signal: AISignal | None,
    ) -> None:
        self._session.add_all([*suggestions, *([signal] if signal is not None else [])])
        await self._session.flush()

    async def transition_attempt(
        self,
        organization_id: UUID,
        attempt_id: UUID,
        *,
        expected_status: str,
        expected_last_sequence: int,
        new_status: str,
        new_sequence: int,
        error: Mapping[str, Any] | None = None,
    ) -> bool:
        self._require_transition(
            expected_status, new_status, ATTEMPT_TRANSITIONS, terminal=ATTEMPT_TERMINAL
        )
        if new_sequence <= expected_last_sequence:
            raise AIReviewStateConflict("attempt event sequence must increase monotonically")
        sanitized = sanitize_error(error) if error is not None else None
        result = await self._session.execute(
            update(AIReviewAttempt)
            .where(
                AIReviewAttempt.organization_id == organization_id,
                AIReviewAttempt.id == attempt_id,
                AIReviewAttempt.status == expected_status,
                AIReviewAttempt.last_sequence == expected_last_sequence,
            )
            .values(
                status=new_status,
                last_sequence=new_sequence,
                finished_at=(
                    utc_now() if new_status in ATTEMPT_TERMINAL | {"retryable_failed"} else None
                ),
                error_code=(str(sanitized.get("code")) if sanitized is not None else None),
                sanitized_error=sanitized,
            )
        )
        return result.rowcount == 1

    async def transition_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        expected_revision: int,
        expected_status: str,
        current_attempt_no: int,
        new_status: str,
    ) -> bool:
        self._require_transition(
            expected_status, new_status, RUN_TRANSITIONS, terminal=RUN_TERMINAL
        )
        result = await self._session.execute(
            update(AIReviewRun)
            .where(
                AIReviewRun.organization_id == organization_id,
                AIReviewRun.id == run_id,
                AIReviewRun.revision == expected_revision,
                AIReviewRun.status == expected_status,
                AIReviewRun.current_attempt_no == current_attempt_no,
            )
            .values(
                status=new_status,
                revision=expected_revision + 1,
                finished_at=(utc_now() if new_status in RUN_TERMINAL else None),
            )
        )
        return result.rowcount == 1

    async def history(self, organization_id: UUID, run_id: UUID) -> AIReviewHistory | None:
        run = await self.get_run(organization_id, run_id)
        if run is None:
            return None
        attempts = (
            (
                await self._session.execute(
                    select(AIReviewAttempt)
                    .where(
                        AIReviewAttempt.organization_id == organization_id,
                        AIReviewAttempt.ai_review_run_id == run_id,
                    )
                    .order_by(AIReviewAttempt.attempt_number, AIReviewAttempt.id)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await self._session.execute(
                    select(AIReviewEventReceipt)
                    .where(
                        AIReviewEventReceipt.organization_id == organization_id,
                        AIReviewEventReceipt.ai_review_run_id == run_id,
                    )
                    .order_by(
                        AIReviewEventReceipt.attempt_number,
                        AIReviewEventReceipt.sequence,
                        AIReviewEventReceipt.event_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        suggestions = (
            (
                await self._session.execute(
                    select(AICriterionSuggestion)
                    .join(
                        AIReviewEventReceipt,
                        (
                            AIReviewEventReceipt.organization_id
                            == AICriterionSuggestion.organization_id
                        )
                        & (AIReviewEventReceipt.event_id == AICriterionSuggestion.event_id),
                    )
                    .where(
                        AICriterionSuggestion.organization_id == organization_id,
                        AICriterionSuggestion.ai_review_run_id == run_id,
                    )
                    .order_by(
                        AIReviewEventReceipt.attempt_number,
                        AIReviewEventReceipt.sequence,
                        AICriterionSuggestion.criterion_id,
                        AICriterionSuggestion.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        signals = (
            (
                await self._session.execute(
                    select(AISignal)
                    .join(
                        AIReviewEventReceipt,
                        (AIReviewEventReceipt.organization_id == AISignal.organization_id)
                        & (AIReviewEventReceipt.event_id == AISignal.event_id),
                    )
                    .where(
                        AISignal.organization_id == organization_id,
                        AISignal.ai_review_run_id == run_id,
                    )
                    .order_by(
                        AIReviewEventReceipt.attempt_number,
                        AIReviewEventReceipt.sequence,
                        AISignal.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return AIReviewHistory(
            run, tuple(attempts), tuple(events), tuple(suggestions), tuple(signals)
        )

    @staticmethod
    def _require_same_provenance(stored: AIReviewRun, candidate: AIReviewRun) -> None:
        fields = (
            "review_iteration_id",
            "course_run_id",
            "submission_version_id",
            "artifact_version_id",
            "content_digest",
            "homework_version_id",
            "homework_digest",
            "criterion_set_id",
            "criteria_digest",
            "contract_version",
            "fingerprint_algorithm",
        )
        if any(getattr(stored, field) != getattr(candidate, field) for field in fields):
            raise AIReviewCollision("input fingerprint is bound to different immutable provenance")

    @staticmethod
    def _require_same_event(stored: AIReviewEventReceipt, candidate: AIReviewEventReceipt) -> None:
        fields = (
            "organization_id",
            "ai_review_run_id",
            "attempt_id",
            "attempt_number",
            "sequence",
            "payload_digest",
            "status",
            "is_final",
        )
        if any(getattr(stored, field) != getattr(candidate, field) for field in fields):
            raise AIReviewCollision("global event ID is bound to a different payload or attempt")

    @staticmethod
    def _require_transition(
        current: str, new: str, allowed: Mapping[str, set[str]], *, terminal: frozenset[str]
    ) -> None:
        if current in terminal or new not in allowed.get(current, set()):
            raise AIReviewStateConflict(f"illegal or terminal state transition: {current} -> {new}")


__all__ = [
    "AIReviewCollision",
    "AIReviewHistory",
    "AIReviewRepository",
    "AIReviewRepositoryError",
    "AIReviewStateConflict",
]
