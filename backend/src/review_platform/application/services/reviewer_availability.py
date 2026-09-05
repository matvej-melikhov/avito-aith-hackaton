"""Free-form advisory reviewer availability updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc

_REVIEWER = AuthorizationPolicy(required_roles=frozenset({"reviewer"}))


class ReviewerAvailabilityError(RuntimeError):
    pass


class ReviewerAvailabilityConflict(ReviewerAvailabilityError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewerAvailabilityResult:
    organization_id: UUID
    membership_id: UUID
    reviewer_id: UUID
    membership_revision: int
    availability_plan_id: UUID
    planned_minutes: int
    until_at: datetime
    availability_revision: int


class ReviewerAvailabilityRepository(Protocol):
    async def lock_active_reviewer_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        reviewer_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> bool: ...

    async def upsert_plan(
        self,
        organization_id: UUID,
        reviewer_id: UUID,
        *,
        planned_minutes: int,
        until_at: datetime,
        transaction: object,
    ) -> tuple[UUID, int]: ...


class ReviewerAvailabilityService:
    def __init__(
        self,
        *,
        repository: ReviewerAvailabilityRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer
        self._audit = audit

    async def update(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        membership_id: UUID,
        expected_membership_revision: int,
        planned_minutes: int,
        until_at: datetime,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> ReviewerAvailabilityResult:
        grant = await self._authorizer.authorize(
            actor=actor, organization_id=organization_id, policy=_REVIEWER
        )
        if actor.user_id is None:
            raise ReviewerAvailabilityError("reviewer identity is required")
        if not isinstance(planned_minutes, int) or isinstance(planned_minutes, bool):
            raise ReviewerAvailabilityError("planned_minutes must be an integer")
        if planned_minutes < 0:
            raise ReviewerAvailabilityError("planned_minutes must be non-negative")
        until = require_utc(until_at)
        if not await self._repository.lock_active_reviewer_membership(
            organization_id,
            membership_id,
            actor.user_id,
            expected_revision=expected_membership_revision,
            transaction=transaction,
        ):
            raise ReviewerAvailabilityConflict("reviewer membership is missing, inactive, or stale")
        plan_id, plan_revision = await self._repository.upsert_plan(
            organization_id,
            actor.user_id,
            planned_minutes=planned_minutes,
            until_at=until,
            transaction=transaction,
        )
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="set_reviewer_availability",
                entity_type="availability_plan",
                entity_id=plan_id,
                before_revision=max(plan_revision - 1, 0),
                after_revision=plan_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"planned_minutes": planned_minutes, "until_at": until.isoformat()},
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return ReviewerAvailabilityResult(
            organization_id,
            membership_id,
            actor.user_id,
            expected_membership_revision,
            plan_id,
            planned_minutes,
            until,
            plan_revision,
        )


__all__ = [
    "ReviewerAvailabilityConflict",
    "ReviewerAvailabilityError",
    "ReviewerAvailabilityRepository",
    "ReviewerAvailabilityResult",
    "ReviewerAvailabilityService",
]
