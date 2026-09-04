"""Serialized OrganizationMembership role mutation and authority invalidation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.request_context import (
    KNOWN_ROLES,
    AuthVersionGuard,
    RequestActor,
    Role,
)
from review_platform.domain.primitives import require_utc, utc_now
from review_platform.infrastructure.db.models.identity import OrganizationMembership
from review_platform.infrastructure.db.models.operations import CommandReceipt
from review_platform.infrastructure.db.repositories.identity import (
    AgentAuthorizationRepository,
    OrganizationMembershipRepository,
    SessionRepository,
)


class MembershipServiceError(RuntimeError):
    """Base class for typed membership-mutation failures."""


class RoleMutationDenied(MembershipServiceError):
    """The actor cannot change roles."""


class StudentSelfEscalation(RoleMutationDenied):
    """A student attempted to grant itself reviewer or methodologist authority."""


class InvalidRoleSet(MembershipServiceError):
    """The requested role set is outside the closed vocabulary."""


class MembershipTargetNotFound(MembershipServiceError):
    """The tenant or target membership does not exist."""


class MembershipRevisionConflict(MembershipServiceError):
    """The target membership revision changed before mutation."""


class LastMethodologistViolation(MembershipServiceError):
    """The mutation would leave no active methodologist."""


@dataclass(frozen=True, slots=True)
class MembershipChangeResult:
    membership_id: UUID
    organization_id: UUID
    user_id: UUID
    roles: tuple[Role, ...]
    revision: int
    auth_epoch: int
    revoked_sessions: int
    revoked_agent_authorizations: int
    invalidated_receipts: int


class MembershipService:
    """Mutate roles inside one caller-owned transaction and final auth check."""

    def __init__(
        self,
        *,
        auth_guard: AuthVersionGuard,
        audit: AuditRecorder,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._auth_guard = auth_guard
        self._audit = audit
        self._clock = clock

    async def change_roles(
        self,
        *,
        transaction: AsyncSession,
        actor: RequestActor,
        membership_id: UUID,
        expected_revision: int,
        roles: Iterable[Role],
        request_id: UUID,
        trace_id: UUID,
    ) -> MembershipChangeResult:
        desired_roles = self._closed_roles(roles)
        repository = OrganizationMembershipRepository(transaction)
        organization = await repository.lock_organization(actor.organization_id)
        if organization is None or organization.status != "active":
            raise MembershipTargetNotFound("active tenant organization was not found")
        target = await repository.get(
            actor.organization_id,
            membership_id,
            for_update=True,
        )
        if target is None:
            raise MembershipTargetNotFound("tenant-scoped membership was not found")
        self._authorize(actor, target, desired_roles)
        if target.status != "active":
            raise RoleMutationDenied("roles cannot be changed on an inactive membership")
        if target.revision != expected_revision:
            raise MembershipRevisionConflict("membership revision is stale")

        current_roles = self._persistent_roles(target.roles)
        if "methodologist" in current_roles and "methodologist" not in desired_roles:
            active_memberships = await repository.list_for_organization(
                actor.organization_id,
                status="active",
                for_update=True,
            )
            methodologist_count = sum(
                "methodologist" in membership.roles for membership in active_memberships
            )
            if methodologist_count <= 1:
                raise LastMethodologistViolation(
                    "role mutation would remove the last active methodologist"
                )

        changed = await repository.compare_and_set(
            actor.organization_id,
            membership_id,
            expected_revision=expected_revision,
            roles=desired_roles,
            increment_auth_epoch=True,
        )
        if not changed:
            raise MembershipRevisionConflict("membership role CAS lost a concurrent race")
        now = require_utc(self._clock())
        revoked_sessions = await SessionRepository(transaction).revoke_for_membership(
            actor.organization_id,
            membership_id,
            revoked_at=now,
        )
        revoked_authorizations = await AgentAuthorizationRepository(
            transaction
        ).revoke_for_user(
            actor.organization_id,
            target.user_id,
            revoked_at=now,
        )
        invalidated_receipts = await self._invalidate_pending_receipts(
            transaction,
            organization_id=actor.organization_id,
            user_id=target.user_id,
        )
        await transaction.refresh(target)
        await self._audit.record(
            AuditEventDraft(
                organization_id=actor.organization_id,
                actor=actor,
                action="change_membership_roles",
                entity_type="organization_membership",
                entity_id=target.id,
                before_revision=expected_revision,
                after_revision=target.revision,
                request_id=request_id,
                trace_id=trace_id,
                outcome="succeeded",
                details={
                    "before_roles": sorted(current_roles),
                    "after_roles": list(desired_roles),
                    "revoked_sessions": revoked_sessions,
                    "revoked_agent_authorizations": revoked_authorizations,
                    "invalidated_receipts": invalidated_receipts,
                },
            ),
            transaction=transaction,
        )
        final_actor = actor
        if target.user_id == actor.user_id:
            if not desired_roles:
                raise RoleMutationDenied("actor cannot remove every role from its own membership")
            final_actor = RequestActor.user(
                organization_id=actor.organization_id,
                user_id=target.user_id,
                roles=desired_roles,
                membership_revision=target.revision,
                auth_epoch=target.auth_epoch,
            )
        await self._auth_guard.lock_and_revalidate(
            actor=final_actor,
            transaction=transaction,
        )
        return MembershipChangeResult(
            membership_id=target.id,
            organization_id=target.organization_id,
            user_id=target.user_id,
            roles=desired_roles,
            revision=target.revision,
            auth_epoch=target.auth_epoch,
            revoked_sessions=revoked_sessions,
            revoked_agent_authorizations=revoked_authorizations,
            invalidated_receipts=invalidated_receipts,
        )

    @staticmethod
    def _authorize(
        actor: RequestActor,
        target: OrganizationMembership,
        desired_roles: tuple[Role, ...],
    ) -> None:
        privileged_additions = {"methodologist", "reviewer"}.intersection(desired_roles).difference(
            target.roles
        )
        if (
            actor.user_id == target.user_id
            and "methodologist" not in actor.roles
            and privileged_additions
        ):
            raise StudentSelfEscalation("student cannot grant itself privileged roles")
        if actor.actor_type != "user" or "methodologist" not in actor.roles:
            raise RoleMutationDenied("active methodologist user is required")

    @staticmethod
    def _closed_roles(roles: Iterable[Role]) -> tuple[Role, ...]:
        values = set(roles)
        unknown = values.difference(KNOWN_ROLES)
        if unknown:
            raise InvalidRoleSet(f"unknown role(s): {sorted(unknown)!r}")
        order: tuple[Role, ...] = ("methodologist", "reviewer", "student")
        return tuple(role for role in order if role in values)

    @staticmethod
    def _persistent_roles(roles: list[str]) -> frozenset[Role]:
        unknown = set(roles).difference(KNOWN_ROLES)
        if unknown:
            raise InvalidRoleSet(f"persistent membership has unknown role(s): {unknown!r}")
        return frozenset(cast(list[Role], roles))

    @staticmethod
    async def _invalidate_pending_receipts(
        transaction: AsyncSession,
        *,
        organization_id: UUID,
        user_id: UUID,
    ) -> int:
        result = await transaction.execute(
            update(CommandReceipt)
            .where(
                CommandReceipt.organization_id == organization_id,
                CommandReceipt.status.in_(("reserved", "processing")),
                CommandReceipt.actor_snapshot["user_id"].as_string() == str(user_id),
            )
            .values(status="invalidated")
        )
        return result.rowcount
