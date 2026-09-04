"""Fail-closed role, scope, tenant, and revocation authorization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from review_platform.application.request_context import (
    KNOWN_AGENT_SCOPES,
    KNOWN_ROLES,
    ActorType,
    AgentScope,
    AuthVersionGuard,
    AuthVersionSnapshot,
    RequestActor,
    Role,
)


class AuthorizationError(PermissionError):
    """Base class for typed authorization failures."""


class InvalidAuthorizationPolicy(AuthorizationError):
    """A caller requested an unknown role, scope, or actor kind."""


class AuthorizationDenied(AuthorizationError):
    """The current authenticated actor does not satisfy a closed policy."""


@dataclass(frozen=True, slots=True)
class AuthorizationPolicy:
    """Closed-world application authorization requirements.

    ``required_roles`` is an OR set.  Scopes restrict agent actors; interactive
    user actors are governed by roles and the command actor policy instead.
    """

    required_roles: frozenset[Role] = frozenset()
    required_scopes: frozenset[AgentScope] = frozenset()
    allowed_actor_types: frozenset[ActorType] = frozenset({"user", "agent"})

    def __post_init__(self) -> None:
        unknown_roles = set(self.required_roles).difference(KNOWN_ROLES)
        if unknown_roles:
            raise InvalidAuthorizationPolicy(f"unknown required role(s): {unknown_roles!r}")
        unknown_scopes = set(self.required_scopes).difference(KNOWN_AGENT_SCOPES)
        if unknown_scopes:
            raise InvalidAuthorizationPolicy(f"unknown required scope(s): {unknown_scopes!r}")
        known_actor_types: frozenset[ActorType] = frozenset(
            {"user", "agent", "installation_operator"}
        )
        unknown_actor_types = set(self.allowed_actor_types).difference(known_actor_types)
        if unknown_actor_types:
            raise InvalidAuthorizationPolicy(
                f"unknown allowed actor type(s): {unknown_actor_types!r}"
            )
        if not self.allowed_actor_types:
            raise InvalidAuthorizationPolicy("authorization policy must allow an actor type")


@dataclass(frozen=True, slots=True)
class AuthorizationGrant:
    """Early authorization result that still requires the final locked check."""

    actor: RequestActor
    policy: AuthorizationPolicy


class Authorizer:
    """Authorize current context and repeat the same checks under commit locks."""

    def __init__(
        self,
        guard: AuthVersionGuard,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._guard = guard
        self._clock = clock or (lambda: datetime.now(UTC))

    async def authorize(
        self,
        *,
        actor: RequestActor,
        organization_id: UUID,
        policy: AuthorizationPolicy,
    ) -> AuthorizationGrant:
        if actor.organization_id != organization_id:
            raise AuthorizationDenied("actor tenant does not match command tenant")
        self._check_actor_policy(actor, policy)
        if actor.actor_type != "installation_operator":
            snapshot = await self._guard.revalidate(actor=actor)
            self._check_snapshot(actor, policy, snapshot)
        return AuthorizationGrant(actor=actor, policy=policy)

    async def revalidate_for_commit(
        self, grant: AuthorizationGrant, *, transaction: object
    ) -> None:
        """Lock authority rows and fail before the transaction can commit."""

        if grant.actor.actor_type == "installation_operator":
            self._check_actor_policy(grant.actor, grant.policy)
            return
        snapshot = await self._guard.lock_and_revalidate(
            actor=grant.actor,
            transaction=transaction,
        )
        self._check_snapshot(grant.actor, grant.policy, snapshot)

    def _check_actor_policy(
        self, actor: RequestActor, policy: AuthorizationPolicy
    ) -> None:
        if actor.actor_type not in policy.allowed_actor_types:
            raise AuthorizationDenied(f"actor type {actor.actor_type!r} is not allowed")
        if actor.actor_type == "installation_operator":
            if policy.required_roles or policy.required_scopes:
                raise AuthorizationDenied("installation operator cannot satisfy product authority")
            return
        if policy.required_roles and actor.roles.isdisjoint(policy.required_roles):
            raise AuthorizationDenied("actor lacks every required role")
        if actor.actor_type == "agent" and not policy.required_scopes.issubset(actor.scopes):
            raise AuthorizationDenied("agent authorization lacks a required scope")

    def _check_snapshot(
        self,
        actor: RequestActor,
        policy: AuthorizationPolicy,
        snapshot: AuthVersionSnapshot,
    ) -> None:
        if snapshot.organization_id != actor.organization_id:
            raise AuthorizationDenied("guard returned a different tenant")
        if snapshot.user_id != actor.user_id:
            raise AuthorizationDenied("guard returned a different represented user")
        if not snapshot.active:
            raise AuthorizationDenied("organization membership is not active")
        if set(snapshot.roles).difference(KNOWN_ROLES):
            raise AuthorizationDenied("guard returned an unknown role")
        if (
            snapshot.membership_revision != actor.membership_revision
            or snapshot.auth_epoch != actor.auth_epoch
        ):
            raise AuthorizationDenied("membership authorization version is stale")
        if snapshot.roles != actor.roles:
            raise AuthorizationDenied("membership role snapshot is stale")
        if policy.required_roles and snapshot.roles.isdisjoint(policy.required_roles):
            raise AuthorizationDenied("current membership lacks every required role")

        if actor.actor_type != "agent":
            return
        if not snapshot.agent_active:
            raise AuthorizationDenied("agent authorization is not active")
        if snapshot.agent_authorization_id != actor.agent_authorization_id:
            raise AuthorizationDenied("guard returned a different agent authorization")
        if snapshot.agent_authorization_revision != actor.agent_authorization_revision:
            raise AuthorizationDenied("agent authorization version is stale")
        if set(snapshot.agent_scopes).difference(KNOWN_AGENT_SCOPES):
            raise AuthorizationDenied("guard returned an unknown agent scope")
        if snapshot.agent_scopes != actor.scopes:
            raise AuthorizationDenied("agent scope snapshot is stale")
        if not policy.required_scopes.issubset(snapshot.agent_scopes):
            raise AuthorizationDenied("current agent authorization lacks a required scope")

        now = self._clock()
        expiry = snapshot.agent_expires_at or actor.expires_at
        if expiry is not None:
            if expiry.tzinfo is None:
                raise AuthorizationDenied("agent authorization expiry is not timezone-aware")
            if expiry <= now:
                raise AuthorizationDenied("agent authorization has expired")
