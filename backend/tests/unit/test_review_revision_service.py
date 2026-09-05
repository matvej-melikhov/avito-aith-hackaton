from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest

from review_platform.application.request_context import RequestActor
from review_platform.application.services.review_drafts import (
    ReviewDecisionInput,
    ReviewDraftConflict,
    ReviewDraftService,
    ReviewNoteInput,
)
from review_platform.application.services.review_revisions import (
    ReviewRevisionService,
    SaveReviewRevisionCommand,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionRecord,
    ReviewNoteRecord,
    ReviewRevisionRecord,
    ReviewRevisionRepositoryError,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000001901")
ITERATION = UUID("00000000-0000-7000-8000-000000001902")
CURRENT = UUID("00000000-0000-7000-8000-000000001903")
CRITERION = UUID("00000000-0000-7000-8000-000000001904")
REVISION = UUID("00000000-0000-7000-8000-000000001905")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class DraftDelegate:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.conflict = False

    async def save(self, **values: Any) -> ReviewRevisionRecord:
        self.calls.append(values)
        if self.conflict:
            raise ReviewDraftConflict("expected current revision is stale")
        decisions = cast(tuple[ReviewDecisionInput, ...], values["decisions"])
        if any(item.points > Decimal("10") for item in decisions):
            raise ReviewRevisionRepositoryError("points are outside criterion range")
        notes = cast(tuple[ReviewNoteInput, ...], values["notes"])
        total = sum((item.points for item in decisions), Decimal("0"))
        return ReviewRevisionRecord(
            organization_id=ORG,
            review_revision_id=REVISION,
            review_iteration_id=ITERATION,
            revision_number=2,
            author_user_id=USER,
            base_revision_id=CURRENT,
            feedback=cast(str, values["feedback"]),
            total_score=total,
            created_at=NOW,
            decisions=tuple(
                ReviewDecisionRecord(
                    UUID(int=20),
                    item.criterion_id,
                    "correctness",
                    1,
                    item.points,
                    item.decision,
                    item.reason,
                    item.evidence_ids,
                    item.ai_suggestion_id,
                )
                for item in decisions
            ),
            notes=tuple(
                ReviewNoteRecord(
                    UUID(int=30 + index),
                    item.criterion_id,
                    item.text,
                    USER,
                    index,
                )
                for index, item in enumerate(notes)
            ),
        )


def _actor() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )


def _command(points: str = "7.25") -> SaveReviewRevisionCommand:
    return SaveReviewRevisionCommand(
        organization_id=ORG,
        review_iteration_id=ITERATION,
        expected_iteration_revision=4,
        expected_current_revision_id=CURRENT,
        feedback="Human feedback",
        decisions=(
            ReviewDecisionInput(
                criterion_id=CRITERION,
                points=Decimal(points),
                decision="manual",
                reason="Human judgement",
                evidence_ids=("artifact:line:1",),
            ),
        ),
        notes=(ReviewNoteInput("Global note"),),
        request_id=UUID(int=1),
        trace_id=UUID(int=2),
    )


@pytest.mark.anyio
async def test_command_facade_preserves_decimal_notes_and_expected_current_cas() -> None:
    delegate = DraftDelegate()
    service = ReviewRevisionService(cast(ReviewDraftService, delegate))
    result = await service.save(_command(), actor=_actor(), transaction=object())

    assert result.review_revision_id == REVISION
    assert result.review_iteration_revision == 5
    assert result.total_score == Decimal("7.25")
    assert delegate.calls[0]["expected_current_revision_id"] == CURRENT
    assert delegate.calls[0]["notes"] == (ReviewNoteInput("Global note"),)


@pytest.mark.anyio
async def test_typed_repository_bounds_and_stale_conflict_propagate_without_duplicate_logic() -> (
    None
):
    delegate = DraftDelegate()
    service = ReviewRevisionService(cast(ReviewDraftService, delegate))
    with pytest.raises(ReviewRevisionRepositoryError, match="points"):
        await service.save(_command("10.01"), actor=_actor(), transaction=object())

    delegate.conflict = True
    with pytest.raises(ReviewDraftConflict, match="stale"):
        await service.save(_command(), actor=_actor(), transaction=object())
