"""Concrete OrganizationMembership authorization-version guard for users."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.authorization import AuthorizationDenied
from review_platform.application.request_context import (
    KNOWN_ROLES,
    AuthVersionGuard,
    AuthVersionSnapshot,
    RequestActor,
    Role,
)
from review_platform.infrastructure.db.models.identity import OrganizationMembership
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.repositories.identity import (
    OrganizationMembershipRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory


class MembershipGuardError(AuthorizationDenied):
    """Base class for fail-closed concrete membership guard failures."""


class UnsupportedMembershipActor(MembershipGuardError):
    """This guard cannot validate operator or AgentAuthorization actors."""


class InvalidMembershipGuardTransaction(MembershipGuardError):
    """Final revalidation did not receive the caller-owned AsyncSession."""


class MembershipNotFound(MembershipGuardError):
    """The tenant anchor or tenant-scoped represented-user membership is absent."""


class MembershipInactive(MembershipGuardError):
    """The organization or membership is not active."""


class StaleMembershipAuthority(MembershipGuardError):
    """The request carries an obsolete revision, auth epoch, or role snapshot."""


class InvalidMembershipAuthority(MembershipGuardError):
    """Persistent membership authority contains an unknown role or identity."""


class UserMembershipAuthGuard(AuthVersionGuard):
    """Validate user authority and repeat it under fixed-order commit locks.

    Agent actors are deliberately rejected; T153 adds the membership plus
    AgentAuthorization lock path without weakening this user-only boundary.
    """

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        """Perform an early tenant-scoped current-authority check without locks."""

        self._require_user_actor(actor)
        async with self._session_factory() as session:
            organization = await self._get_organization(session, actor.organization_id)
            membership = await OrganizationMembershipRepository(session).get_by_user(
                actor.organization_id,
                self._require_user_id(actor),
            )
            return self._validate(actor, organization, membership)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        """Lock Organization then membership in the caller's transaction and check."""

        self._require_user_actor(actor)
        if not isinstance(transaction, AsyncSession):
            raise InvalidMembershipGuardTransaction(
                "membership final revalidation requires caller-owned AsyncSession"
            )
        repository = OrganizationMembershipRepository(transaction)
        # The order is security-significant and shared with role mutation:
        # Organization serializes last-methodologist changes, then membership
        # serializes auth_epoch and revision revocation.
        organization = await repository.lock_organization(actor.organization_id)
        membership = await repository.get_by_user(
            actor.organization_id,
            self._require_user_id(actor),
            for_update=True,
        )
        return self._validate(actor, organization, membership)

    @staticmethod
    async def _get_organization(
        session: AsyncSession, organization_id: UUID
    ) -> Organization | None:
        result = await session.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _require_user_actor(actor: RequestActor) -> None:
        if actor.actor_type != "user":
            raise UnsupportedMembershipActor(
                "user membership guard accepts only authenticated user actors"
            )

    @staticmethod
    def _require_user_id(actor: RequestActor) -> UUID:
        if actor.user_id is None:
            raise InvalidMembershipAuthority("user actor is missing represented user identity")
        return actor.user_id

    @staticmethod
    def _validate(
        actor: RequestActor,
        organization: Organization | None,
        membership: OrganizationMembership | None,
    ) -> AuthVersionSnapshot:
        if organization is None:
            raise MembershipNotFound("tenant organization was not found")
        if organization.id != actor.organization_id:
            raise InvalidMembershipAuthority("guard loaded a different tenant organization")
        if organization.status != "active":
            raise MembershipInactive("tenant organization is not active")
        if membership is None:
            raise MembershipNotFound("tenant-scoped user membership was not found")
        if (
            membership.organization_id != actor.organization_id
            or membership.user_id != actor.user_id
        ):
            raise InvalidMembershipAuthority("guard loaded a different membership identity")
        if membership.status != "active":
            raise MembershipInactive("organization membership is not active")

        unknown_roles = set(membership.roles).difference(KNOWN_ROLES)
        if unknown_roles:
            raise InvalidMembershipAuthority(
                f"membership contains unknown role(s): {sorted(unknown_roles)!r}"
            )
        roles = frozenset(cast(list[Role], membership.roles))
        if (
            membership.revision != actor.membership_revision
            or membership.auth_epoch != actor.auth_epoch
            or roles != actor.roles
        ):
            raise StaleMembershipAuthority(
                "membership revision, auth epoch, or role snapshot is stale"
            )
        return AuthVersionSnapshot(
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            roles=roles,
            membership_revision=membership.revision,
            auth_epoch=membership.auth_epoch,
            active=True,
        )


MembershipAuthVersionGuard = UserMembershipAuthGuard

__all__ = [
    "InvalidMembershipAuthority",
    "InvalidMembershipGuardTransaction",
    "MembershipAuthVersionGuard",
    "MembershipGuardError",
    "MembershipInactive",
    "MembershipNotFound",
    "StaleMembershipAuthority",
    "UnsupportedMembershipActor",
    "UserMembershipAuthGuard",
]
