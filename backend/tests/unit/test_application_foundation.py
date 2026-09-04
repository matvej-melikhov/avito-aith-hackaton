from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from review_platform.application.audit import AuditEventDraft, AuditRecorder, sanitize_shared_value
from review_platform.application.authorization import (
    AuthorizationDenied,
    AuthorizationPolicy,
    Authorizer,
)
from review_platform.application.command_bus import (
    CommandActorMismatch,
    CommandBus,
    CommandNotRegistered,
    CommandRegistration,
    CommandTargetMismatch,
    CommandTypeMismatch,
    DuplicateCommandRegistration,
)
from review_platform.application.idempotency import (
    IdempotencyConflict,
    IdempotencyCoordinator,
    IdempotencyReceipt,
)
from review_platform.application.request_context import (
    AuthVersionSnapshot,
    InvalidActorContext,
    RequestActor,
)
from review_platform.contracts.commands import (
    ApplicationCommand,
    Transport,
    UserActor,
    WireCommand,
    build_application_command,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
USER = UUID("00000000-0000-7000-8000-000000000003")
TARGET = UUID("00000000-0000-7000-8000-000000000004")
AGENT = UUID("00000000-0000-7000-8000-000000000005")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000000006")
REQUEST = UUID("00000000-0000-7000-8000-000000000007")
TRACE = UUID("00000000-0000-7000-8000-000000000008")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class Guard:
    def __init__(self, snapshot: AuthVersionSnapshot, order: list[str] | None = None) -> None:
        self.snapshot = snapshot
        self.order = order if order is not None else []

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.order.append("authorize")
        return self.snapshot

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        self.order.append("auth-lock")
        return self.snapshot


def _user(*, organization_id: UUID = ORG) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )


def _snapshot(*, organization_id: UUID = ORG) -> AuthVersionSnapshot:
    return AuthVersionSnapshot(
        organization_id=organization_id,
        user_id=USER,
        roles=frozenset({"reviewer"}),
        membership_revision=3,
        auth_epoch=2,
        active=True,
    )


def test_request_actor_rejects_unknown_roles_and_scopes() -> None:
    with pytest.raises(InvalidActorContext, match="role"):
        RequestActor.user(
            organization_id=ORG,
            user_id=USER,
            roles={"administrator"},  # type: ignore[arg-type]
            membership_revision=1,
            auth_epoch=1,
        )

    with pytest.raises(InvalidActorContext, match="scope"):
        RequestActor.agent(
            organization_id=ORG,
            user_id=USER,
            roles={"reviewer"},
            membership_revision=1,
            auth_epoch=1,
            agent_id=AGENT,
            agent_authorization_id=AUTHORIZATION,
            agent_authorization_revision=1,
            scopes={"reviews:delete"},  # type: ignore[arg-type]
        )


@pytest.mark.anyio
async def test_authorization_fails_closed_on_tenant_mismatch() -> None:
    authorizer = Authorizer(Guard(_snapshot()))

    with pytest.raises(AuthorizationDenied, match="tenant"):
        await authorizer.authorize(
            actor=_user(),
            organization_id=OTHER_ORG,
            policy=AuthorizationPolicy(required_roles=frozenset({"reviewer"})),
        )


@dataclass(frozen=True)
class ForgedCommand:
    organization_id: UUID
    request_id: UUID
    idempotency_key: str
    command_name: str
    revision_target: str
    target_id: UUID
    expected_revision: int
    payload: Mapping[str, Any]


class Revisions:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def lock_and_check(
        self,
        *,
        organization_id: UUID,
        revision_target: str,
        target_id: UUID,
        expected_revision: int,
        transaction: object,
    ) -> None:
        self.order.append("revision-lock")


class Transactions:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[object]:
        self.order.append("begin")
        try:
            yield object()
        except BaseException:
            self.order.append("rollback")
            raise
        else:
            self.order.append("commit")


