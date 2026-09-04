"""Atomic invitation issue, verified consumption, and revocation services."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, sha256_digest, utc_now, uuid7
from review_platform.infrastructure.db.models.identity import Invitation
from review_platform.infrastructure.db.repositories.identity import InvitationRepository

type InvitationRole = Literal["methodologist", "reviewer"]


class InvitationServiceError(RuntimeError):
    """Base class for typed invitation failures."""


class InvitationPermissionDenied(InvitationServiceError):
    """The actor cannot manage this tenant's invitations."""


class InvalidInvitation(InvitationServiceError):
    """Invitation input or persistent state violates the contract."""


class InvitationNotFound(InvitationServiceError):
    """The tenant-scoped invitation does not exist."""


class InvitationConflict(InvitationServiceError):
    """A one-time invitation transition lost its CAS race."""


class VerifiedIdentityMismatch(InvitationServiceError):
    """The short-lived verified identity does not match the invitation target."""


@dataclass(frozen=True, slots=True)
class InvitationEmailIntent:
    organization_id: UUID
    message_id: UUID
    invitation_id: UUID
    recipient: str
    sealed_magic_link: str
    expires_at: datetime


class InvitationEmailIntentPort(Protocol):
    """Persist a durable invitation-email intent in the caller's transaction."""

    async def enqueue(self, intent: InvitationEmailIntent, *, transaction: object) -> None: ...


class InvitationSecretProtector(Protocol):
    """Seal a magic link before it reaches a durable/shared sink."""

    def seal(self, magic_link: str) -> str: ...


@dataclass(frozen=True, slots=True)
class VerifiedEmailIdentity:
    organization_id: UUID
    user_id: UUID
    provider: Literal["email_magic_link"]
    issuer: str
    subject: str
    verified_email: str
    issued_at: datetime
    expires_at: datetime
    state_id: UUID


@dataclass(frozen=True, slots=True)
class InvitationView:
    invitation_id: UUID
    organization_id: UUID
    normalized_email: str
    role: InvitationRole
    status: str
    revision: int
    expires_at: datetime


