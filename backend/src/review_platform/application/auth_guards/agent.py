"""Combined user and AgentAuthorization revalidation guard."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.auth_guards.membership import (
    InvalidMembershipGuardTransaction,
    MembershipGuardError,
    UserMembershipAuthGuard,
)
from review_platform.application.request_context import (
    KNOWN_AGENT_SCOPES,
    KNOWN_ROLES,
    AgentScope,
    AuthVersionGuard,
    AuthVersionSnapshot,
    RequestActor,
    Role,
)
from review_platform.domain.primitives import require_utc, utc_now
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.repositories.agents import (
    LockedAgentAuthority,
    SqlAgentAuthorizationRepository,
)
from review_platform.infrastructure.db.repositories.identity import (
    OrganizationMembershipRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory


class AgentGuardError(MembershipGuardError):
    pass


class AgentAuthorityNotFound(AgentGuardError):
    pass


class AgentAuthorityInactive(AgentGuardError):
    pass


class StaleAgentAuthority(AgentGuardError):
    pass


class InvalidAgentAuthority(AgentGuardError):
    pass


class CombinedMembershipAgentAuthGuard(AuthVersionGuard):
    """Preserve user behavior and add exact agent authority revalidation."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._users = UserMembershipAuthGuard(session_factory)
        self._agents = SqlAgentAuthorizationRepository()

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        if actor.actor_type == "user":
            return await self._users.revalidate(actor=actor)
        self._require_agent(actor)
        async with self._session_factory() as session:
            organization = await session.scalar(
                select(Organization).where(Organization.id == actor.organization_id)
            )
            authority = await self._agents.lock_membership_then_authorization(
                actor.organization_id,
                cast(UUID, actor.user_id),
                cast(UUID, actor.agent_authorization_id),
                transaction=session,
            )
            return self._validate(actor, organization, authority)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        if actor.actor_type == "user":
            return await self._users.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
        self._require_agent(actor)
        if not isinstance(transaction, AsyncSession):
            raise InvalidMembershipGuardTransaction(
                "agent final revalidation requires caller-owned AsyncSession"
            )
        memberships = OrganizationMembershipRepository(transaction)
        organization = await memberships.lock_organization(actor.organization_id)
        authority = await self._agents.lock_membership_then_authorization(
            actor.organization_id,
            cast(UUID, actor.user_id),
            cast(UUID, actor.agent_authorization_id),
            transaction=transaction,
        )
        return self._validate(actor, organization, authority)

    @staticmethod
    def _require_agent(actor: RequestActor) -> None:
        if actor.actor_type != "agent":
            raise InvalidAgentAuthority(
                "combined authorization guard accepts only user or agent actors"
            )
        if (
            actor.user_id is None
            or actor.agent_id is None
            or actor.agent_authorization_id is None
            or actor.agent_authorization_revision is None
        ):
            raise InvalidAgentAuthority("agent actor identity is incomplete")

    def _validate(
        self,
        actor: RequestActor,
        organization: Organization | None,
        authority: LockedAgentAuthority | None,
    ) -> AuthVersionSnapshot:
        if organization is None or authority is None:
            raise AgentAuthorityNotFound("tenant agent authority was not found")
        if organization.id != actor.organization_id:
            raise InvalidAgentAuthority("agent guard loaded a different organization")
        if organization.status != "active":
            raise AgentAuthorityInactive("tenant organization is inactive")
        authorization = authority.authorization
        if (
            authority.organization_id != actor.organization_id
            or authority.user_id != actor.user_id
            or authorization.organization_id != actor.organization_id
            or authorization.user_id != actor.user_id
            or authorization.authorization_id != actor.agent_authorization_id
            or authorization.agent_id != actor.agent_id
        ):
            raise InvalidAgentAuthority("agent authority identity crossed scope")
        roles = frozenset(cast(tuple[Role, ...], authority.roles))
        scopes = frozenset(cast(tuple[AgentScope, ...], authorization.scopes))
        if set(roles).difference(KNOWN_ROLES) or set(scopes).difference(KNOWN_AGENT_SCOPES):
            raise InvalidAgentAuthority("agent authority contains unknown roles or scopes")
        if authority.membership_status != "active":
            raise AgentAuthorityInactive("organization membership is inactive")
        if (
            authorization.status != "active"
            or authorization.revoked_at is not None
            or require_utc(authorization.expires_at) <= require_utc(self._clock())
        ):
            raise AgentAuthorityInactive("agent authorization is revoked or expired")
        if (
            authority.membership_revision != actor.membership_revision
            or authority.auth_epoch != actor.auth_epoch
            or authorization.membership_revision != actor.membership_revision
            or authorization.auth_epoch != actor.auth_epoch
            or authorization.revision != actor.agent_authorization_revision
            or roles != actor.roles
            or scopes != actor.scopes
            or (
                actor.expires_at is not None
                and require_utc(actor.expires_at) != require_utc(authorization.expires_at)
            )
        ):
            raise StaleAgentAuthority("membership or agent authorization snapshot is stale")
        return AuthVersionSnapshot(
            organization_id=authority.organization_id,
            user_id=authority.user_id,
            roles=roles,
            membership_revision=authority.membership_revision,
            auth_epoch=authority.auth_epoch,
            active=True,
            agent_authorization_id=authorization.authorization_id,
            agent_authorization_revision=authorization.revision,
            agent_scopes=scopes,
            agent_expires_at=authorization.expires_at,
            agent_active=True,
        )


__all__ = [
    "AgentAuthorityInactive",
    "AgentAuthorityNotFound",
    "AgentGuardError",
    "CombinedMembershipAgentAuthGuard",
    "InvalidAgentAuthority",
    "StaleAgentAuthority",
]
