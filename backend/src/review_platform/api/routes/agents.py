"""Interactive session-only AgentAuthorization grant and revoke routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import AuthorizationError, Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import AgentScope, RequestActor, Role
from review_platform.application.services.agent_authorizations import (
    AgentAuthorizationService,
    AgentAuthorizationServiceError,
    MembershipAuthority,
    MembershipAuthorityRepository,
)
from review_platform.contracts.commands import (
    GrantAgentAuthorizationPayload,
    ReasonPayload,
    WireCommand,
)
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.identity import OrganizationMembership
from review_platform.infrastructure.db.repositories.agents import (
    SqlAgentAuthorizationRepository,
)
from review_platform.infrastructure.db.repositories.operations import CommandReceiptRepository

router = APIRouter(tags=["agents"])


class AgentRouteError(RuntimeError):
    """Typed transport/composition failure for agent authorization routes."""


class SqlMembershipAuthorityRepository(MembershipAuthorityRepository):
    async def lock_membership(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        transaction: object,
    ) -> MembershipAuthority | None:
        session = _session(transaction)
        row = await session.scalar(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.id == membership_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        return MembershipAuthority(
            organization_id=row.organization_id,
            membership_id=row.id,
            user_id=row.user_id,
            roles=frozenset(cast(list[Role], row.roles)),
            status=row.status,
            revision=row.revision,
            auth_epoch=row.auth_epoch,
        )


@router.post(
    "/v1/memberships/{membershipId}/agent-authorizations",
    operation_id="grantAgentAuthorization",
    status_code=201,
)
async def grant_agent_authorization(
    membershipId: UUID,
    request: Request,
    body: Mapping[str, Any],
) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "grant_agent_authorization", membershipId)
        payload = cast(GrantAgentAuthorizationPayload, command.payload)
        async with runtime.transaction() as transaction:
            result = await _service(runtime).grant(
                transaction=transaction,
                organization_id=actor.organization_id,
                membership_id=membershipId,
                expected_membership_revision=command.expected_revision,
                agent_id=payload.agent_id,
                scopes=cast(list[AgentScope], payload.scopes),
                expires_at=payload.expires_at,
                idempotency_key=command.idempotency_key,
                actor=actor,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await _bind_receipt_actor(transaction, actor, command)
            await runtime.user_auth_guard.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
            if result.replayed or result.access_token is None:
                raise AgentRouteError(
                    "agent authorization already exists; bearer secret cannot be replayed"
                )
            authorization = result.authorization
            response = {
                "id": str(authorization.authorization_id),
                "agent_id": str(authorization.agent_id),
                "scopes": list(authorization.scopes),
                "expires_at": authorization.expires_at.isoformat(),
                "revision": authorization.revision,
                "access_token": result.access_token.reveal(),
            }
        return JSONResponse(response, status_code=201)
    except AuthorizationError as error:
        return _error(403, "agent_authorization_forbidden", str(error))
    except (AgentAuthorizationServiceError, AgentRouteError, ValidationError, ValueError) as error:
        return _error(409, "agent_authorization_conflict", str(error))


@router.post(
    "/v1/agent-authorizations/{agentAuthorizationId}/revoke",
    operation_id="revokeAgentAuthorization",
    status_code=204,
)
async def revoke_agent_authorization(
    agentAuthorizationId: UUID,
    request: Request,
    body: Mapping[str, Any],
) -> Response:
    try:
        runtime, actor = _context(request)
        command = _wire(body, "revoke_agent_authorization", agentAuthorizationId)
        payload = cast(ReasonPayload, command.payload)
        async with runtime.transaction() as transaction:
            await _service(runtime).revoke(
                transaction=transaction,
                organization_id=actor.organization_id,
                authorization_id=agentAuthorizationId,
                expected_authorization_revision=command.expected_revision,
                reason=payload.reason,
                idempotency_key=command.idempotency_key,
                actor=actor,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await _bind_receipt_actor(transaction, actor, command)
            await runtime.user_auth_guard.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
        return Response(status_code=204)
    except AuthorizationError as error:
        return _error(403, "agent_authorization_forbidden", str(error))
    except (AgentAuthorizationServiceError, AgentRouteError, ValidationError, ValueError) as error:
        return _error(409, "agent_authorization_conflict", str(error))


def _service(runtime: FoundationRuntime) -> AgentAuthorizationService:
    return AgentAuthorizationService(
        membership_repository=SqlMembershipAuthorityRepository(),
        authorization_repository=SqlAgentAuthorizationRepository(),
        receipt_repository=SqlIdempotencyReceiptRepository(),
        authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
        audit=AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=runtime.id_factory,
            clock=runtime.clock,
        ),
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


def _context(request: Request) -> tuple[FoundationRuntime, RequestActor]:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(runtime, FoundationRuntime):
        raise AgentRouteError("Foundation runtime is not configured")
    if not isinstance(actor, RequestActor):
        raise AuthorizationError("interactive authenticated session is required")
    if actor.actor_type != "user":
        raise AuthorizationError("interactive user session is required")
    return runtime, actor


def _wire(body: Mapping[str, Any], name: str, target_id: UUID) -> WireCommand:
    command = WireCommand.model_validate(dict(body))
    if str(command.command_name) != name or command.target_id != target_id:
        raise AgentRouteError("route command or path target mismatched")
    return command


async def _bind_receipt_actor(
    transaction: AsyncSession,
    actor: RequestActor,
    command: WireCommand,
) -> None:
    repository = CommandReceiptRepository(transaction)
    receipt = await repository.get_by_idempotency_key(
        actor.organization_id,
        command.idempotency_key,
        for_update=True,
    )
    if receipt is None:
        raise AgentRouteError("agent command receipt disappeared")
    snapshot = _actor_snapshot(actor)
    if not receipt.actor_snapshot:
        receipt.actor_snapshot = snapshot
        await transaction.flush([receipt])
    elif receipt.actor_snapshot != snapshot:
        raise AgentRouteError("idempotency replay actor snapshot mismatched")


def _actor_snapshot(actor: RequestActor) -> dict[str, Any]:
    return {
        "type": actor.actor_type,
        "organization_id": str(actor.organization_id),
        "user_id": str(actor.user_id) if actor.user_id is not None else None,
        "roles": sorted(str(role) for role in actor.roles),
        "membership_revision": actor.membership_revision,
        "auth_epoch": actor.auth_epoch,
        "agent_id": None,
        "agent_authorization_id": None,
        "agent_authorization_revision": None,
        "scopes": [],
        "expires_at": None,
    }


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise AgentRouteError("agent authorization requires caller-owned AsyncSession")
    return transaction


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "action": "refresh_and_retry"},
        status_code=status,
    )


__all__ = [
    "SqlMembershipAuthorityRepository",
    "grant_agent_authorization",
    "revoke_agent_authorization",
    "router",
]
