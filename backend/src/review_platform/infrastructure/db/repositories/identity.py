"""Tenant-safe SQLAlchemy repositories for identity and access state."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    ExternalCredential,
    ExternalIdentity,
    Invitation,
    OAuthState,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.models.organization import Organization


class UserIdentityRepository:
    """Installation-global User and canonical external-identity access."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_user(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush([user])
        return user

    async def get_user(self, user_id: UUID, *, for_update: bool = False) -> User | None:
        statement = select(User).where(User.id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add_external_identity(self, identity: ExternalIdentity) -> ExternalIdentity:
        self._session.add(identity)
        await self._session.flush([identity])
        return identity

    async def get_external_identity(
        self,
        *,
        provider: str,
        issuer: str,
        subject: str,
        for_update: bool = False,
    ) -> ExternalIdentity | None:
        statement = select(ExternalIdentity).where(
            ExternalIdentity.provider == provider,
            ExternalIdentity.issuer == issuer,
            ExternalIdentity.subject == subject,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_external_identities(self, user_id: UUID) -> Sequence[ExternalIdentity]:
        result = await self._session.execute(
            select(ExternalIdentity)
            .where(ExternalIdentity.user_id == user_id)
            .order_by(
                ExternalIdentity.provider,
                ExternalIdentity.issuer,
                ExternalIdentity.subject,
                ExternalIdentity.id,
            )
        )
        return result.scalars().all()

    async def compare_and_set_user_status(
        self,
        user_id: UUID,
        *,
        expected_status: str,
        new_status: str,
    ) -> bool:
        result = await self._session.execute(
            update(User)
            .where(User.id == user_id, User.status == expected_status)
            .values(status=new_status)
        )
        return result.rowcount == 1

    async def compare_and_set_external_identity_status(
        self,
        identity_id: UUID,
        *,
        expected_status: str,
        new_status: str,
    ) -> bool:
        result = await self._session.execute(
            update(ExternalIdentity)
            .where(
                ExternalIdentity.id == identity_id,
                ExternalIdentity.status == expected_status,
            )
            .values(status=new_status)
        )
        return result.rowcount == 1


class OrganizationMembershipRepository:
    """Membership locks, revision CAS, and auth-epoch invalidation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, membership: OrganizationMembership) -> OrganizationMembership:
        self._session.add(membership)
        await self._session.flush([membership])
        return membership

    async def lock_organization(self, organization_id: UUID) -> Organization | None:
        result = await self._session.execute(
            select(Organization)
            .where(Organization.id == organization_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        for_update: bool = False,
    ) -> OrganizationMembership | None:
        statement = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.id == membership_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_user(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> OrganizationMembership | None:
        statement = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        for_update: bool = False,
    ) -> Sequence[OrganizationMembership]:
        statement = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id
        )
        if status is not None:
            statement = statement.where(OrganizationMembership.status == status)
        statement = statement.order_by(
            OrganizationMembership.user_id,
            OrganizationMembership.id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalars().all()

    async def lock_users(
        self,
        organization_id: UUID,
        user_ids: Sequence[UUID],
    ) -> Sequence[OrganizationMembership]:
        """Lock memberships in a stable order for multi-user mutations."""

        if not user_ids:
            return ()
        result = await self._session.execute(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id.in_(tuple(sorted(set(user_ids), key=str))),
            )
            .order_by(OrganizationMembership.user_id, OrganizationMembership.id)
            .with_for_update()
        )
        return result.scalars().all()

    async def compare_and_set(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        expected_revision: int,
        roles: Sequence[str] | None = None,
        status: str | None = None,
        increment_auth_epoch: bool = False,
        revoked_at: datetime | None = None,
        revoked_by: UUID | None = None,
    ) -> bool:
        values: dict[str, object] = {"revision": expected_revision + 1}
        if roles is not None:
            values["roles"] = list(roles)
        if status is not None:
            values["status"] = status
        if increment_auth_epoch:
            values["auth_epoch"] = OrganizationMembership.auth_epoch + 1
        if revoked_at is not None:
            values["revoked_at"] = revoked_at
        if revoked_by is not None:
            values["revoked_by"] = revoked_by
        result = await self._session.execute(
            update(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
                OrganizationMembership.revision == expected_revision,
            )
            .values(**values)
        )
        return result.rowcount == 1


class InvitationRepository:
    """Invitation storage with token-digest-only one-time transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invitation: Invitation) -> Invitation:
        self._session.add(invitation)
        await self._session.flush([invitation])
        return invitation

    async def get(
        self,
        organization_id: UUID,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> Invitation | None:
        statement = select(Invitation).where(
            Invitation.organization_id == organization_id,
            Invitation.id == invitation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_token_digest(
        self,
        organization_id: UUID,
        token_digest: str,
        *,
        for_update: bool = False,
    ) -> Invitation | None:
        statement = select(Invitation).where(
            Invitation.organization_id == organization_id,
            Invitation.token_digest == token_digest,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
    ) -> Sequence[Invitation]:
        statement = select(Invitation).where(Invitation.organization_id == organization_id)
        if status is not None:
            statement = statement.where(Invitation.status == status)
        result = await self._session.execute(
            statement.order_by(Invitation.created_at.desc(), Invitation.id.desc())
        )
        return result.scalars().all()

    async def consume_once(
        self,
        organization_id: UUID,
        invitation_id: UUID,
        *,
        token_digest: str,
        expected_revision: int,
        consumed_by: UUID,
        consumed_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == organization_id,
                Invitation.id == invitation_id,
                Invitation.token_digest == token_digest,
                Invitation.status == "active",
                Invitation.revision == expected_revision,
                Invitation.expires_at > consumed_at,
            )
            .values(
                status="consumed",
                revision=expected_revision + 1,
                consumed_by=consumed_by,
                consumed_at=consumed_at,
            )
        )
        return result.rowcount == 1

    async def revoke_once(
        self,
        organization_id: UUID,
        invitation_id: UUID,
        *,
        expected_revision: int,
        revoked_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(Invitation)
            .where(
                Invitation.organization_id == organization_id,
                Invitation.id == invitation_id,
                Invitation.status == "active",
                Invitation.revision == expected_revision,
            )
            .values(
                status="revoked",
                revision=expected_revision + 1,
                revoked_at=revoked_at,
            )
        )
        return result.rowcount == 1

class OAuthStateRepository:
    """One-time OAuth state access by tenant and state digest."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, state: OAuthState) -> OAuthState:
        self._session.add(state)
        await self._session.flush([state])
        return state

    async def get(
        self,
        organization_id: UUID,
        state_id: UUID,
        *,
        for_update: bool = False,
    ) -> OAuthState | None:
        statement = select(OAuthState).where(
            OAuthState.organization_id == organization_id,
            OAuthState.state_id == state_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_state_digest(
        self,
        organization_id: UUID,
        state_digest: str,
        *,
        for_update: bool = False,
    ) -> OAuthState | None:
        statement = select(OAuthState).where(
            OAuthState.organization_id == organization_id,
            OAuthState.state_digest == state_digest,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def consume_once(
        self,
        organization_id: UUID,
        state_id: UUID,
        *,
        state_digest: str,
        consumed_at: datetime,
        resulting_session_id: UUID,
    ) -> bool:
        # A newly added Session must exist before the composite FK is updated.
        await self._session.flush()
        result = await self._session.execute(
            update(OAuthState)
            .where(
                OAuthState.organization_id == organization_id,
                OAuthState.state_id == state_id,
                OAuthState.state_digest == state_digest,
                OAuthState.consumed_at.is_(None),
                OAuthState.expires_at > consumed_at,
            )
            .values(
                consumed_at=consumed_at,
                resulting_session_id=resulting_session_id,
            )
        )
        return result.rowcount == 1


class SessionRepository:
    """Tenant-scoped session lookup and revocation by digest or membership."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session_row: Session) -> Session:
        self._session.add(session_row)
        await self._session.flush([session_row])
        return session_row

    async def get(
        self,
        organization_id: UUID,
        session_id: UUID,
        *,
        for_update: bool = False,
    ) -> Session | None:
        statement = select(Session).where(
            Session.organization_id == organization_id,
            Session.id == session_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_token_digest(
        self,
        organization_id: UUID,
        token_digest: str,
        *,
        for_update: bool = False,
    ) -> Session | None:
        statement = select(Session).where(
            Session.organization_id == organization_id,
            Session.token_digest == token_digest,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_user(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        status: str | None = None,
        for_update: bool = False,
    ) -> Sequence[Session]:
        statement = select(Session).where(
            Session.organization_id == organization_id,
            Session.user_id == user_id,
        )
        if status is not None:
            statement = statement.where(Session.status == status)
        statement = statement.order_by(Session.created_at, Session.id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalars().all()

    async def revoke_once(
        self,
        organization_id: UUID,
        session_id: UUID,
        *,
        revoked_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(Session)
            .where(
                Session.organization_id == organization_id,
                Session.id == session_id,
                Session.status == "active",
                Session.revoked_at.is_(None),
            )
            .values(status="revoked", revoked_at=revoked_at)
        )
        return result.rowcount == 1

    async def revoke_for_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        revoked_at: datetime,
    ) -> int:
        result = await self._session.execute(
            update(Session)
            .where(
                Session.organization_id == organization_id,
                Session.membership_id == membership_id,
                Session.status == "active",
                Session.revoked_at.is_(None),
            )
            .values(status="revoked", revoked_at=revoked_at)
        )
        return result.rowcount


class AgentAuthorizationRepository:
    """Tenant-scoped agent grants with revision and auth-epoch snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, authorization: AgentAuthorization) -> AgentAuthorization:
        self._session.add(authorization)
        await self._session.flush([authorization])
        return authorization

    async def get(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentAuthorization | None:
        statement = select(AgentAuthorization).where(
            AgentAuthorization.organization_id == organization_id,
            AgentAuthorization.id == authorization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_token_digest(
        self,
        organization_id: UUID,
        token_digest: str,
        *,
        for_update: bool = False,
    ) -> AgentAuthorization | None:
        statement = select(AgentAuthorization).where(
            AgentAuthorization.organization_id == organization_id,
            AgentAuthorization.token_digest == token_digest,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_user(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        status: str | None = None,
        for_update: bool = False,
    ) -> Sequence[AgentAuthorization]:
        statement = select(AgentAuthorization).where(
            AgentAuthorization.organization_id == organization_id,
            AgentAuthorization.user_id == user_id,
        )
        if status is not None:
            statement = statement.where(AgentAuthorization.status == status)
        statement = statement.order_by(AgentAuthorization.id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalars().all()

    async def compare_and_set(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        expected_revision: int,
        status: str | None = None,
        scopes: Sequence[str] | None = None,
        revoked_at: datetime | None = None,
    ) -> bool:
        values: dict[str, object] = {"revision": expected_revision + 1}
        if status is not None:
            values["status"] = status
        if scopes is not None:
            values["scopes"] = list(scopes)
        if revoked_at is not None:
            values["revoked_at"] = revoked_at
        result = await self._session.execute(
            update(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.id == authorization_id,
                AgentAuthorization.revision == expected_revision,
            )
            .values(**values)
        )
        return result.rowcount == 1

    async def revoke_once(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        expected_revision: int,
        revoked_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.id == authorization_id,
                AgentAuthorization.revision == expected_revision,
                AgentAuthorization.status == "active",
                AgentAuthorization.revoked_at.is_(None),
            )
            .values(
                status="revoked",
                revision=expected_revision + 1,
                revoked_at=revoked_at,
            )
        )
        return result.rowcount == 1

    async def revoke_for_user(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        revoked_at: datetime,
    ) -> int:
        """Invalidate every active grant for a revoked tenant membership."""

        result = await self._session.execute(
            update(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.user_id == user_id,
                AgentAuthorization.status == "active",
                AgentAuthorization.revoked_at.is_(None),
            )
            .values(
                status="revoked",
                revision=AgentAuthorization.revision + 1,
                revoked_at=revoked_at,
            )
        )
        return result.rowcount


class ExternalCredentialRepository:
    """Exact-version encrypted credential access; no plaintext boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, credential: ExternalCredential) -> ExternalCredential:
        self._session.add(credential)
        await self._session.flush([credential])
        return credential

    async def get_exact(
        self,
        organization_id: UUID,
        credential_id: UUID,
        binding_version: int,
        *,
        status: str | None = None,
        for_update: bool = False,
    ) -> ExternalCredential | None:
        statement = select(ExternalCredential).where(
            ExternalCredential.organization_id == organization_id,
            ExternalCredential.id == credential_id,
            ExternalCredential.binding_version == binding_version,
        )
        if status is not None:
            statement = statement.where(ExternalCredential.status == status)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_versions(
        self,
        organization_id: UUID,
        credential_id: UUID,
    ) -> Sequence[ExternalCredential]:
        result = await self._session.execute(
            select(ExternalCredential)
            .where(
                ExternalCredential.organization_id == organization_id,
                ExternalCredential.id == credential_id,
            )
            .order_by(ExternalCredential.binding_version.desc())
        )
        return result.scalars().all()

    async def revoke_exact(
        self,
        organization_id: UUID,
        credential_id: UUID,
        binding_version: int,
        *,
        revoked_at: datetime,
    ) -> bool:
        result = await self._session.execute(
            update(ExternalCredential)
            .where(
                ExternalCredential.organization_id == organization_id,
                ExternalCredential.id == credential_id,
                ExternalCredential.binding_version == binding_version,
                ExternalCredential.status == "active",
            )
            .values(status="revoked", revoked_at=revoked_at)
        )
        return result.rowcount == 1


__all__ = [
    "AgentAuthorizationRepository",
    "ExternalCredentialRepository",
    "InvitationRepository",
    "OAuthStateRepository",
    "OrganizationMembershipRepository",
    "SessionRepository",
    "UserIdentityRepository",
]
