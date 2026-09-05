"""Resolve an MCP bearer into a server-owned agent actor snapshot."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, cast

from review_platform.application.request_context import (
    KNOWN_AGENT_SCOPES,
    KNOWN_ROLES,
    AgentScope,
    InvalidActorContext,
    RequestActor,
    Role,
)
from review_platform.domain.primitives import require_utc, utc_now
from review_platform.infrastructure.auth.agent_tokens import (
    AgentTokenError,
    digest_agent_token,
    verify_agent_token,
)
from review_platform.infrastructure.db.repositories.agents import (
    LockedAgentAuthority,
    SqlAgentAuthorizationRepository,
)

_BEARER_HEADER = re.compile(r"^Bearer ([^\s,]+)$", re.IGNORECASE)
_GENERIC_FAILURE = "invalid agent bearer credentials"


class MCPAuthenticationError(RuntimeError):
    """A generic credential failure that never carries presented token material."""

    def __init__(self) -> None:
        super().__init__(_GENERIC_FAILURE)


class ActiveAgentAuthorityResolver(Protocol):
    async def resolve_active_digest(
        self,
        token_digest: str,
        *,
        now: datetime,
        transaction: object,
    ) -> LockedAgentAuthority | None: ...


class MCPBearerAuthenticator:
    """Authenticate from the bearer alone; no tenant hint is accepted or trusted."""

    def __init__(
        self,
        repository: ActiveAgentAuthorityResolver | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository or SqlAgentAuthorizationRepository()
        self._clock = clock

    async def resolve(
        self,
        authorization_header: str | None,
        *,
        transaction: object,
    ) -> RequestActor:
        token = _parse_bearer_header(authorization_header)
        try:
            token_digest = digest_agent_token(token)
        except (AgentTokenError, TypeError) as error:
            raise MCPAuthenticationError from error

        now = require_utc(self._clock())
        authority = await self._repository.resolve_active_digest(
            token_digest,
            now=now,
            transaction=transaction,
        )
        if authority is None:
            raise MCPAuthenticationError
        authorization = authority.authorization
        roles = tuple(authority.roles)
        scopes = tuple(authorization.scopes)
        try:
            expires_at = require_utc(authorization.expires_at)
        except (TypeError, ValueError) as error:
            raise MCPAuthenticationError from error
        if (
            authority.organization_id != authorization.organization_id
            or authority.user_id != authorization.user_id
            or authority.membership_status != "active"
            or authority.membership_revision != authorization.membership_revision
            or authority.auth_epoch != authorization.auth_epoch
            or authorization.status != "active"
            or authorization.revoked_at is not None
            or authorization.revision < 0
            or expires_at <= now
            or not roles
            or not scopes
            or len(set(roles)) != len(roles)
            or len(set(scopes)) != len(scopes)
            or set(roles).difference(KNOWN_ROLES)
            or set(scopes).difference(KNOWN_AGENT_SCOPES)
            or not verify_agent_token(token, authorization.token_digest)
        ):
            raise MCPAuthenticationError
        try:
            return RequestActor.agent(
                organization_id=authority.organization_id,
                user_id=authority.user_id,
                roles=cast(tuple[Role, ...], roles),
                membership_revision=authority.membership_revision,
                auth_epoch=authority.auth_epoch,
                agent_id=authorization.agent_id,
                agent_authorization_id=authorization.authorization_id,
                agent_authorization_revision=authorization.revision,
                scopes=cast(tuple[AgentScope, ...], scopes),
                expires_at=expires_at,
            )
        except InvalidActorContext as error:
            raise MCPAuthenticationError from error


async def resolve_mcp_bearer(
    authorization_header: str | None,
    *,
    transaction: object,
    repository: ActiveAgentAuthorityResolver | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> RequestActor:
    """Functional entrypoint for the stateless MCP server."""

    return await MCPBearerAuthenticator(repository, clock=clock).resolve(
        authorization_header,
        transaction=transaction,
    )


def _parse_bearer_header(value: str | None) -> str:
    if not isinstance(value, str):
        raise MCPAuthenticationError
    matched = _BEARER_HEADER.fullmatch(value)
    if matched is None:
        raise MCPAuthenticationError
    return matched.group(1)


__all__ = [
    "ActiveAgentAuthorityResolver",
    "MCPAuthenticationError",
    "MCPBearerAuthenticator",
    "resolve_mcp_bearer",
]