@pytest.mark.anyio
async def test_command_bus_checks_exact_target_and_revalidates_immediately_before_commit() -> None:
    order: list[str] = []
    actor = _user()
    guard = Guard(_snapshot(), order)

    async def handler(
        command: ApplicationCommand, actor: RequestActor, transaction: object
    ) -> str:
        order.append("handler")
        return "result:archive-course"

    bus = CommandBus(
        transactions=Transactions(order),
        revisions=Revisions(order),
        authorizer=Authorizer(guard),
        registrations={
            "archive_course": CommandRegistration(
                revision_target="course",
                policy=AuthorizationPolicy(required_roles=frozenset({"reviewer"})),
                handler=handler,
            )
        },
    )
    command = build_application_command(
        WireCommand.model_validate(
            {
                "request_id": REQUEST,
                "idempotency_key": "archive-course-fixture-0001",
                "command_name": "archive_course",
                "revision_target": "course",
                "target_id": TARGET,
                "expected_revision": 4,
                "payload": {"reason": "finished"},
            }
        ),
        organization_id=ORG,
        actor=UserActor(
            type="user",
            user_id=USER,
            membership_revision=3,
            auth_epoch=2,
        ),
        transport=Transport.REST,
        trace_id=TRACE,
    )

    with pytest.raises(CommandTargetMismatch):
        await bus.dispatch(
            command,
            actor=actor,
            route_command="archive_course",
            path_target_id=UUID("00000000-0000-7000-8000-000000000099"),
        )

    assert await bus.dispatch(
        command,
        actor=actor,
        route_command="archive_course",
        path_target_id=TARGET,
    ) == "result:archive-course"
    assert order == ["authorize", "begin", "revision-lock", "handler", "auth-lock", "commit"]


@pytest.mark.anyio
async def test_command_bus_rejects_forged_model_and_mismatched_server_actor() -> None:
    actor = _user()

    async def handler(
        command: ApplicationCommand, actor: RequestActor, transaction: object
    ) -> str:
        return "unreachable"

    bus = CommandBus(
        transactions=Transactions([]),
        revisions=Revisions([]),
        authorizer=Authorizer(Guard(_snapshot())),
        registrations={
            "archive_course": CommandRegistration(
                revision_target="course",
                policy=AuthorizationPolicy(required_roles=frozenset({"reviewer"})),
                handler=handler,
            )
        },
    )
    forged = ForgedCommand(
        organization_id=ORG,
        request_id=REQUEST,
        idempotency_key="archive-course-fixture-0001",
        command_name="archive_course",
        revision_target="course",
        target_id=TARGET,
        expected_revision=4,
        payload={"reason": "finished"},
    )
    with pytest.raises(CommandTypeMismatch, match="exact ApplicationCommand"):
        await bus.dispatch(forged, actor=actor)  # type: ignore[arg-type]

    command = build_application_command(
        WireCommand.model_validate(
            {
                "request_id": REQUEST,
                "idempotency_key": "archive-course-fixture-0001",
                "command_name": "archive_course",
                "revision_target": "course",
                "target_id": TARGET,
                "expected_revision": 4,
                "payload": {"reason": "finished"},
            }
        ),
        organization_id=ORG,
        actor=UserActor(type="user", user_id=USER, membership_revision=3, auth_epoch=2),
        transport=Transport.REST,
        trace_id=TRACE,
    )
    mismatched_actor = RequestActor.user(
        organization_id=ORG,
        user_id=UUID("00000000-0000-7000-8000-000000000099"),
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )
    with pytest.raises(CommandActorMismatch, match="server context"):
        await bus.dispatch(command, actor=mismatched_actor)


@pytest.mark.anyio
async def test_command_bus_empty_registry_fails_closed_and_registration_cannot_overwrite() -> None:
    actor = _user()

    async def handler(
        command: ApplicationCommand, actor: RequestActor, transaction: object
    ) -> str:
        return "registered"

    registration = CommandRegistration(
        revision_target="course",
        policy=AuthorizationPolicy(required_roles=frozenset({"reviewer"})),
        handler=handler,
    )
    bus = CommandBus(
        transactions=Transactions([]),
        revisions=Revisions([]),
        authorizer=Authorizer(Guard(_snapshot())),
    )
    command = build_application_command(
        WireCommand.model_validate(
            {
                "request_id": REQUEST,
                "idempotency_key": "archive-course-fixture-0001",
                "command_name": "archive_course",
                "revision_target": "course",
                "target_id": TARGET,
                "expected_revision": 4,
                "payload": {"reason": "finished"},
            }
        ),
        organization_id=ORG,
        actor=UserActor(type="user", user_id=USER, membership_revision=3, auth_epoch=2),
        transport=Transport.REST,
        trace_id=TRACE,
    )

    with pytest.raises(CommandNotRegistered, match="not registered"):
        await bus.dispatch(command, actor=actor)

    bus.register("archive_course", registration)
    with pytest.raises(DuplicateCommandRegistration, match="already"):
        bus.register("archive_course", registration)


