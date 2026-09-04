"""Server-owned authenticated actor context and revocation guard boundary.

Wire commands deliberately do not construct :class:`RequestActor`.  A transport
adapter creates it from an already authenticated session, agent authorization, or
the local installation-operator entrypoint and passes it into the application
layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

type Role = Literal["methodologist", "reviewer", "student"]
type AgentScope = Literal[
    "courses:read",
    "review_preferences:write",
    "review_queue:read",
    "reviews:read",
    "reviews:write",
    "ai_reviews:start",
    "operations:read",
    "publication_requests:write",
]
type ActorType = Literal["user", "agent", "installation_operator"]

KNOWN_ROLES = frozenset[Role]({"methodologist", "reviewer", "student"})
KNOWN_AGENT_SCOPES = frozenset[AgentScope](
    {
        "courses:read",
        "review_preferences:write",
        "review_queue:read",
        "reviews:read",
        "reviews:write",
        "ai_reviews:start",
        "operations:read",
        "publication_requests:write",
    }
)


class InvalidActorContext(ValueError):
    """The authenticated context is internally inconsistent or not closed-world."""


@dataclass(frozen=True, slots=True)
class RequestActor:
    """Authenticated, server-owned identity supplied to application commands."""

    organization_id: UUID
    actor_type: ActorType
    user_id: UUID | None = None
    roles: frozenset[Role] = frozenset()
    membership_revision: int | None = None
    auth_epoch: int | None = None
    agent_id: UUID | None = None
    agent_authorization_id: UUID | None = None
    agent_authorization_revision: int | None = None
    scopes: frozenset[AgentScope] = frozenset()
    expires_at: datetime | None = None
    installation_operator_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        unknown_roles = set(self.roles).difference(KNOWN_ROLES)
        if unknown_roles:
            raise InvalidActorContext(f"unknown role(s): {sorted(unknown_roles)!r}")
        unknown_scopes = set(self.scopes).difference(KNOWN_AGENT_SCOPES)
        if unknown_scopes:
            raise InvalidActorContext(f"unknown agent scope(s): {sorted(unknown_scopes)!r}")

        if self.actor_type == "installation_operator":
            if not self.installation_operator_id or not self.reason:
                raise InvalidActorContext("installation operator identity and reason are required")
            if any(
                value is not None
                for value in (
                    self.user_id,
                    self.membership_revision,
                    self.auth_epoch,
                    self.agent_id,
                    self.agent_authorization_id,
                    self.agent_authorization_revision,
                    self.expires_at,
                )
            ) or self.roles or self.scopes:
                raise InvalidActorContext(
                    "installation operator cannot carry user or agent authority"
                )
            return

        if self.user_id is None or self.membership_revision is None or self.auth_epoch is None:
            raise InvalidActorContext(
                "user identity, membership revision, and auth epoch are required"
            )
        if self.membership_revision < 0 or self.auth_epoch < 0:
            raise InvalidActorContext("membership revision and auth epoch must be non-negative")
        if not self.roles:
            raise InvalidActorContext("authenticated user must have at least one closed role")

        if self.actor_type == "user":
            if any(
                value is not None
                for value in (
                    self.agent_id,
                    self.agent_authorization_id,
                    self.agent_authorization_revision,
                    self.expires_at,
                )
            ) or self.scopes:
                raise InvalidActorContext("user actor cannot carry agent authorization fields")
            return

        if (
            self.agent_id is None
            or self.agent_authorization_id is None
            or self.agent_authorization_revision is None
        ):
            raise InvalidActorContext("agent identity and authorization revision are required")
        if self.agent_authorization_revision < 0:
            raise InvalidActorContext("agent authorization revision must be non-negative")
        if not self.scopes:
            raise InvalidActorContext("agent authorization must contain at least one closed scope")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise InvalidActorContext("agent authorization expiry must be timezone-aware")

    @classmethod
    def user(
        cls,
        *,
        organization_id: UUID,
        user_id: UUID,
        roles: Iterable[Role],
        membership_revision: int,
        auth_epoch: int,
    ) -> RequestActor:
        return cls(
            organization_id=organization_id,
            actor_type="user",
            user_id=user_id,
            roles=frozenset(roles),
            membership_revision=membership_revision,
            auth_epoch=auth_epoch,
        )

    @classmethod
    def agent(
        cls,
        *,
        organization_id: UUID,
        user_id: UUID,
        roles: Iterable[Role],
        membership_revision: int,
        auth_epoch: int,
        agent_id: UUID,
        agent_authorization_id: UUID,
        agent_authorization_revision: int,
        scopes: Iterable[AgentScope],
        expires_at: datetime | None = None,
    ) -> RequestActor:
        return cls(
            organization_id=organization_id,
            actor_type="agent",
            user_id=user_id,
            roles=frozenset(roles),
            membership_revision=membership_revision,
            auth_epoch=auth_epoch,
            agent_id=agent_id,
            agent_authorization_id=agent_authorization_id,
            agent_authorization_revision=agent_authorization_revision,
            scopes=frozenset(scopes),
            expires_at=expires_at,
        )

    @classmethod
    def installation_operator(
        cls,
        *,
        organization_id: UUID,
        installation_operator_id: str,
        reason: str,
    ) -> RequestActor:
        return cls(
            organization_id=organization_id,
            actor_type="installation_operator",
            installation_operator_id=installation_operator_id,
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class AuthVersionSnapshot:
    """Current authority read by a guard, optionally under database locks."""

    organization_id: UUID
    user_id: UUID
    roles: frozenset[Role]
    membership_revision: int
    auth_epoch: int
    active: bool
    agent_authorization_id: UUID | None = None
    agent_authorization_revision: int | None = None
    agent_scopes: frozenset[AgentScope] = frozenset()
    agent_expires_at: datetime | None = None
    agent_active: bool = True


class AuthVersionGuard(Protocol):
    """Persistence-independent boundary for authority epoch revalidation.

    Concrete membership and AgentAuthorization repositories implement this in
    later tasks.  ``lock_and_revalidate`` must acquire their locks in the
    documented fixed order and keep them until the caller commits or rolls back.
    """

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        """Read current authorization state for an early fail-fast check."""

        ...

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        """Lock and return current authorization state immediately before commit."""

        ...
