"""HTTP router integration tests for the frozen US1 surface."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from fastapi import Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.auth_guards.membership import UserMembershipAuthGuard
from review_platform.application.authorization import AuthorizationDenied
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.authentication import SessionAuthenticationResult
from review_platform.application.services.invitations import InvitationView
from review_platform.application.services.memberships import MembershipChangeResult
from review_platform.infrastructure.db.models import (
    CommandReceipt,
    Operation,
    OrganizationMembership,
    OutboxMessage,
    User,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000000701")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000000702")
COURSE = UUID("00000000-0000-7000-8000-000000000703")
RUN = UUID("00000000-0000-7000-8000-000000000704")
INVITATION = UUID("00000000-0000-7000-8000-000000000705")
OPERATION = UUID("00000000-0000-7000-8000-000000000706")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class ProbeScalarResult:
    def scalar_one_or_none(self) -> int:
        return 3


class ProbeTransaction:
    async def execute(self, _statement: object) -> ProbeScalarResult:
        return ProbeScalarResult()


class RecordingGuard:
    def __init__(self) -> None:
        self.fail_early = False
        self.fail_final = False
        self.early_calls = 0
        self.final_calls = 0

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.early_calls += 1
        if self.fail_early:
            raise AuthorizationDenied("stale actor")
        return self._snapshot(actor)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        self.final_calls += 1
        if self.fail_final:
            raise AuthorizationDenied("actor revoked before commit")
        return self._snapshot(actor)

    @staticmethod
    def _snapshot(actor: RequestActor) -> AuthVersionSnapshot:
        assert actor.user_id is not None
        assert actor.membership_revision is not None
        assert actor.auth_epoch is not None
        return AuthVersionSnapshot(
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            roles=actor.roles,
            membership_revision=actor.membership_revision,
            auth_epoch=actor.auth_epoch,
            active=True,
        )


class ProbeRuntime(FoundationRuntime):
    def __init__(self) -> None:
        self._counter = 800
        self.guard = RecordingGuard()
        self._settings_value = Settings()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, ProbeTransaction())

    @property
    def id_factory(self) -> Callable[[], UUID]:
        def create() -> UUID:
            self._counter += 1
            return UUID(f"00000000-0000-7000-8000-{self._counter:012d}")

        return create

    @property
    def clock(self) -> Callable[[], datetime]:
        return lambda: NOW

    @property
    def worker_auth_revalidator(self) -> Any:
        return self.guard

    @property
    def user_auth_guard(self) -> Any:
        return self.guard

    @property
    def settings(self) -> Settings:
        return self._settings_value

    async def close(self) -> None:
        return None


class DatabaseRouteRuntime(ProbeRuntime):
    def __init__(self, factory: AsyncSessionFactory) -> None:
        super().__init__()
        self.guard = UserMembershipAuthGuard(factory)
        self._factory = factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with session_scope(self._factory) as session:
            yield session


class ProbeCourseService:
    async def list_courses(self, **_: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(id=COURSE, title="Course", status="active", revision=2)]

    async def list_course_runs(self, **_: object) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=RUN,
                course_id=COURSE,
                title="Run",
                timezone="Europe/Moscow",
                status="active",
                revision=3,
            )
        ]

    async def read_roster(self, **_: object) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                user_id=USER,
                kind="reviewer",
                status="active",
                source="invitation",
            )
        ]

    async def archive_course(self, **_: object) -> None:
        return None

    async def restore_course(self, **_: object) -> None:
        return None

    async def archive_course_run(self, **_: object) -> None:
        return None

    async def restore_course_run(self, **_: object) -> None:
        return None


class ProbeInvitationService:
    async def issue(self, **_: object) -> InvitationView:
        return InvitationView(
            invitation_id=INVITATION,
            organization_id=ORG,
            normalized_email="reviewer@example.test",
            role="reviewer",
            status="active",
            revision=0,
            expires_at=NOW,
        )

    async def revoke(self, **_: object) -> None:
        return None


class ProbeMembershipService:
    async def change_roles(self, **_: object) -> MembershipChangeResult:
        return MembershipChangeResult(
            membership_id=MEMBERSHIP,
            organization_id=ORG,
            user_id=USER,
            roles=("methodologist", "reviewer"),
            revision=4,
            auth_epoch=5,
            revoked_sessions=1,
            revoked_agent_authorizations=1,
            invalidated_receipts=1,
        )


class ProbeAuthenticationService:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_stepik_oauth(self, **_: object) -> SessionAuthenticationResult:
        self.calls += 1
        return SessionAuthenticationResult(
            organization_id=ORG,
            session_id=UUID("00000000-0000-7000-8000-000000000707"),
            user_id=USER,
            membership_id=MEMBERSHIP,
            membership_revision=3,
            auth_epoch=2,
            roles=("methodologist",),
            expires_at=NOW,
            replayed=self.calls > 1,
            session_secret=("s" * 48) if self.calls == 1 else None,
        )


@pytest.fixture
def route_app() -> Any:
    app = create_app(Settings(), runtime=ProbeRuntime())
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist", "reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )

    @app.middleware("http")
    async def inject_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    app.state.installation_organization_id = ORG
    app.state.course_import_credential_binding_id = UUID(
        "00000000-0000-7000-8000-000000000708"
    )
    app.state.course_import_credential_binding_version = 1
    app.state.course_service_factory = lambda _session: ProbeCourseService()
    app.state.invitation_service_factory = lambda _session: ProbeInvitationService()
    app.state.membership_service_factory = lambda _session: ProbeMembershipService()
    return app


@pytest.fixture
async def route_client(route_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=route_app),
        base_url="http://test",
    ) as client:
        yield client


def _command(
    name: str,
    revision_target: str,
    target: UUID,
    payload: dict[str, object],
    *,
    expected_revision: int = 3,
) -> dict[str, object]:
    return {
        "request_id": "00000000-0000-7000-8000-000000000709",
        "idempotency_key": f"route-{name}-fixture-0001",
        "command_name": name,
        "revision_target": revision_target,
        "target_id": str(target),
        "expected_revision": expected_revision,
        "payload": payload,
    }


def test_registry_contains_exact_us1_paths_operation_ids_and_no_operator_surface(
    route_app: Any,
) -> None:
    operations = {
        (route.path, method): route.operation_id
        for route in route_app.routes
        if hasattr(route, "methods") and hasattr(route, "operation_id")
        for method in route.methods
    }
    expected = {
        ("/api/v1/auth/stepik/start", "GET"): "startStepikAuthentication",
        ("/api/v1/auth/stepik/callback", "POST"): "completeStepikAuthentication",
        ("/api/v1/auth/reviewer/magic-link", "POST"): "consumeReviewerMagicLink",
        ("/api/v1/session", "GET"): "getCurrentSession",
        ("/api/v1/session", "DELETE"): "revokeCurrentSession",
        ("/api/v1/organization", "GET"): "getOrganization",
        ("/api/v1/courses", "GET"): "listCourses",
        ("/api/v1/course-runs", "GET"): "listCourseRuns",
        ("/api/v1/organization/memberships", "GET"): "listOrganizationMemberships",
        ("/api/v1/invitations", "GET"): "listInvitations",
        ("/api/v1/invitations", "POST"): "createInvitation",
        ("/api/v1/invitations/{invitationId}/revoke", "POST"): "revokeInvitation",
        ("/api/v1/courses/imports", "POST"): "startCourseImport",
        ("/api/v1/course-runs/{courseRunId}/memberships", "GET"): (
            "listCourseRunMemberships"
        ),
        ("/api/v1/course-runs/{courseRunId}/archive", "POST"): "archiveCourseRun",
        ("/api/v1/course-runs/{courseRunId}/restore", "POST"): "restoreCourseRun",
        ("/api/v1/courses/{courseId}/archive", "POST"): "archiveCourse",
        ("/api/v1/courses/{courseId}/restore", "POST"): "restoreCourse",
        ("/api/v1/memberships/{membershipId}/roles", "PUT"): "changeMembershipRoles",
    }
    assert expected.items() <= operations.items()
    assert not any(
        marker in path.casefold()
        for path, _method in operations
        for marker in ("bootstrap", "recover", "operator")
    )


async def test_typed_course_run_and_roster_reads(route_client: httpx.AsyncClient) -> None:
    courses = await route_client.get("/api/v1/courses")
    runs = await route_client.get("/api/v1/course-runs", params={"course_id": str(COURSE)})
    roster = await route_client.get(f"/api/v1/course-runs/{RUN}/memberships")

    assert courses.status_code == runs.status_code == roster.status_code == 200
    assert courses.json()["items"] == [
        {"id": str(COURSE), "title": "Course", "status": "active", "revision": 2}
    ]
    assert runs.json()["items"][0]["course_id"] == str(COURSE)
    assert roster.json() == {
        "items": [
            {
                "user_id": str(USER),
                "kind": "reviewer",
                "status": "active",
                "source": "invitation",
            }
        ]
    }


async def test_typed_invitation_and_membership_results(
    route_client: httpx.AsyncClient,
) -> None:
    invitation = await route_client.post(
        "/api/v1/invitations",
        json=_command(
            "create_invitation",
            "organization",
            ORG,
            {
                "email": "reviewer@example.test",
                "role": "reviewer",
                "expires_at": "2026-09-05T12:00:00Z",
            },
        ),
    )
    membership = await route_client.put(
        f"/api/v1/memberships/{MEMBERSHIP}/roles",
        json=_command(
            "change_membership_roles",
            "membership",
            MEMBERSHIP,
            {"roles": ["methodologist", "reviewer"]},
        ),
    )
    assert invitation.status_code == 201
    assert invitation.json() == {"id": str(INVITATION), "revision": 0}
    assert membership.status_code == 200
    assert membership.json() == {"id": str(MEMBERSHIP), "revision": 4}


async def test_business_route_rejects_command_and_path_mismatch_with_closed_error(
    route_client: httpx.AsyncClient,
) -> None:
    wrong_command = await route_client.post(
        f"/api/v1/courses/{COURSE}/archive",
        json=_command("restore_course", "course", COURSE, {}),
    )
    wrong_target = await route_client.post(
        f"/api/v1/courses/{COURSE}/archive",
        json=_command(
            "archive_course",
            "course",
            UUID("00000000-0000-7000-8000-000000000799"),
            {"reason": "done"},
        ),
    )
    client_context = _command(
        "archive_course", "course", COURSE, {"reason": "done"}
    )
    client_context["organization_id"] = str(ORG)
    forged_context = await route_client.post(
        f"/api/v1/courses/{COURSE}/archive",
        json=client_context,
    )

    for response in (wrong_command, wrong_target, forged_context):
        assert response.status_code == 409
        assert set(response.json()) == {"code", "message", "action"}


async def test_protocols_fail_closed_without_exact_cookie_or_magic_link_identity(
    route_client: httpx.AsyncClient,
) -> None:
    session = await route_client.get("/api/v1/session")
    magic_link = await route_client.post(
        "/api/v1/auth/reviewer/magic-link",
        json={"token": "short", "state_id": "not-a-uuid"},
    )

    assert session.status_code == magic_link.status_code == 401
    assert set(session.json()) == {"code", "message", "action"}
    assert set(magic_link.json()) == {"code", "message", "action"}


async def test_callback_sets_session_secret_once(route_app: Any) -> None:
    authentication = ProbeAuthenticationService()
    route_app.state.authentication_service_factory = lambda _session: authentication
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=route_app),
        base_url="http://test",
    ) as client:
        first = await client.post(
            "/api/v1/auth/stepik/callback",
            params={"state": "state-secret", "code": "authorization-code"},
        )
        replay = await client.post(
            "/api/v1/auth/stepik/callback",
            params={"state": "state-secret", "code": "authorization-code"},
        )

    assert first.status_code == replay.status_code == 204
    assert "review_session=" in first.headers["set-cookie"]
    assert "HttpOnly" in first.headers["set-cookie"]
    assert "Secure" in first.headers["set-cookie"]
    assert "set-cookie" not in replay.headers


async def test_direct_read_revalidates_and_rejects_stale_actor(
    route_app: Any,
) -> None:
    runtime = cast(ProbeRuntime, route_app.state.foundation_runtime)
    runtime.guard.fail_early = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=route_app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/organization")

    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"
    assert runtime.guard.early_calls == 1


async def test_direct_invitation_mutation_rejects_revocation_before_commit(
    route_app: Any,
) -> None:
    runtime = cast(ProbeRuntime, route_app.state.foundation_runtime)
    runtime.guard.fail_final = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=route_app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/v1/invitations/{INVITATION}/revoke",
            json=_command(
                "revoke_invitation",
                "invitation",
                INVITATION,
                {"reason": "revoke"},
            ),
        )

    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"
    assert runtime.guard.early_calls == 1
    assert runtime.guard.final_calls == 1


@pytest.mark.infrastructure
async def test_course_import_reserves_pending_operation_and_one_outbox_without_provider(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    async with session_scope(foundation_session_factory) as session:
        session.add(User(id=USER, display_name="Route User", status="active"))
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP,
                organization_id=ORG,
                user_id=USER,
                roles=["methodologist"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )

    runtime = DatabaseRouteRuntime(foundation_session_factory)
    app = create_app(Settings(), runtime=runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist"},
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    app.state.course_import_credential_binding_id = UUID(
        "00000000-0000-7000-8000-000000000708"
    )
    app.state.course_import_credential_binding_version = 7

    def provider_must_not_be_resolved(_session: object) -> None:
        raise AssertionError("HTTP route must not resolve or call the import provider")

    app.state.course_import_service_factory = provider_must_not_be_resolved
    command = _command(
        "start_course_import",
        "organization",
        ORG,
        {"provider": "stepik", "external_url": "https://stepik.org/course/1"},
        expected_revision=0,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.post("/api/v1/courses/imports", json=command)
        replay = await client.post("/api/v1/courses/imports", json=command)

    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert first.json()["state"] == "pending"
    assert first.json()["attempts"] == []
    operation_id = UUID(first.json()["id"])
    async with foundation_session_factory() as session:
        counts = {
            "operations": await session.scalar(select(func.count()).select_from(Operation)),
            "outbox": await session.scalar(select(func.count()).select_from(OutboxMessage)),
            "receipts": await session.scalar(select(func.count()).select_from(CommandReceipt)),
        }
        operation = (
            await session.execute(
                select(Operation).where(
                    Operation.organization_id == ORG,
                    Operation.id == operation_id,
                )
            )
        ).scalar_one()
        message = (await session.execute(select(OutboxMessage))).scalar_one()

    assert counts == {"operations": 1, "outbox": 1, "receipts": 1}
    assert operation.state == "pending"
    assert message.aggregate_id == operation.id
    assert message.event_type == "CourseImportRequested"
    assert message.payload == {
        "contract_version": "1.1.0",
        "operation_id": str(operation.id),
        "credential_binding_id": "00000000-0000-7000-8000-000000000708",
        "credential_binding_version": 7,
        "provider": "stepik",
        "external_url": "https://stepik.org/course/1",
        "cursor": None,
        "page_size": 100,
        "course_binding_version": 1,
        "actor": {
            "type": "user",
            "user_id": str(USER),
            "roles": ["methodologist"],
            "membership_revision": 0,
            "auth_epoch": 0,
        },
    }