class Receipts:
    def __init__(self) -> None:
        self.receipt: IdempotencyReceipt | None = None

    async def reserve(
        self, proposed: IdempotencyReceipt, *, transaction: object
    ) -> tuple[IdempotencyReceipt, bool]:
        if self.receipt is None:
            self.receipt = proposed
            return proposed, True
        return self.receipt, False


@pytest.mark.anyio
async def test_idempotency_replays_same_digest_and_rejects_same_key_conflict() -> None:
    coordinator = IdempotencyCoordinator(
        Receipts(),
        receipt_id_factory=lambda: UUID("00000000-0000-7000-8000-000000000010"),
        result_reference_factory=lambda: {"kind": "command_result", "id": "stable"},
    )
    first = await coordinator.reserve(
        organization_id=ORG,
        idempotency_key="stable-idempotency-key",
        request_id=REQUEST,
        command_name="archive_course",
        target_id=TARGET,
        expected_revision=4,
        payload={"reason": "finished"},
        transaction=object(),
    )
    replay = await coordinator.reserve(
        organization_id=ORG,
        idempotency_key="stable-idempotency-key",
        request_id=REQUEST,
        command_name="archive_course",
        target_id=TARGET,
        expected_revision=4,
        payload={"reason": "finished"},
        transaction=object(),
    )
    assert first.disposition == "reserved"
    assert replay.disposition == "replay"
    assert replay.receipt.result_reference == first.receipt.result_reference

    with pytest.raises(IdempotencyConflict, match="payload"):
        await coordinator.reserve(
            organization_id=ORG,
            idempotency_key="stable-idempotency-key",
            request_id=REQUEST,
            command_name="archive_course",
            target_id=TARGET,
            expected_revision=4,
            payload={"reason": "different"},
            transaction=object(),
        )


def test_shared_sink_sanitizer_is_recursive_and_utf8_bounded() -> None:
    secret = "private-secret-value"
    sanitized = sanitize_shared_value(
        {
            "authorization": f"Bearer {secret}",
            "magic_link": f"https://review.test/invite/{secret}",
            "provider_response": {"body": secret, "safe_code": "unavailable"},
            "nested": [{"access_token": secret, "message": "я" * 4_000}],
        }
    )
    rendered = repr(sanitized)
    assert secret not in rendered
    assert "safe_code" in rendered
    assert len(repr(sanitized).encode("utf-8")) <= 2048


class Audits:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, event: object, *, transaction: object) -> None:
        self.events.append(event)


@pytest.mark.anyio
async def test_audit_records_actor_agent_revisions_and_sanitized_outcome() -> None:
    repository = Audits()
    recorder = AuditRecorder(repository, event_id_factory=lambda: TARGET, clock=lambda: NOW)
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=5,
        scopes={"reviews:write"},
        expires_at=NOW + timedelta(hours=1),
    )
    event = await recorder.record(
        AuditEventDraft(
            organization_id=ORG,
            actor=actor,
            action="save_review_revision",
            entity_type="review_iteration",
            entity_id=TARGET,
            before_revision=4,
            after_revision=5,
            request_id=REQUEST,
            trace_id=TRACE,
            outcome="succeeded",
            details={"raw_body": "secret", "safe_code": "saved"},
        ),
        transaction=object(),
    )
    assert event.actor_user_id == USER
    assert event.agent_id == AGENT
    assert event.agent_authorization_id == AUTHORIZATION
    assert event.before_revision == 4
    assert event.after_revision == 5
    assert event.sanitized_details == {"safe_code": "saved"}
    assert repository.events == [event]
