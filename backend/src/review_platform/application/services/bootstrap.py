"""One-time local installation bootstrap and methodologist recovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.idempotency import (
    IdempotencyConflict,
    IdempotencyCoordinator,
    IdempotencyReceiptRepository,
)
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import (
    ActivateBootstrapPayload,
    ApplicationCommand,
    InstallationOperatorActor,
    RecoverMethodologistPayload,
    Transport,
)
from review_platform.domain.primitives import require_utc, utc_now, uuid7
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.identity import (
    ExternalIdentity,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.operations import AuditEvent
from review_platform.infrastructure.db.repositories.identity import (
    OrganizationMembershipRepository,
    UserIdentityRepository,
)
from review_platform.infrastructure.db.repositories.operations import CommandReceiptRepository


class BootstrapError(RuntimeError):
    """Base error for a rejected installation-operator operation."""


class InvalidBootstrapCommand(BootstrapError):
    """The command is not an exact local operator command for this organization."""


class OrganizationNotFound(BootstrapError):
    """The explicit installation organization does not exist."""


class BootstrapAlreadyActivated(BootstrapError):
    """The installation already has an active methodologist."""


class BootstrapRevisionConflict(BootstrapError):
    """The organization revision changed before the local operation."""


class InvalidExternalIdentity(BootstrapError):
    """The exact provider identity is revoked or points at an unusable user."""


class RecoveryAuthorityConflict(BootstrapError):
    """The one-time recovery authority was reused for a different recipient."""


class RecoveryReplayInconsistent(BootstrapError):
    """A replay receipt exists but its recovered identity is missing."""


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    organization_id: UUID
    user_id: UUID
    external_identity_id: UUID
    membership_id: UUID
    roles: tuple[str, ...]
    organization_revision: int
    replayed: bool = False


class BootstrapService:
    """Mutate bootstrap state only inside a caller-owned ``AsyncSession``."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        idempotency_repository: IdempotencyReceiptRepository | None = None,
        audit_recorder: AuditRecorder | None = None,
    ) -> None:
        self._session = session
        self._id_factory = id_factory
        self._clock = clock
        self._memberships = OrganizationMembershipRepository(session)
        self._identities = UserIdentityRepository(session)
        self._idempotency_repository = (
            idempotency_repository or SqlIdempotencyReceiptRepository()
        )
        self._audit = audit_recorder or AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=id_factory,
            clock=self._datetime_now,
        )

    async def activate(self, command: ApplicationCommand) -> BootstrapResult:
        actor = _require_operator(command, expected_name="activate_bootstrap")
        if not isinstance(command.payload, ActivateBootstrapPayload):
            raise InvalidBootstrapCommand("activate_bootstrap payload is not exact")
        organization = await self._memberships.lock_organization(command.organization_id)
        if organization is None:
            raise OrganizationNotFound("explicit bootstrap organization was not found")
        if organization.id != command.target_id:
            raise InvalidBootstrapCommand("bootstrap target does not match organization")

        active = await self._memberships.list_for_organization(
            command.organization_id,
            status="active",
            for_update=True,
        )
        if (
            organization.revision != 0
            or await self._bootstrap_was_activated(command.organization_id)
            or any("methodologist" in membership.roles for membership in active)
        ):
            raise BootstrapAlreadyActivated("installation bootstrap was already activated")
        if organization.revision != command.expected_revision:
            raise BootstrapRevisionConflict("bootstrap organization revision is stale")

        claim = command.payload.external_identity
        user, identity = await self._resolve_or_create_identity(
            provider=claim.provider,
            issuer=claim.issuer,
            subject=claim.subject,
            verified_email=None,
        )
        membership = await self._grant_methodologist(
            organization_id=command.organization_id,
            user=user,
        )
        before_revision = organization.revision
        organization.revision += 1
        await self._record_audit(
            command=command,
            actor=actor,
            action="activate_bootstrap",
            entity_id=membership.id,
            before_revision=before_revision,
            after_revision=organization.revision,
            details={
                "provider": identity.provider,
                "issuer": identity.issuer,
                "subject": identity.subject,
            },
        )
        await self._session.flush()
        return _result(
            organization_revision=organization.revision,
            identity=identity,
            membership=membership,
        )

    async def recover(self, command: ApplicationCommand) -> BootstrapResult:
        actor = _require_operator(command, expected_name="recover_methodologist")
        if not isinstance(command.payload, RecoverMethodologistPayload):
            raise InvalidBootstrapCommand("recover_methodologist payload is not exact")
        payload = command.payload
        if (
            payload.organization_id != command.organization_id
            or command.target_id != command.organization_id
        ):
            raise InvalidBootstrapCommand("recovery organization identities do not match")

        organization = await self._memberships.lock_organization(command.organization_id)
        if organization is None:
            raise OrganizationNotFound("explicit recovery organization was not found")

        coordinator = IdempotencyCoordinator(
            self._idempotency_repository,
            receipt_id_factory=self._id_factory,
            result_reference_factory=lambda: {
                "kind": "methodologist_recovery",
                "id": str(command.request_id),
            },
        )
        try:
            reservation = await coordinator.reserve(
                organization_id=command.organization_id,
                idempotency_key=command.idempotency_key,
                request_id=command.request_id,
                command_name=str(command.command_name),
                target_id=command.target_id,
                expected_revision=command.expected_revision,
                payload=payload,
                transaction=self._session,
            )
        except IdempotencyConflict as error:
            raise RecoveryAuthorityConflict(
                "recovery authority is already bound to another recipient"
            ) from error

        if reservation.disposition == "replay":
            replay = await self._find_result(
                organization_id=command.organization_id,
                provider=payload.provider,
                issuer=payload.issuer,
                subject=payload.subject,
                organization_revision=organization.revision,
            )
            if replay is None:
                raise RecoveryReplayInconsistent(
                    "recovery receipt exists without the exact recovered membership"
                )
            return replay

        if organization.revision != command.expected_revision:
            raise BootstrapRevisionConflict("recovery organization revision is stale")
        user, identity = await self._resolve_or_create_identity(
            provider=payload.provider,
            issuer=payload.issuer,
            subject=payload.subject,
            verified_email=None,
        )
        membership = await self._grant_methodologist(
            organization_id=command.organization_id,
            user=user,
        )
        before_revision = organization.revision
        organization.revision += 1
        await self._record_audit(
            command=command,
            actor=actor,
            action="recover_methodologist",
            entity_id=membership.id,
            before_revision=before_revision,
            after_revision=organization.revision,
            details={
                "provider": payload.provider,
                "issuer": payload.issuer,
                "subject": payload.subject,
                "reason": payload.reason,
                "installation_operator_reason": actor.reason,
            },
        )
        receipt = await CommandReceiptRepository(self._session).get_by_idempotency_key(
            command.organization_id,
            command.idempotency_key,
            for_update=True,
        )
        if receipt is None:
            raise RecoveryReplayInconsistent("recovery receipt disappeared before commit")
        receipt.status = "succeeded"
        receipt.actor_snapshot = {
            "type": actor.actor_type,
            "installation_operator_id": actor.installation_operator_id,
            "reason": actor.reason,
        }
        await self._session.flush()
        return _result(
            organization_revision=organization.revision,
            identity=identity,
            membership=membership,
        )

    async def _resolve_or_create_identity(
        self,
        *,
        provider: str,
        issuer: str,
        subject: str,
        verified_email: str | None,
    ) -> tuple[User, ExternalIdentity]:
        identity = await self._identities.get_external_identity(
            provider=provider,
            issuer=issuer,
            subject=subject,
            for_update=True,
        )
        if identity is not None:
            if identity.status != "active":
                raise InvalidExternalIdentity("exact external identity is revoked")
            user = await self._identities.get_user(identity.user_id, for_update=True)
            if user is None or user.status != "active":
                raise InvalidExternalIdentity("exact external identity user is not active")
            if verified_email is not None:
                identity.verified_email = verified_email
            return user, identity

        user = User(
            id=self._id_factory(),
            display_name=verified_email or subject,
            status="active",
        )
        await self._identities.add_user(user)
        identity = ExternalIdentity(
            id=self._id_factory(),
            user_id=user.id,
            provider=provider,
            issuer=issuer,
            subject=subject,
            verified_email=verified_email,
            status="active",
        )
        await self._identities.add_external_identity(identity)
        return user, identity

    async def _grant_methodologist(
        self,
        *,
        organization_id: UUID,
        user: User,
    ) -> OrganizationMembership:
        membership = await self._memberships.get_by_user(
            organization_id,
            user.id,
            for_update=True,
        )
        if membership is None:
            membership = OrganizationMembership(
                id=self._id_factory(),
                organization_id=organization_id,
                user_id=user.id,
                roles=["methodologist"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
            return await self._memberships.add(membership)

        roles = set(membership.roles)
        roles.add("methodologist")
        changed = (
            roles != set(membership.roles)
            or membership.status != "active"
            or membership.revoked_at is not None
        )
        membership.roles = sorted(roles)
        membership.status = "active"
        membership.revoked_at = None
        membership.revoked_by = None
        if changed:
            membership.revision += 1
            membership.auth_epoch += 1
        return membership

    async def _find_result(
        self,
        *,
        organization_id: UUID,
        provider: str,
        issuer: str,
        subject: str,
        organization_revision: int,
    ) -> BootstrapResult | None:
        identity = await self._identities.get_external_identity(
            provider=provider,
            issuer=issuer,
            subject=subject,
        )
        if identity is None:
            return None
        membership = await self._memberships.get_by_user(
            organization_id,
            identity.user_id,
        )
        if (
            membership is None
            or membership.status != "active"
            or "methodologist" not in membership.roles
        ):
            return None
        return _result(
            organization_revision=organization_revision,
            identity=identity,
            membership=membership,
            replayed=True,
        )

    async def _record_audit(
        self,
        *,
        command: ApplicationCommand,
        actor: RequestActor,
        action: str,
        entity_id: UUID,
        before_revision: int,
        after_revision: int,
        details: Mapping[str, object],
    ) -> None:
        await self._audit.record(
            AuditEventDraft(
                organization_id=command.organization_id,
                actor=actor,
                action=action,
                entity_type="organization_membership",
                entity_id=entity_id,
                before_revision=before_revision,
                after_revision=after_revision,
                request_id=command.request_id,
                trace_id=command.trace_id,
                outcome="succeeded",
                details=details,
            ),
            transaction=self._session,
        )

    async def _bootstrap_was_activated(self, organization_id: UUID) -> bool:
        result = await self._session.execute(
            select(AuditEvent.id)
            .where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.action == "activate_bootstrap",
                AuditEvent.outcome == "succeeded",
            )
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none() is not None

    def _datetime_now(self) -> datetime:
        return require_utc(self._clock())


def _require_operator(command: ApplicationCommand, *, expected_name: str) -> RequestActor:
    if str(command.command_name) != expected_name:
        raise InvalidBootstrapCommand(f"expected {expected_name} command")
    if command.transport != Transport.OPERATOR or not isinstance(
        command.actor, InstallationOperatorActor
    ):
        raise InvalidBootstrapCommand("bootstrap/recovery requires installation_operator")
    if not command.actor.installation_operator_id or not command.actor.reason:
        raise InvalidBootstrapCommand("installation operator identity and reason are required")
    return RequestActor.installation_operator(
        organization_id=command.organization_id,
        installation_operator_id=command.actor.installation_operator_id,
        reason=command.actor.reason,
    )


def _result(
    *,
    organization_revision: int,
    identity: ExternalIdentity,
    membership: OrganizationMembership,
    replayed: bool = False,
) -> BootstrapResult:
    return BootstrapResult(
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        external_identity_id=identity.id,
        membership_id=membership.id,
        roles=tuple(sorted(membership.roles)),
        organization_revision=organization_revision,
        replayed=replayed,
    )


__all__ = [
    "BootstrapAlreadyActivated",
    "BootstrapError",
    "BootstrapResult",
    "BootstrapRevisionConflict",
    "BootstrapService",
    "InvalidBootstrapCommand",
    "InvalidExternalIdentity",
    "OrganizationNotFound",
    "RecoveryAuthorityConflict",
    "RecoveryReplayInconsistent",
]
