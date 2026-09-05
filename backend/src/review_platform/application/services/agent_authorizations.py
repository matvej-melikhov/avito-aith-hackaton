"""Interactive lifecycle for scoped, expiring AgentAuthorization grants."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.authorization import (
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.idempotency import (
    IdempotencyCoordinator,
    IdempotencyError,
    IdempotencyReceiptRepository,
    IdempotencyReservation,
)
from review_platform.application.request_context import (
    KNOWN_AGENT_SCOPES,
    KNOWN_ROLES,
    AgentScope,
    RequestActor,
    Role,
)
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.auth.agent_tokens import (
    AgentTokenError,
    AgentTokenSecret,
    IssuedAgentToken,
    issue_agent_token,
)
from review_platform.infrastructure.db.repositories.agents import (
    AgentAuthorizationRecord,
    AgentAuthorizationRepositoryError,
)

DEFAULT_MAX_AGENT_AUTHORIZATION_TTL = timedelta(days=30)

_MANAGE_AGENT_AUTHORIZATION = AuthorizationPolicy(
    required_roles=frozenset({"reviewer"}),
    allowed_actor_types=frozenset({"user"}),
)
_AGENT_SCOPE_ORDER: tuple[AgentScope, ...] = (
    "courses:read",
    "review_preferences:write",
    "review_queue:read",
    "reviews:read",
    "reviews:write",
    "ai_reviews:start",
    "operations:read",
    "publication_requests:write",
)
_ROLE_SCOPES: dict[Role, frozenset[AgentScope]] = {
    "reviewer": frozenset(_AGENT_SCOPE_ORDER),
    "methodologist": frozenset(
        {
            "courses:read",
            "reviews:read",
            "reviews:write",
            "ai_reviews:start",
            "operations:read",
            "publication_requests:write",
        }
    ),
    "student": frozenset(),
}


class AgentAuthorizationServiceError(RuntimeError):
    """Base typed failure for interactive agent-authorization management."""


class AgentAuthorizationPermissionDenied(AgentAuthorizationServiceError):
    """The interactive actor does not own the requested authority."""


class AgentAuthorizationNotFound(AgentAuthorizationServiceError):
    """The tenant-scoped membership or authorization does not exist."""


class AgentAuthorizationConflict(AgentAuthorizationServiceError):
    """A CAS, identity, or idempotency conflict prevented the mutation."""


class InvalidAgentAuthorizationGrant(AgentAuthorizationServiceError):
    """Grant scopes or expiry are outside the closed policy."""


@dataclass(frozen=True, slots=True)
class MembershipAuthority:
    organization_id: UUID
    membership_id: UUID
    user_id: UUID
    roles: frozenset[Role]
    status: str
    revision: int
    auth_epoch: int


@dataclass(frozen=True, slots=True)
class AgentAuthorizationView:
    authorization_id: UUID
    organization_id: UUID
    user_id: UUID
    agent_id: UUID
    scopes: tuple[AgentScope, ...]
    expires_at: datetime
    revision: int
    status: str


@dataclass(frozen=True, slots=True)
class AgentAuthorizationGrantResult:
    authorization: AgentAuthorizationView
    access_token: AgentTokenSecret | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class AgentAuthorizationRevokeResult:
    authorization_id: UUID
    revision: int
    invalidated_receipts: int
    replayed: bool


class MembershipAuthorityRepository(Protocol):
    """Lock one exact tenant membership in the caller-owned transaction."""

    async def lock_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        transaction: object,
    ) -> MembershipAuthority | None: ...


class AgentAuthorizationStore(Protocol):
    """T151-compatible digest-only authorization persistence port."""

    async def reserve(
        self,
        candidate: AgentAuthorizationRecord,
        *,
        transaction: object,
    ) -> tuple[AgentAuthorizationRecord, bool]: ...

    async def lock_by_id(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        transaction: object,
    ) -> AgentAuthorizationRecord | None: ...

    async def revoke_linked(
        self,
        organization_id: UUID,
        user_id: UUID,
        authorization_id: UUID,
        *,
        expected_revision: int,
        expected_membership_revision: int,
        expected_auth_epoch: int,
        revoked_at: datetime,
        transaction: object,
    ) -> bool: ...

    async def invalidate_pending_receipts(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        authorization_revision: int | None = None,
        transaction: object,
    ) -> int: ...


class AgentAuthorizationService:
    """Grant and revoke authority without opening or committing transactions."""

    def __init__(
        self,
        *,
        membership_repository: MembershipAuthorityRepository,
        authorization_repository: AgentAuthorizationStore,
        receipt_repository: IdempotencyReceiptRepository,
        authorizer: Authorizer,
        audit: AuditRecorder,
        max_ttl: timedelta = DEFAULT_MAX_AGENT_AUTHORIZATION_TTL,
        token_issuer: Callable[[], IssuedAgentToken] = issue_agent_token,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if max_ttl <= timedelta(0):
            raise ValueError("agent authorization maximum TTL must be positive")
        self._membership_repository = membership_repository
        self._authorization_repository = authorization_repository
        self._receipt_repository = receipt_repository
        self._authorizer = authorizer
        self._audit = audit
        self._max_ttl = max_ttl
        self._token_issuer = token_issuer
        self._id_factory = id_factory
        self._clock = clock

    async def grant(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        membership_id: UUID,
        expected_membership_revision: int,
        agent_id: UUID,
        scopes: Iterable[AgentScope],
        expires_at: datetime,
        idempotency_key: str,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> AgentAuthorizationGrantResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_MANAGE_AGENT_AUTHORIZATION,
        )
        now = require_utc(self._clock())
        expiry = self._validate_expiry(expires_at, now=now)
        requested_scopes = self._validate_scopes(scopes)
        membership = await self._membership_repository.lock_membership(
            organization_id,
            membership_id,
            transaction=transaction,
        )
        self._validate_membership(
            membership,
            actor=actor,
            organization_id=organization_id,
            membership_id=membership_id,
            expected_revision=expected_membership_revision,
        )
        assert membership is not None
        self._require_role_scope_intersection(requested_scopes, membership.roles)

        authorization_id = self._id_factory()
        reservation = await self._reserve_command(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            command_name="grant_agent_authorization",
            target_id=membership_id,
            expected_revision=expected_membership_revision,
            payload={
                "actor_user_id": actor.user_id,
                "agent_id": agent_id,
                "scopes": list(requested_scopes),
                "expires_at": expiry,
            },
            result_reference={
                "kind": "agent_authorization",
                "authorization_id": str(authorization_id),
                "revision": 0,
            },
            transaction=transaction,
        )
        if reservation.disposition == "replay":
            stored_authorization_id = self._validate_result_reference(
                reservation,
                kind="agent_authorization",
                authorization_id=None,
                revision=0,
            )
            stored = await self._authorization_repository.lock_by_id(
                organization_id,
                stored_authorization_id,
                transaction=transaction,
            )
            self._validate_replayed_authorization(
                stored,
                membership=membership,
                agent_id=agent_id,
                scopes=requested_scopes,
                expires_at=expiry,
            )
            assert stored is not None
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return AgentAuthorizationGrantResult(
                authorization=self._view(stored),
                access_token=None,
                replayed=True,
            )
        self._validate_result_reference(
            reservation,
            kind="agent_authorization",
            authorization_id=authorization_id,
            revision=0,
        )

        try:
            issued = self._token_issuer()
        except AgentTokenError as error:
            raise AgentAuthorizationServiceError("agent token issuance failed") from error
        candidate = AgentAuthorizationRecord(
            authorization_id=authorization_id,
            organization_id=organization_id,
            user_id=membership.user_id,
            agent_id=agent_id,
            scopes=tuple(requested_scopes),
            membership_revision=membership.revision,
            auth_epoch=membership.auth_epoch,
            token_digest=issued.token_digest,
            status="active",
            expires_at=expiry,
            revision=0,
        )
        try:
            stored, created = await self._authorization_repository.reserve(
                candidate,
                transaction=transaction,
            )
        except AgentAuthorizationRepositoryError as error:
            raise AgentAuthorizationConflict(str(error)) from error
        if not created:
            raise AgentAuthorizationConflict(
                "agent authorization token digest was already persisted"
            )
        self._validate_stored_authorization(stored, candidate)
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="grant_agent_authorization",
                entity_type="agent_authorization",
                entity_id=authorization_id,
                before_revision=None,
                after_revision=0,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "agent_id": str(agent_id),
                    "scopes": list(requested_scopes),
                    "expires_at": expiry.isoformat(),
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return AgentAuthorizationGrantResult(
            authorization=self._view(stored),
            access_token=issued.access_token,
            replayed=False,
        )

    async def revoke(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        authorization_id: UUID,
        expected_authorization_revision: int,
        reason: str,
        idempotency_key: str,
        actor: RequestActor,
        request_id: UUID,
        trace_id: UUID,
    ) -> AgentAuthorizationRevokeResult:
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_MANAGE_AGENT_AUTHORIZATION,
        )
        if actor.user_id is None or actor.membership_revision is None or actor.auth_epoch is None:
            raise AgentAuthorizationPermissionDenied(
                "interactive represented user authority is required"
            )
        if expected_authorization_revision < 0:
            raise AgentAuthorizationConflict(
                "expected AgentAuthorization revision must be nonnegative"
            )
        normalized_reason = reason.strip()
        if not 1 <= len(normalized_reason) <= 2048:
            raise InvalidAgentAuthorizationGrant(
                "revocation reason must contain 1 to 2048 characters"
            )
        result_revision = expected_authorization_revision + 1
        reservation = await self._reserve_command(
            organization_id=organization_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            command_name="revoke_agent_authorization",
            target_id=authorization_id,
            expected_revision=expected_authorization_revision,
            payload={
                "actor_user_id": actor.user_id,
                "reason": normalized_reason,
            },
            result_reference={
                "kind": "agent_authorization_revocation",
                "authorization_id": str(authorization_id),
                "revision": result_revision,
            },
            transaction=transaction,
        )
        self._validate_result_reference(
            reservation,
            kind="agent_authorization_revocation",
            authorization_id=authorization_id,
            revision=result_revision,
        )
        if reservation.disposition == "replay":
            await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
            return AgentAuthorizationRevokeResult(
                authorization_id=authorization_id,
                revision=result_revision,
                invalidated_receipts=0,
                replayed=True,
            )

        try:
            revoked = await self._authorization_repository.revoke_linked(
                organization_id,
                actor.user_id,
                authorization_id,
                expected_revision=expected_authorization_revision,
                expected_membership_revision=actor.membership_revision,
                expected_auth_epoch=actor.auth_epoch,
                revoked_at=require_utc(self._clock()),
                transaction=transaction,
            )
        except AgentAuthorizationRepositoryError as error:
            raise AgentAuthorizationConflict(str(error)) from error
        if not revoked:
            raise AgentAuthorizationConflict(
                "agent authorization is missing, not owned, inactive, or stale"
            )
        invalidated = await self._authorization_repository.invalidate_pending_receipts(
            organization_id,
            authorization_id,
            authorization_revision=expected_authorization_revision,
            transaction=transaction,
        )
        await self._audit.record(
            AuditEventDraft(
                organization_id=organization_id,
                actor=actor,
                action="revoke_agent_authorization",
                entity_type="agent_authorization",
                entity_id=authorization_id,
                before_revision=expected_authorization_revision,
                after_revision=result_revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "reason": normalized_reason,
                    "invalidated_receipts": invalidated,
                },
            ),
            transaction=transaction,
        )
        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return AgentAuthorizationRevokeResult(
            authorization_id=authorization_id,
            revision=result_revision,
            invalidated_receipts=invalidated,
            replayed=False,
        )

    def _validate_expiry(self, expires_at: datetime, *, now: datetime) -> datetime:
        expiry = require_utc(expires_at)
        if expiry <= now:
            raise InvalidAgentAuthorizationGrant("agent authorization expiry must be in the future")
        if expiry - now > self._max_ttl:
            raise InvalidAgentAuthorizationGrant(
                "agent authorization expiry exceeds the configured maximum TTL"
            )
        return expiry

    @staticmethod
    def _validate_scopes(scopes: Iterable[AgentScope]) -> tuple[AgentScope, ...]:
        provided = tuple(scopes)
        unknown = set(provided).difference(KNOWN_AGENT_SCOPES)
        if unknown:
            raise InvalidAgentAuthorizationGrant(
                f"unknown agent authorization scope(s): {sorted(unknown)!r}"
            )
        if not provided:
            raise InvalidAgentAuthorizationGrant(
                "agent authorization must contain at least one scope"
            )
        if len(set(provided)) != len(provided):
            raise InvalidAgentAuthorizationGrant("agent authorization scopes must be unique")
        selected = set(provided)
        return tuple(scope for scope in _AGENT_SCOPE_ORDER if scope in selected)

    @staticmethod
    def _require_role_scope_intersection(
        scopes: tuple[AgentScope, ...], roles: frozenset[Role]
    ) -> None:
        unknown_roles = set(roles).difference(KNOWN_ROLES)
        if unknown_roles:
            raise AgentAuthorizationConflict(
                f"membership contains unknown role(s): {sorted(unknown_roles)!r}"
            )
        allowed: set[AgentScope] = set()
        for role in roles:
            allowed.update(_ROLE_SCOPES[role])
        denied = set(scopes).difference(allowed)
        if denied:
            raise InvalidAgentAuthorizationGrant(
                f"scope(s) are not available to the represented roles: {sorted(denied)!r}"
            )

    @staticmethod
    def _validate_membership(
        membership: MembershipAuthority | None,
        *,
        actor: RequestActor,
        organization_id: UUID,
        membership_id: UUID,
        expected_revision: int,
    ) -> None:
        if membership is None:
            raise AgentAuthorizationNotFound("tenant membership was not found")
        if (
            membership.organization_id != organization_id
            or membership.membership_id != membership_id
        ):
            raise AgentAuthorizationConflict("repository returned another membership")
        if membership.status != "active":
            raise AgentAuthorizationPermissionDenied("membership is not active")
        if membership.user_id != actor.user_id:
            raise AgentAuthorizationPermissionDenied(
                "only the represented membership owner may grant agent authority"
            )
        if (
            membership.revision != expected_revision
            or membership.revision != actor.membership_revision
            or membership.auth_epoch != actor.auth_epoch
            or membership.roles != actor.roles
        ):
            raise AgentAuthorizationConflict("membership authority snapshot is stale")

    async def _reserve_command(
        self,
        *,
        organization_id: UUID,
        idempotency_key: str,
        request_id: UUID,
        command_name: str,
        target_id: UUID,
        expected_revision: int,
        payload: object,
        result_reference: dict[str, str | int],
        transaction: object,
    ) -> IdempotencyReservation:
        try:
            return await IdempotencyCoordinator(
                self._receipt_repository,
                receipt_id_factory=self._id_factory,
                result_reference_factory=lambda: result_reference,
            ).reserve(
                organization_id=organization_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
                command_name=command_name,
                target_id=target_id,
                expected_revision=expected_revision,
                payload=payload,
                transaction=transaction,
            )
        except IdempotencyError as error:
            raise AgentAuthorizationConflict(str(error)) from error

    @staticmethod
    def _validate_result_reference(
        reservation: IdempotencyReservation,
        *,
        kind: str,
        authorization_id: UUID | None,
        revision: int,
    ) -> UUID:
        reference = reservation.receipt.result_reference
        if reference.get("kind") != kind or reference.get("revision") != revision:
            raise AgentAuthorizationConflict(
                "idempotency receipt has an invalid authorization result reference"
            )
        raw_authorization_id = reference.get("authorization_id")
        if not isinstance(raw_authorization_id, str):
            raise AgentAuthorizationConflict("idempotency receipt has no authorization identity")
        try:
            stored_authorization_id = UUID(raw_authorization_id)
        except ValueError as error:
            raise AgentAuthorizationConflict(
                "idempotency receipt authorization identity is invalid"
            ) from error
        if authorization_id is not None and stored_authorization_id != authorization_id:
            raise AgentAuthorizationConflict("idempotency receipt references another authorization")
        return stored_authorization_id

    @staticmethod
    def _validate_stored_authorization(
        stored: AgentAuthorizationRecord,
        candidate: AgentAuthorizationRecord,
    ) -> None:
        if stored != candidate:
            raise AgentAuthorizationConflict(
                "authorization repository returned another persisted grant"
            )

    @staticmethod
    def _validate_replayed_authorization(
        stored: AgentAuthorizationRecord | None,
        *,
        membership: MembershipAuthority,
        agent_id: UUID,
        scopes: tuple[AgentScope, ...],
        expires_at: datetime,
    ) -> None:
        if stored is None:
            raise AgentAuthorizationConflict("idempotent grant references a missing authorization")
        if (
            stored.organization_id != membership.organization_id
            or stored.user_id != membership.user_id
            or stored.agent_id != agent_id
            or stored.scopes != scopes
            or stored.membership_revision != membership.revision
            or stored.auth_epoch != membership.auth_epoch
            or stored.expires_at != expires_at
            or stored.revision != 0
        ):
            raise AgentAuthorizationConflict(
                "idempotent grant references different authorization metadata"
            )

    @staticmethod
    def _view(record: AgentAuthorizationRecord) -> AgentAuthorizationView:
        scopes = AgentAuthorizationService._validate_scopes(
            cast(tuple[AgentScope, ...], record.scopes)
        )
        return AgentAuthorizationView(
            authorization_id=record.authorization_id,
            organization_id=record.organization_id,
            user_id=record.user_id,
            agent_id=record.agent_id,
            scopes=scopes,
            expires_at=record.expires_at,
            revision=record.revision,
            status=record.status,
        )


__all__ = [
    "DEFAULT_MAX_AGENT_AUTHORIZATION_TTL",
    "AgentAuthorizationConflict",
    "AgentAuthorizationGrantResult",
    "AgentAuthorizationNotFound",
    "AgentAuthorizationPermissionDenied",
    "AgentAuthorizationRevokeResult",
    "AgentAuthorizationService",
    "AgentAuthorizationServiceError",
    "AgentAuthorizationStore",
    "AgentAuthorizationView",
    "InvalidAgentAuthorizationGrant",
    "MembershipAuthority",
    "MembershipAuthorityRepository",
]
