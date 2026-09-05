"""Tenant-safe persistence for revocable AgentAuthorization grants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.request_context import KNOWN_AGENT_SCOPES, KNOWN_ROLES
from review_platform.domain.primitives import require_utc, validate_digest
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    OrganizationMembership,
)
from review_platform.infrastructure.db.models.operations import CommandReceipt


class AgentAuthorizationRepositoryError(RuntimeError):
    pass


class InvalidAgentAuthorizationTransaction(AgentAuthorizationRepositoryError):
    pass


class AgentAuthorizationCollision(AgentAuthorizationRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class AgentAuthorizationRecord:
    authorization_id: UUID
    organization_id: UUID
    user_id: UUID
    agent_id: UUID
    scopes: tuple[str, ...]
    membership_revision: int
    auth_epoch: int
    token_digest: str
    status: str
    expires_at: datetime
    revision: int
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LockedAgentAuthority:
    organization_id: UUID
    membership_id: UUID
    user_id: UUID
    roles: tuple[str, ...]
    membership_status: str
    membership_revision: int
    auth_epoch: int
    authorization: AgentAuthorizationRecord


class SqlAgentAuthorizationRepository:
    """Caller-owned SQL adapter; raw bearer tokens never cross this boundary."""

    async def reserve(
        self,
        candidate: AgentAuthorizationRecord,
        *,
        transaction: object,
    ) -> tuple[AgentAuthorizationRecord, bool]:
        session = _session(transaction)
        self._validate_candidate(candidate)
        existing = await self._by_digest(session, candidate.token_digest)
        if existing is not None:
            self._require_same(existing, candidate)
            return _record(existing), False
        row = AgentAuthorization(
            id=candidate.authorization_id,
            organization_id=candidate.organization_id,
            user_id=candidate.user_id,
            agent_id=candidate.agent_id,
            scopes=list(candidate.scopes),
            membership_revision=candidate.membership_revision,
            auth_epoch=candidate.auth_epoch,
            token_digest=candidate.token_digest,
            status=candidate.status,
            expires_at=require_utc(candidate.expires_at),
            revision=candidate.revision,
            revoked_at=candidate.revoked_at,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError:
            existing = await self._by_digest(
                session,
                candidate.token_digest,
                for_update=True,
                populate_existing=True,
            )
            if existing is None:
                raise
            self._require_same(existing, candidate)
            return _record(existing), False
        return _record(row), True

    async def lookup_by_digest(
        self,
        token_digest: str,
        *,
        transaction: object,
    ) -> AgentAuthorizationRecord | None:
        session = _session(transaction)
        digest = validate_digest(token_digest)
        row = await self._by_digest(session, digest)
        return None if row is None else _record(row)

    async def lookup_tenant_by_digest(
        self,
        organization_id: UUID,
        token_digest: str,
        *,
        transaction: object,
    ) -> AgentAuthorizationRecord | None:
        session = _session(transaction)
        digest = validate_digest(token_digest)
        row = await session.scalar(
            select(AgentAuthorization).where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.token_digest == digest,
            )
        )
        return None if row is None else _record(row)

    async def lock_by_id(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        transaction: object,
    ) -> AgentAuthorizationRecord | None:
        session = _session(transaction)
        row = await session.scalar(
            select(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.id == authorization_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return None if row is None else _record(row)

    async def lock_membership_then_authorization(
        self,
        organization_id: UUID,
        user_id: UUID,
        authorization_id: UUID,
        *,
        transaction: object,
    ) -> LockedAgentAuthority | None:
        session = _session(transaction)
        membership = await session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if membership is None:
            return None
        authorization = await session.scalar(
            select(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.id == authorization_id,
                AgentAuthorization.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if authorization is None:
            return None
        return LockedAgentAuthority(
            organization_id=organization_id,
            membership_id=membership.id,
            user_id=membership.user_id,
            roles=tuple(membership.roles),
            membership_status=membership.status,
            membership_revision=membership.revision,
            auth_epoch=membership.auth_epoch,
            authorization=_record(authorization),
        )

    async def resolve_active_digest(
        self,
        token_digest: str,
        *,
        now: datetime,
        transaction: object,
    ) -> LockedAgentAuthority | None:
        session = _session(transaction)
        digest = validate_digest(token_digest)
        selected_at = require_utc(now)
        authorization = await self._by_digest(session, digest)
        if authorization is None:
            return None
        membership = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == authorization.organization_id,
                OrganizationMembership.user_id == authorization.user_id,
            )
        )
        if (
            membership is None
            or membership.status != "active"
            or authorization.status != "active"
            or authorization.revoked_at is not None
            or authorization.expires_at <= selected_at
            or authorization.membership_revision != membership.revision
            or authorization.auth_epoch != membership.auth_epoch
            or set(membership.roles).difference(KNOWN_ROLES)
            or set(authorization.scopes).difference(KNOWN_AGENT_SCOPES)
        ):
            return None
        return LockedAgentAuthority(
            organization_id=authorization.organization_id,
            membership_id=membership.id,
            user_id=membership.user_id,
            roles=tuple(membership.roles),
            membership_status=membership.status,
            membership_revision=membership.revision,
            auth_epoch=membership.auth_epoch,
            authorization=_record(authorization),
        )

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
    ) -> bool:
        session = _session(transaction)
        context = await self.lock_membership_then_authorization(
            organization_id,
            user_id,
            authorization_id,
            transaction=session,
        )
        if context is None:
            return False
        authorization = context.authorization
        if (
            context.membership_status != "active"
            or context.membership_revision != expected_membership_revision
            or context.auth_epoch != expected_auth_epoch
            or authorization.membership_revision != expected_membership_revision
            or authorization.auth_epoch != expected_auth_epoch
            or authorization.revision != expected_revision
            or authorization.status != "active"
            or authorization.revoked_at is not None
        ):
            return False
        result = await session.execute(
            update(AgentAuthorization)
            .where(
                AgentAuthorization.organization_id == organization_id,
                AgentAuthorization.id == authorization_id,
                AgentAuthorization.user_id == user_id,
                AgentAuthorization.revision == expected_revision,
                AgentAuthorization.membership_revision == expected_membership_revision,
                AgentAuthorization.auth_epoch == expected_auth_epoch,
                AgentAuthorization.status == "active",
                AgentAuthorization.revoked_at.is_(None),
            )
            .values(
                status="revoked",
                revision=expected_revision + 1,
                revoked_at=require_utc(revoked_at),
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1

    async def invalidate_pending_receipts(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        authorization_revision: int | None = None,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        rendered_id = str(authorization_id)
        predicates = [
            CommandReceipt.organization_id == organization_id,
            CommandReceipt.status.in_(("reserved", "processing")),
            func.json_unquote(
                func.json_extract(
                    CommandReceipt.actor_snapshot,
                    "$.agent_authorization_id",
                )
            )
            == rendered_id,
        ]
        if authorization_revision is not None:
            if authorization_revision < 0:
                raise ValueError("authorization_revision must be nonnegative")
            predicates.append(
                func.json_extract(
                    CommandReceipt.actor_snapshot,
                    "$.agent_authorization_revision",
                )
                == authorization_revision
            )
        result = await session.execute(
            update(CommandReceipt)
            .where(*predicates)
            .values(
                status="invalidated",
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount

    async def invalidate_pending_commands(
        self,
        organization_id: UUID,
        authorization_id: UUID,
        *,
        transaction: object,
    ) -> int:
        """Compatibility alias for receipts lacking an authority revision."""

        return await self.invalidate_pending_receipts(
            organization_id,
            authorization_id,
            transaction=transaction,
        )

    @staticmethod
    async def _by_digest(
        session: AsyncSession,
        token_digest: str,
        *,
        for_update: bool = False,
        populate_existing: bool = False,
    ) -> AgentAuthorization | None:
        statement = select(AgentAuthorization).where(
            AgentAuthorization.token_digest == token_digest
        )
        if for_update:
            statement = statement.with_for_update()
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return cast(AgentAuthorization | None, await session.scalar(statement))

    @staticmethod
    def _validate_candidate(candidate: AgentAuthorizationRecord) -> None:
        validate_digest(candidate.token_digest)
        require_utc(candidate.expires_at)
        if (
            candidate.status != "active"
            or candidate.revision != 0
            or candidate.revoked_at is not None
            or candidate.membership_revision < 0
            or candidate.auth_epoch < 0
            or not candidate.scopes
            or set(candidate.scopes).difference(KNOWN_AGENT_SCOPES)
            or len(set(candidate.scopes)) != len(candidate.scopes)
        ):
            raise AgentAuthorizationCollision(
                "new authorization must carry exact active versioned authority"
            )

    @staticmethod
    def _require_same(
        stored: AgentAuthorization,
        candidate: AgentAuthorizationRecord,
    ) -> None:
        fields = (
            "organization_id",
            "user_id",
            "agent_id",
            "membership_revision",
            "auth_epoch",
            "token_digest",
            "status",
            "expires_at",
            "revision",
        )
        if (
            stored.id != candidate.authorization_id
            or tuple(stored.scopes) != candidate.scopes
            or any(getattr(stored, field) != getattr(candidate, field) for field in fields)
        ):
            raise AgentAuthorizationCollision("authorization digest or identity is already bound")


def _record(row: AgentAuthorization) -> AgentAuthorizationRecord:
    return AgentAuthorizationRecord(
        authorization_id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        scopes=tuple(row.scopes),
        membership_revision=row.membership_revision,
        auth_epoch=row.auth_epoch,
        token_digest=row.token_digest,
        status=row.status,
        expires_at=row.expires_at,
        revision=row.revision,
        revoked_at=row.revoked_at,
    )


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise InvalidAgentAuthorizationTransaction(
            "agent authorization repository requires caller-owned AsyncSession"
        )
    return transaction


__all__ = [
    "AgentAuthorizationCollision",
    "AgentAuthorizationRecord",
    "AgentAuthorizationRepositoryError",
    "InvalidAgentAuthorizationTransaction",
    "LockedAgentAuthority",
    "SqlAgentAuthorizationRepository",
]
