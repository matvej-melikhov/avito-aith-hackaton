"""Harmless idempotent requests to publish one exact current human revision."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, utc_now, uuid7

_REQUEST_PUBLICATION = AuthorizationPolicy(
    required_roles=frozenset({"reviewer", "methodologist"}),
    required_scopes=frozenset({"publication_requests:write"}),
)


class PublicationRequestError(RuntimeError):
    pass


class PublicationRequestNotFound(PublicationRequestError):
    pass


class PublicationRequestConflict(PublicationRequestError):
    pass


@dataclass(frozen=True, slots=True)
class PublicationRequestContext:
    organization_id: UUID
    review_iteration_id: UUID
    iteration_revision: int
    current_review_revision_id: UUID | None


@dataclass(frozen=True, slots=True)
class PublicationRequestRecord:
    organization_id: UUID
    publication_request_id: UUID
    review_iteration_id: UUID
    review_revision_id: UUID
    requested_by_user_id: UUID
    agent_id: UUID | None
    agent_authorization_id: UUID | None
    idempotency_key: str
    status: str
    expires_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class PublicationRequestResult:
    request: PublicationRequestRecord
    replayed: bool


class PublicationRequestRepository(Protocol):
    """T129 port: tenant lock plus atomic insert-or-return by idempotency key."""

    async def lock_iteration_context(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> PublicationRequestContext | None: ...

    async def reserve(
        self, candidate: PublicationRequestRecord, *, transaction: object
    ) -> tuple[PublicationRequestRecord, bool]: ...


class PublicationRequestService:
    def __init__(
        self,
        *,
        repository: PublicationRequestRepository,
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

    async def request(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        review_iteration_id: UUID,
        expected_iteration_revision: int,
        review_revision_id: UUID,
        expires_at: datetime,
        idempotency_key: str,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> PublicationRequestResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_REQUEST_PUBLICATION,
        )
        if actor.user_id is None:
            raise PublicationRequestError("represented human identity is required")
        if not 16 <= len(idempotency_key) <= 128:
            raise PublicationRequestError("idempotency key must contain 16 to 128 characters")
        expiry = require_utc(expires_at)
        if expiry <= require_utc(self._clock()):
            raise PublicationRequestError("publication request expiry must be in the future")
        context = await self._repository.lock_iteration_context(
            organization_id,
            review_iteration_id,
            expected_revision=expected_iteration_revision,
            transaction=transaction,
        )
        if context is None:
            raise PublicationRequestNotFound(
                "tenant ReviewIteration is missing or its revision is stale"
            )
        if (
            context.organization_id != organization_id
            or context.review_iteration_id != review_iteration_id
            or context.iteration_revision != expected_iteration_revision
        ):
            raise PublicationRequestConflict("repository returned another iteration context")
        if context.current_review_revision_id != review_revision_id:
            raise PublicationRequestConflict(
                "publication request must reference exact current ReviewRevision"
            )
        candidate = PublicationRequestRecord(
            organization_id=organization_id,
            publication_request_id=self._id_factory(),
            review_iteration_id=review_iteration_id,
            review_revision_id=review_revision_id,
            requested_by_user_id=actor.user_id,
            agent_id=actor.agent_id,
            agent_authorization_id=actor.agent_authorization_id,
            idempotency_key=idempotency_key,
            status="pending",
            expires_at=expiry,
            revision=0,
        )
        stored, created = await self._repository.reserve(candidate, transaction=transaction)
        self._require_same_request(stored, candidate)
        if created:
            await self._audit.record(
                AuditEventDraft(
                    organization_id=organization_id,
                    actor=actor,
                    action="request_review_publication",
                    entity_type="publication_request",
                    entity_id=stored.publication_request_id,
                    before_revision=None,
                    after_revision=0,
                    request_id=request_id,
                    trace_id=trace_id,
                    outcome="succeeded",
                    details={"review_revision_id": str(review_revision_id)},
                ),
                transaction=transaction,
            )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return PublicationRequestResult(stored, replayed=not created)

    @staticmethod
    def _require_same_request(
        stored: PublicationRequestRecord,
        candidate: PublicationRequestRecord,
    ) -> None:
        identity = (
            "organization_id",
            "review_iteration_id",
            "review_revision_id",
            "requested_by_user_id",
            "agent_id",
            "agent_authorization_id",
            "idempotency_key",
            "expires_at",
        )
        if any(getattr(stored, field) != getattr(candidate, field) for field in identity):
            raise PublicationRequestConflict(
                "idempotency key is bound to a different publication request payload"
            )


__all__ = [
    "PublicationRequestConflict",
    "PublicationRequestContext",
    "PublicationRequestError",
    "PublicationRequestNotFound",
    "PublicationRequestRecord",
    "PublicationRequestRepository",
    "PublicationRequestResult",
    "PublicationRequestService",
]