class InvitationService:
    """Operate on Invitation rows without opening or committing transactions."""

    def __init__(
        self,
        *,
        email_intents: InvitationEmailIntentPort,
        secret_protector: InvitationSecretProtector,
        audit: AuditRecorder,
        magic_link_base_url: str,
        id_factory: Callable[[], UUID] = uuid7,
        token_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not magic_link_base_url.startswith(("https://", "http://localhost")):
            raise InvalidInvitation("magic-link base URL must use HTTPS or localhost")
        self._email_intents = email_intents
        self._secret_protector = secret_protector
        self._audit = audit
        self._magic_link_base_url = magic_link_base_url
        self._id_factory = id_factory
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._clock = clock

    async def issue(
        self,
        *,
        transaction: AsyncSession,
        actor: RequestActor,
        email: str,
        role: InvitationRole,
        expires_at: datetime,
        request_id: UUID,
        trace_id: UUID,
    ) -> InvitationView:
        self._require_methodologist(actor)
        now = require_utc(self._clock())
        expiry = require_utc(expires_at)
        if expiry <= now:
            raise InvalidInvitation("invitation expiry must be in the future")
        normalized_email = normalize_email(email)
        if role not in {"methodologist", "reviewer"}:
            raise InvalidInvitation("invitation role must be methodologist or reviewer")
        if actor.user_id is None:
            raise InvitationPermissionDenied("invitation issuer user identity is required")

        invitation_id = self._id_factory()
        message_id = self._id_factory()
        token = self._token_factory()
        if len(token) < 32:
            raise InvalidInvitation("invitation token must contain at least 32 characters")
        token_digest = sha256_digest(token)
        invitation = Invitation(
            id=invitation_id,
            organization_id=actor.organization_id,
            role=role,
            normalized_email=normalized_email,
            token_digest=token_digest,
            expires_at=expiry,
            issued_by=actor.user_id,
            consumed_by=None,
            consumed_at=None,
            revoked_at=None,
            status="active",
            revision=0,
        )
        await InvitationRepository(transaction).add(invitation)

        query = urlencode({"invitation_id": invitation_id, "token": token})
        magic_link = f"{self._magic_link_base_url}?{query}"
        sealed_magic_link = self._secret_protector.seal(magic_link)
        if not sealed_magic_link or token in sealed_magic_link or sealed_magic_link == magic_link:
            raise InvalidInvitation("secret protector did not seal the invitation magic link")
        await self._email_intents.enqueue(
            InvitationEmailIntent(
                organization_id=actor.organization_id,
                message_id=message_id,
                invitation_id=invitation_id,
                recipient=normalized_email,
                sealed_magic_link=sealed_magic_link,
                expires_at=expiry,
            ),
            transaction=transaction,
        )
        await self._audit.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="create_invitation",
                entity_type="invitation",
                entity_id=invitation_id,
                before_revision=None,
                after_revision=0,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"role": role},
            ),
            transaction=transaction,
        )
        return _view(invitation)

    async def consume(
        self,
        *,
        transaction: AsyncSession,
        actor: RequestActor,
        invitation_id: UUID,
        expected_revision: int,
        token: str,
        identity: VerifiedEmailIdentity,
        request_id: UUID,
        trace_id: UUID,
    ) -> InvitationView:
        now = require_utc(self._clock())
        self._require_verified_identity(actor, identity, now=now)
        repository = InvitationRepository(transaction)
        invitation = await repository.get(
            actor.organization_id,
            invitation_id,
            for_update=True,
        )
        self._require_consumable(
            invitation,
            expected_revision=expected_revision,
            token_digest=sha256_digest(token),
            normalized_email=normalize_email(identity.verified_email),
            now=now,
        )
        updated = await repository.consume_once(
            actor.organization_id,
            invitation_id,
            token_digest=sha256_digest(token),
            expected_revision=expected_revision,
            consumed_by=identity.user_id,
            consumed_at=now,
        )
        if not updated:
            raise InvitationConflict("invitation consume lost its one-time CAS race")
        assert invitation is not None
        await transaction.refresh(invitation)
        await self._audit_transition(
            transaction=transaction,
            actor=actor,
            invitation=invitation,
            action="consume_invitation",
            before_revision=expected_revision,
            request_id=request_id,
            trace_id=trace_id,
        )
        return _view(invitation)

    async def revoke(
        self,
        *,
        transaction: AsyncSession,
        actor: RequestActor,
        invitation_id: UUID,
        expected_revision: int,
        request_id: UUID,
        trace_id: UUID,
    ) -> InvitationView:
        self._require_methodologist(actor)
        now = require_utc(self._clock())
        repository = InvitationRepository(transaction)
        invitation = await repository.get(
            actor.organization_id,
            invitation_id,
            for_update=True,
        )
        if invitation is None:
            raise InvitationNotFound("tenant-scoped invitation was not found")
        if invitation.status != "active" or invitation.revision != expected_revision:
            raise InvitationConflict("invitation is no longer active at the expected revision")
        updated = await repository.revoke_once(
            actor.organization_id,
            invitation_id,
            expected_revision=expected_revision,
            revoked_at=now,
        )
        if not updated:
            raise InvitationConflict("invitation revoke lost its one-time CAS race")
        await transaction.refresh(invitation)
        await self._audit_transition(
            transaction=transaction,
            actor=actor,
            invitation=invitation,
            action="revoke_invitation",
            before_revision=expected_revision,
            request_id=request_id,
            trace_id=trace_id,
        )
        return _view(invitation)

    async def _audit_transition(
        self,
        *,
        transaction: AsyncSession,
        actor: RequestActor,
        invitation: Invitation,
        action: str,
        before_revision: int,
        request_id: UUID,
        trace_id: UUID,
    ) -> None:
        await self._audit.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action=action,
                entity_type="invitation",
                entity_id=invitation.id,
                before_revision=before_revision,
                after_revision=invitation.revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={"status": invitation.status},
            ),
            transaction=transaction,
        )

    @staticmethod
    def _require_methodologist(actor: RequestActor) -> None:
        if actor.actor_type != "user" or "methodologist" not in actor.roles:
            raise InvitationPermissionDenied("active methodologist user is required")

    @staticmethod
    def _require_verified_identity(
        actor: RequestActor,
        identity: VerifiedEmailIdentity,
        *,
        now: datetime,
    ) -> None:
        if (
            actor.actor_type != "user"
            or actor.organization_id != identity.organization_id
            or actor.user_id != identity.user_id
            or identity.provider != "email_magic_link"
        ):
            raise VerifiedIdentityMismatch("verified identity does not match consuming actor")
        issued_at = require_utc(identity.issued_at)
        expires_at = require_utc(identity.expires_at)
        if issued_at > now or expires_at <= now or issued_at >= expires_at:
            raise VerifiedIdentityMismatch("verified identity assertion is not currently valid")
        if not identity.issuer or not identity.subject:
            raise VerifiedIdentityMismatch("verified identity issuer and subject are required")

    @staticmethod
    def _require_consumable(
        invitation: Invitation | None,
        *,
        expected_revision: int,
        token_digest: str,
        normalized_email: str,
        now: datetime,
    ) -> None:
        if invitation is None:
            raise InvitationNotFound("tenant-scoped invitation was not found")
        if invitation.status != "active" or invitation.revision != expected_revision:
            raise InvitationConflict("invitation is no longer active at the expected revision")
        if invitation.expires_at <= now:
            raise InvitationConflict("invitation has expired")
        if invitation.token_digest != token_digest:
            raise VerifiedIdentityMismatch("invitation token does not match")
        if invitation.normalized_email != normalized_email:
            raise VerifiedIdentityMismatch("verified email does not match invitation target")


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    local, separator, domain = normalized.rpartition("@")
    if (
        not separator
        or not local
        or not domain
        or "." not in domain
        or len(normalized) > 320
        or any(character.isspace() for character in normalized)
    ):
        raise InvalidInvitation("invitation email is invalid")
    return normalized


def _view(invitation: Invitation) -> InvitationView:
    role: InvitationRole
    if invitation.role == "methodologist":
        role = "methodologist"
    elif invitation.role == "reviewer":
        role = "reviewer"
    else:
        raise InvalidInvitation("persistent invitation has an unknown role")
    return InvitationView(
        invitation_id=invitation.id,
        organization_id=invitation.organization_id,
        normalized_email=invitation.normalized_email,
        role=role,
        status=invitation.status,
        revision=invitation.revision,
        expires_at=invitation.expires_at,
    )
