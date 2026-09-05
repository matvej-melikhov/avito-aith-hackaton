"""Append-only, non-exclusive human review participation events."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, utc_now, uuid7

type ResponsibilityAction = Literal["started", "joined", "released", "completed"]

_PARTICIPANT = AuthorizationPolicy(required_roles=frozenset({"reviewer", "methodologist"}))
_ACTIONS = frozenset[ResponsibilityAction]({"started", "joined", "released", "completed"})


class ReviewResponsibilityError(RuntimeError):
    pass


class ReviewResponsibilityNotFound(ReviewResponsibilityError):
    pass


class ReviewResponsibilityConflict(ReviewResponsibilityError):
    pass


@dataclass(frozen=True, slots=True)
class ResponsibilityContext:
    organization_id: UUID
    review_case_id: UUID
    review_iteration_id: UUID | None
    review_iteration_revision: int | None


@dataclass(frozen=True, slots=True)
class ReviewResponsibilityEvent:
    event_id: UUID
    organization_id: UUID
    review_case_id: UUID
    review_iteration_id: UUID | None
    reviewer_id: UUID
    actor_id: UUID
    action: ResponsibilityAction
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewResponsibilityResult:
    event_id: UUID
    review_case_id: UUID
    review_iteration_id: UUID | None
    reviewer_id: UUID
    action: ResponsibilityAction
    occurred_at: datetime


class ReviewResponsibilityRepository(Protocol):
    """T128 port; implementations must not take exclusive ownership locks."""

    async def resolve_context(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        review_iteration_id: UUID | None,
        *,
        expected_review_iteration_revision: int | None,
        transaction: object,
    ) -> ResponsibilityContext | None: ...

    async def append(
        self,
        event: ReviewResponsibilityEvent,
        *,
        expected_review_iteration_revision: int | None,
        transaction: object,
    ) -> bool: ...


class ReviewResponsibilityService:
    def __init__(
        self,
        *,
        repository: ReviewResponsibilityRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def record(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        review_case_id: UUID,
        review_iteration_id: UUID | None,
        expected_review_iteration_revision: int | None,
        action: ResponsibilityAction,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> ReviewResponsibilityResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_PARTICIPANT,
        )
        if actor.user_id is None:
            raise ReviewResponsibilityError("human participant identity is required")
        if action not in _ACTIONS:
            raise ReviewResponsibilityError(f"unknown responsibility action: {action!r}")
        context = await self._repository.resolve_context(
            organization_id,
            review_case_id,
            review_iteration_id,
            expected_review_iteration_revision=expected_review_iteration_revision,
            transaction=transaction,
        )
        if context is None:
            raise ReviewResponsibilityNotFound(
                "tenant ReviewCase or exact optional ReviewIteration was not found"
            )
        if (
            context.organization_id != organization_id
            or context.review_case_id != review_case_id
            or context.review_iteration_id != review_iteration_id
            or context.review_iteration_revision != expected_review_iteration_revision
        ):
            raise ReviewResponsibilityConflict(
                "repository returned a different case/iteration affinity"
            )
        occurred_at = require_utc(self._clock())
        event = ReviewResponsibilityEvent(
            event_id=self._id_factory(),
            organization_id=organization_id,
            review_case_id=review_case_id,
            review_iteration_id=review_iteration_id,
            reviewer_id=actor.user_id,
            actor_id=actor.user_id,
            action=action,
            occurred_at=occurred_at,
        )
        if not await self._repository.append(
            event,
            expected_review_iteration_revision=expected_review_iteration_revision,
            transaction=transaction,
        ):
            raise ReviewResponsibilityConflict(
                "ReviewIteration revision changed before responsibility append"
            )
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="record_review_responsibility",
                entity_type="review_responsibility",
                entity_id=event.event_id,
                before_revision=None,
                after_revision=None,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "review_case_id": str(review_case_id),
                    "review_iteration_id": (
                        str(review_iteration_id) if review_iteration_id is not None else None
                    ),
                    "responsibility_action": action,
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return ReviewResponsibilityResult(
            event.event_id,
            event.review_case_id,
            event.review_iteration_id,
            event.reviewer_id,
            event.action,
            event.occurred_at,
        )


__all__ = [
    "ResponsibilityAction",
    "ResponsibilityContext",
    "ReviewResponsibilityConflict",
    "ReviewResponsibilityError",
    "ReviewResponsibilityEvent",
    "ReviewResponsibilityNotFound",
    "ReviewResponsibilityRepository",
    "ReviewResponsibilityResult",
    "ReviewResponsibilityService",
]
