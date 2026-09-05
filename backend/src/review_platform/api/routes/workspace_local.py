"""Explicit local fixture login issuing ordinary revocable server sessions."""

import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from review_platform.api.routes.workspace import WorkspaceRoute
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.workspace.common import WorkspaceFailure
from review_platform.contracts.workspace import (
    LocalIdentities,
    LocalIdentity,
    LocalLoginInput,
    ProfileView,
)
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.models import OrganizationMembership, Session, User

router = APIRouter(prefix="/v1/auth/local", tags=["local-fixtures"], route_class=WorkspaceRoute)


def local_runtime(request: Request) -> FoundationRuntime:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    if (
        not isinstance(runtime, FoundationRuntime)
        or runtime.settings.environment != "local"
        or not runtime.settings.workspace_fixtures
    ):
        raise WorkspaceFailure("not_found", "Локальный вход отключён.", 404)
    # Compose binds API to loopback; also reject cross-site login requests before a cookie exists.
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        allowed = set(
            filter(None, os.environ.get("REVIEW_PLATFORM_BROWSER_ORIGINS", "").split(","))
        )
        if origin not in allowed and urlsplit(origin).netloc != request.headers.get("host"):
            raise WorkspaceFailure("origin_mismatch", "Недопустимый источник запроса.", 403)
    return runtime


@router.get("/identities", response_model=LocalIdentities)
async def local_identities(request: Request) -> LocalIdentities:
    from review_platform.application.workspace.local_fixture import IDENTITIES

    runtime = getattr(request.app.state, "foundation_runtime", None)
    enabled = (
        isinstance(runtime, FoundationRuntime)
        and runtime.settings.environment == "local"
        and runtime.settings.workspace_fixtures
    )
    return LocalIdentities(
        enabled=enabled,
        items=[LocalIdentity(key=k, label=label, roles=[role]) for k, label, role in IDENTITIES]
        if enabled
        else [],
    )


@router.post("/login", response_model=ProfileView)
async def local_login(request: Request, response: Response, body: LocalLoginInput) -> ProfileView:
    from review_platform.application.workspace.local_fixture import IDENTITIES, fixture_id

    runtime = local_runtime(request)
    if body.identity not in {item[0] for item in IDENTITIES}:
        raise WorkspaceFailure("unknown_identity", "Выберите локального участника.", 422)
    async with runtime.transaction() as session:
        member = await session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == fixture_id("org"),
                OrganizationMembership.user_id == fixture_id(body.identity),
                OrganizationMembership.status == "active",
            )
        )
        if member is None:
            raise WorkspaceFailure(
                "fixture_not_seeded", "Сначала выполните локальную инициализацию.", 409
            )
        user = await session.get(User, member.user_id)
        assert user is not None
        secret = secrets.token_urlsafe(48)
        session.add(
            Session(
                id=runtime.id_factory(),
                organization_id=member.organization_id,
                user_id=member.user_id,
                membership_id=member.id,
                membership_revision=member.revision,
                auth_epoch=member.auth_epoch,
                token_digest=sha256_digest(secret),
                expires_at=runtime.clock() + timedelta(hours=8),
                status="active",
            )
        )
        response.set_cookie(
            "review_session",
            secret,
            httponly=True,
            samesite="lax",
            path="/api",
            max_age=28800,
            secure=request.url.scheme == "https",
        )
        return ProfileView(user_id=user.id, display_name=user.display_name)
