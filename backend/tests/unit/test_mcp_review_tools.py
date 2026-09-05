"""Focused parity tests for the five MCP review mutation bindings."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any, cast
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from tests.support.contracts import CONTRACT_ROOT, load_json, load_openapi, resolve_fragment

from review_platform.application.audit import AuditEvent, AuditRecorder
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import (
    IdempotencyError,
    canonical_command_digest,
)
from review_platform.application.request_context import (
    AuthVersionSnapshot,
    RequestActor,
)
from review_platform.contracts.commands import WireCommand
from review_platform.mcp.server import MCPServerError
from review_platform.mcp.tools.review import (
    REVIEW_TOOL_NAMES,
    ReviewToolContext,
    build_review_tool_registry,
    open_review_iteration,
    record_review_responsibility,
    save_review_revision,
    select_reviewer_courses,
    set_availability,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 5, 15, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000191001")
USER = UUID("00000000-0000-7000-8000-000000191002")
AGENT = UUID("00000000-0000-7000-8000-000000191003")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000191004")
TARGET = UUID("00000000-0000-7000-8000-000000191005")
RELATED = UUID("00000000-0000-7000-8000-000000191006")


class FakeGuard:
    def __init__(self) -> None:
        self.early: list[RequestActor] = []
        self.locked: list[tuple[RequestActor, object]] = []

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.early.append(actor)
        return _snapshot(actor)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        self.locked.append((actor, transaction))
        return _snapshot(actor)


class FakeRuntime:
    def __init__(self, guard: FakeGuard) -> None:
        self.user_auth_guard = guard
        self.clock = lambda: NOW
        identifiers = count(100)
        self.id_factory = lambda: UUID(int=next(identifiers))


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        del transaction
        self.events.append(event)


@dataclass
class ReceiptState:
    payload_digest: str
    actor_snapshot: tuple[object, ...]
    payload: dict[str, Any] | None = None


class FakeReceipts:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, str], ReceiptState] = {}
        self.completed = 0

    async def reserve(
        self,
        *,
        command: WireCommand,
        actor: RequestActor,
        transaction: object,
    ) -> tuple[str, Mapping[str, Any] | None]:
        del transaction
        key = (actor.organization_id, command.idempotency_key)
        digest = canonical_command_digest(
            command_name=str(command.command_name),
            target_id=command.target_id,
            expected_revision=command.expected_revision,
            payload=command.payload,
        )
        snapshot = _actor_identity(actor)
        existing = self.items.get(key)
        if existing is not None:
            if existing.payload_digest != digest:
                raise IdempotencyError("different canonical payload")
            if existing.actor_snapshot != snapshot:
                raise IdempotencyError("authority snapshot does not match")
            if existing.payload is None:
                raise IdempotencyError("command is still processing")
            return "replay", existing.payload
        self.items[key] = ReceiptState(digest, snapshot)
        return "reserved", None

    async def complete(
        self,
        *,
        command: WireCommand,
        actor: RequestActor,
        payload: Mapping[str, Any],
        transaction: object,
    ) -> None:
        del transaction
        state = self.items[(actor.organization_id, command.idempotency_key)]
        if state.actor_snapshot != _actor_identity(actor) or state.payload is not None:
            raise IdempotencyError("completion conflict")
        state.payload = dict(payload)
        self.completed += 1


class FakeDispatch:
    def __init__(self) -> None:
        self.calls: list[tuple[RequestActor, WireCommand, object, UUID]] = []

    async def __call__(
        self,
        actor: RequestActor,
        command: WireCommand,
        transaction: object,
        trace_id: UUID,
    ) -> Mapping[str, Any] | None:
        self.calls.append((actor, command, transaction, trace_id))
        name = str(command.command_name)
        if name == "set_reviewer_course_selection":
            return None
        if name == "set_reviewer_availability":
            return {"id": str(RELATED), "revision": 4}
        if name == "open_review_iteration":
            return {
                "review_iteration_id": str(RELATED),
                "review_case_id": str(command.target_id),
                "revision": command.expected_revision + 1,
            }
        if name == "record_review_responsibility":
            return {"id": str(RELATED), "revision": 0}
        if name == "save_review_revision":
            return {
                "review_revision_id": str(RELATED),
                "review_iteration_id": str(command.target_id),
                "review_iteration_revision": command.expected_revision + 1,
            }
        raise AssertionError(name)


@dataclass
class Harness:
    context: ReviewToolContext
    runtime: FakeRuntime
    actor: RequestActor
    guard: FakeGuard
    audit: FakeAuditRepository
    receipts: FakeReceipts
    dispatch: FakeDispatch
    transaction: object


def _harness() -> Harness:
    guard = FakeGuard()
    runtime = FakeRuntime(guard)
    audit = FakeAuditRepository()
    receipts = FakeReceipts()
    dispatch = FakeDispatch()
    ticks = count()
    context = ReviewToolContext(
        runtime=cast(FoundationRuntime, runtime),
        audit=AuditRecorder(
            audit,
            event_id_factory=lambda: UUID(int=900),
            clock=lambda: NOW,
        ),
        receipts=receipts,
        dispatch=dispatch,
        monotonic=lambda: 10 + next(ticks) / 100,
    )
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer", "methodologist"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=4,
        scopes={"review_preferences:write", "reviews:write"},
        expires_at=NOW + timedelta(hours=1),
    )
    return Harness(
        context=context,
        runtime=runtime,
        actor=actor,
        guard=guard,
        audit=audit,
        receipts=receipts,
        dispatch=dispatch,
        transaction=object(),
    )


def _arguments(tool_name: str, number: int = 1) -> dict[str, Any]:
    command_names = {
        "select_reviewer_courses": "set_reviewer_course_selection",
        "set_availability": "set_reviewer_availability",
        "open_review_iteration": "open_review_iteration",
        "record_review_responsibility": "record_review_responsibility",
        "save_review_revision": "save_review_revision",
    }
    revision_targets = {
        "select_reviewer_courses": "membership",
        "set_availability": "membership",
        "open_review_iteration": "review_case",
        "record_review_responsibility": "review_iteration",
        "save_review_revision": "review_iteration",
    }
    payloads: dict[str, dict[str, Any]] = {
        "select_reviewer_courses": {"course_run_ids": [str(RELATED)]},
        "set_availability": {
            "planned_minutes": 240,
            "until_at": (NOW + timedelta(days=2)).isoformat(),
        },
        "open_review_iteration": {"submission_version_id": str(RELATED)},
        "record_review_responsibility": {"action": "joined"},
        "save_review_revision": {
            "feedback": "Exact human draft",
            "criterion_decisions": [],
            "review_notes": [],
        },
    }
    return {
        "request_id": str(UUID(int=1000 + number)),
        "idempotency_key": f"mcp-review-{tool_name}-{number:04d}",
        "command_name": command_names[tool_name],
        "revision_target": revision_targets[tool_name],
        "target_id": str(TARGET),
        "expected_revision": 3,
        "payload": payloads[tool_name],
    }


def _output_definition(tool_name: str) -> dict[str, Any]:
    manifest = load_json(CONTRACT_ROOT / "mcp-tools.json")
    tool = next(item for item in manifest["tools"] if item["name"] == tool_name)
    reference = tool["outputSchema"]["$ref"]
    file_name, _, fragment = reference.partition("#")
    document = load_openapi() if file_name == "openapi.yaml" else manifest
    value = resolve_fragment(document, f"#{fragment}")
    assert isinstance(value, dict)
    return value


async def test_registry_is_exact_and_contains_only_review_mutation_handlers() -> None:
    harness = _harness()
    registry = build_review_tool_registry(
        cast(FoundationRuntime, harness.runtime),
        audit=harness.context.audit,
        receipts=harness.receipts,
        dispatch=harness.dispatch,
    )

    assert set(registry) == REVIEW_TOOL_NAMES
    assert "publish_review" not in registry
    assert "request_review_publication" not in registry
    assert "start_ai_review" not in registry
    assert all(callable(handler) for handler in registry.values())
    assert all(
        callable(handler)
        for handler in (
            select_reviewer_courses,
            set_availability,
            open_review_iteration,
            record_review_responsibility,
            save_review_revision,
        )
    )


async def test_all_five_tools_dispatch_exact_commands_and_return_typed_outputs() -> None:
    harness = _harness()
    expected_names = [
        "select_reviewer_courses",
        "set_availability",
        "open_review_iteration",
        "record_review_responsibility",
        "save_review_revision",
    ]

    for index, tool_name in enumerate(expected_names, start=1):
        result = await _call(
            harness,
            tool_name,
            _arguments(tool_name, index),
        )
        Draft202012Validator(_output_definition(tool_name)).validate(result)

    assert [str(call[1].command_name) for call in harness.dispatch.calls] == [
        "set_reviewer_course_selection",
        "set_reviewer_availability",
        "open_review_iteration",
        "record_review_responsibility",
        "save_review_revision",
    ]
    assert all(call[0] is harness.actor for call in harness.dispatch.calls)
    assert all(call[2] is harness.transaction for call in harness.dispatch.calls)
    assert len(harness.guard.early) == len(harness.guard.locked) == 5
    assert len(harness.audit.events) == 5
    assert [event.after_revision for event in harness.audit.events] == [3, 3, 4, 3, 4]
    for event, tool_name in zip(harness.audit.events, expected_names, strict=True):
        assert event.actor_user_id == USER
        assert event.agent_id == AGENT
        assert event.agent_authorization_id == AUTHORIZATION
        assert event.entity_id == TARGET
        assert event.before_revision == 3
        assert event.outcome == "succeeded"
        assert event.sanitized_details == {
            "tool_name": tool_name,
            "duration_ms": 10,
            "idempotency_disposition": "reserved",
        }


async def test_replay_returns_stable_result_without_second_dispatch_or_completion() -> None:
    harness = _harness()
    arguments = _arguments("save_review_revision")

    first = await _call(harness, "save_review_revision", arguments)
    replay = await _call(harness, "save_review_revision", arguments)

    assert replay == first
    assert len(harness.dispatch.calls) == 1
    assert harness.receipts.completed == 1
    dispositions = [
        event.sanitized_details["idempotency_disposition"] for event in harness.audit.events
    ]
    assert dispositions == ["reserved", "replay"]


async def test_same_key_rejects_stale_cas_payload_and_different_actor_snapshot() -> None:
    harness = _harness()
    arguments = _arguments("record_review_responsibility")
    await _call(harness, "record_review_responsibility", arguments)

    stale = dict(arguments)
    stale["expected_revision"] = 2
    with pytest.raises(MCPServerError) as collision:
        await _call(harness, "record_review_responsibility", stale)
    assert (collision.value.status_code, collision.value.code) == (
        409,
        "mcp_command_conflict",
    )

    harness.actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer", "methodologist"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=5,
        scopes={"review_preferences:write", "reviews:write"},
        expires_at=NOW + timedelta(hours=1),
    )
    with pytest.raises(MCPServerError) as authority_collision:
        await _call(harness, "record_review_responsibility", arguments)
    assert (authority_collision.value.status_code, authority_collision.value.code) == (
        409,
        "mcp_command_conflict",
    )
    assert len(harness.dispatch.calls) == 1


async def test_wrong_tool_command_and_missing_role_or_scope_fail_closed() -> None:
    harness = _harness()
    mismatched = _arguments("save_review_revision")
    with pytest.raises(MCPServerError) as wrong_command:
        await _call(harness, "open_review_iteration", mismatched)
    assert (wrong_command.value.status_code, wrong_command.value.code) == (
        400,
        "invalid_mcp_arguments",
    )

    harness.actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"methodologist"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=4,
        scopes={"reviews:write"},
        expires_at=NOW + timedelta(hours=1),
    )
    with pytest.raises(MCPServerError) as role_denied:
        await _call(
            harness,
            "select_reviewer_courses",
            _arguments("select_reviewer_courses"),
        )
    assert (role_denied.value.status_code, role_denied.value.code) == (
        403,
        "mcp_scope_denied",
    )


async def _call(
    harness: Harness,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    handlers = {
        "select_reviewer_courses": select_reviewer_courses,
        "set_availability": set_availability,
        "open_review_iteration": open_review_iteration,
        "record_review_responsibility": record_review_responsibility,
        "save_review_revision": save_review_revision,
    }
    return await handlers[tool_name](
        actor=harness.actor,
        arguments=arguments,
        transaction=harness.transaction,
        context=harness.context,
    )


def _actor_identity(actor: RequestActor) -> tuple[object, ...]:
    return (
        actor.actor_type,
        actor.organization_id,
        actor.user_id,
        actor.roles,
        actor.membership_revision,
        actor.auth_epoch,
        actor.agent_id,
        actor.agent_authorization_id,
        actor.agent_authorization_revision,
        actor.scopes,
        actor.expires_at,
    )


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
        agent_authorization_id=actor.agent_authorization_id,
        agent_authorization_revision=actor.agent_authorization_revision,
        agent_scopes=actor.scopes,
        agent_expires_at=actor.expires_at,
        agent_active=True,
    )
