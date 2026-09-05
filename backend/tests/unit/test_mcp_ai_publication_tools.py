"""Focused tests for MCP AI start and harmless publication requests."""

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
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewStartService,
)
from review_platform.contracts.commands import WireCommand
from review_platform.mcp.server import MCPServerError
from review_platform.mcp.tools.ai_publication import (
    AI_PUBLICATION_TOOL_NAMES,
    AIPublicationToolContext,
    build_ai_publication_tool_registry,
    request_review_publication,
    start_ai_review,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 5, 16, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000192001")
USER = UUID("00000000-0000-7000-8000-000000192002")
AGENT = UUID("00000000-0000-7000-8000-000000192003")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000192004")
ITERATION = UUID("00000000-0000-7000-8000-000000192005")
REVIEW_REVISION = UUID("00000000-0000-7000-8000-000000192006")
OPERATION = UUID("00000000-0000-7000-8000-000000192007")
PUBLICATION_REQUEST = UUID("00000000-0000-7000-8000-000000192008")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000192009")
BINDING = AIComponentCredentialBinding(
    organization_id=ORG,
    credential_binding_id=CREDENTIAL,
    credential_binding_version=7,
)


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
        identifiers = count(300)
        self.id_factory = lambda: UUID(int=next(identifiers))


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        del transaction
        self.events.append(event)


@dataclass
class Receipt:
    digest: str
    actor_revision: int | None
    result: dict[str, Any] | None = None


class FakeReceipts:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, str], Receipt] = {}
        self.completions = 0

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
        existing = self.items.get(key)
        if existing is not None:
            if existing.digest != digest:
                raise IdempotencyError("different canonical payload")
            if existing.actor_revision != actor.agent_authorization_revision:
                raise IdempotencyError("authority snapshot does not match")
            assert existing.result is not None
            return "replay", existing.result
        self.items[key] = Receipt(digest, actor.agent_authorization_revision)
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
        receipt = self.items[(actor.organization_id, command.idempotency_key)]
        receipt.result = dict(payload)
        self.completions += 1


class FakeDispatch:
    def __init__(self) -> None:
        self.calls: list[
            tuple[
                RequestActor,
                WireCommand,
                object,
                UUID,
                AIComponentCredentialBinding,
            ]
        ] = []

    async def __call__(
        self,
        actor: RequestActor,
        command: WireCommand,
        transaction: object,
        trace_id: UUID,
        credential_binding: AIComponentCredentialBinding,
    ) -> Mapping[str, Any]:
        self.calls.append((actor, command, transaction, trace_id, credential_binding))
        if str(command.command_name) == "start_ai_review":
            return {
                "id": str(OPERATION),
                "kind": "ai_review",
                "input_version": "ai-review:1.1.0:sha256:input",
                "state": "pending",
                "attempts": [
                    {
                        "attempt_number": 1,
                        "state": "processing",
                        "started_at": NOW.isoformat(),
                        "finished_at": None,
                        "error": {
                            "code": "temporary",
                            "message": "Retry later",
                            "action": "retry",
                            "retryable": True,
                        },
                        "worker_identity": "must-be-dropped",
                    }
                ],
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
                "finished_at": None,
                "error": None,
                "internal_signed_url": "must-be-dropped",
            }
        return {
            "id": str(PUBLICATION_REQUEST),
            "review_revision_id": str(REVIEW_REVISION),
            "status": "pending",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            "revision": 0,
            "published": False,
        }


@dataclass
class Harness:
    context: AIPublicationToolContext
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
    context = AIPublicationToolContext(
        runtime=cast(FoundationRuntime, runtime),
        credential_binding=BINDING,
        audit=AuditRecorder(
            audit,
            event_id_factory=lambda: UUID(int=999),
            clock=lambda: NOW,
        ),
        receipts=receipts,
        dispatch=dispatch,
        monotonic=lambda: 20 + next(ticks) / 100,
    )
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer", "methodologist"},
        membership_revision=4,
        auth_epoch=3,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=2,
        scopes={"ai_reviews:start", "publication_requests:write"},
        expires_at=NOW + timedelta(hours=1),
    )
    return Harness(
        context,
        runtime,
        actor,
        guard,
        audit,
        receipts,
        dispatch,
        object(),
    )


def _arguments(tool_name: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if tool_name == "request_review_publication":
        payload = {
            "review_revision_id": str(REVIEW_REVISION),
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }
    return {
        "request_id": str(UUID(int=600 if tool_name == "start_ai_review" else 601)),
        "idempotency_key": f"mcp-{tool_name}-0001",
        "command_name": tool_name,
        "revision_target": "review_iteration",
        "target_id": str(ITERATION),
        "expected_revision": 5,
        "payload": payload,
    }


def _output_definition(tool_name: str) -> dict[str, Any]:
    manifest = load_json(CONTRACT_ROOT / "mcp-tools.json")
    tool = next(item for item in manifest["tools"] if item["name"] == tool_name)
    reference = tool["outputSchema"]["$ref"]
    file_name, _, fragment = reference.partition("#")
    if file_name == "openapi.yaml":
        openapi = load_openapi()
        return {
            "$ref": f"#{fragment}",
            "components": openapi["components"],
        }
    document = manifest
    value = resolve_fragment(document, f"#{fragment}")
    assert isinstance(value, dict)
    return value


async def test_registry_contains_exact_two_tools_and_no_direct_publish_surface() -> None:
    harness = _harness()

    def unused_factory(_: Any) -> AIReviewStartService:
        raise AssertionError("injected dispatcher must be used")

    registry = build_ai_publication_tool_registry(
        cast(FoundationRuntime, harness.runtime),
        credential_binding=BINDING,
        ai_review_start_service_factory=unused_factory,
        audit=harness.context.audit,
        receipts=harness.receipts,
        dispatch=harness.dispatch,
    )

    assert set(registry) == AI_PUBLICATION_TOOL_NAMES
    assert set(registry) == {"start_ai_review", "request_review_publication"}
    assert "publish_review" not in registry
    assert all(callable(handler) for handler in registry.values())


async def test_both_tools_use_exact_server_binding_and_return_closed_typed_outputs() -> None:
    harness = _harness()

    ai_result = await _call(harness, "start_ai_review", _arguments("start_ai_review"))
    request_result = await _call(
        harness,
        "request_review_publication",
        _arguments("request_review_publication"),
    )

    Draft202012Validator(_output_definition("start_ai_review")).validate(ai_result)
    Draft202012Validator(_output_definition("request_review_publication")).validate(request_result)
    assert set(ai_result) == {
        "id",
        "kind",
        "input_version",
        "state",
        "attempts",
        "created_at",
        "updated_at",
        "finished_at",
        "error",
    }
    assert set(request_result) == {
        "id",
        "review_revision_id",
        "status",
        "expires_at",
        "revision",
    }
    assert set(ai_result["attempts"][0]) == {
        "attempt_number",
        "state",
        "started_at",
        "finished_at",
        "error",
    }
    assert ai_result["attempts"][0]["error"] == {
        "code": "temporary",
        "message": "Retry later",
        "action": "retry",
    }
    assert [call[4] for call in harness.dispatch.calls] == [BINDING, BINDING]
    assert all(call[0] is harness.actor for call in harness.dispatch.calls)
    assert len(harness.guard.early) == len(harness.guard.locked) == 2
    assert [event.agent_id for event in harness.audit.events] == [AGENT, AGENT]
    assert [event.agent_authorization_id for event in harness.audit.events] == [
        AUTHORIZATION,
        AUTHORIZATION,
    ]
    assert [event.sanitized_details["tool_name"] for event in harness.audit.events] == [
        "start_ai_review",
        "request_review_publication",
    ]


async def test_replay_is_stable_and_never_restarts_ai_or_duplicates_request() -> None:
    harness = _harness()
    arguments = _arguments("start_ai_review")

    first = await _call(harness, "start_ai_review", arguments)
    replay = await _call(harness, "start_ai_review", arguments)

    assert replay == first
    assert len(harness.dispatch.calls) == 1
    assert harness.receipts.completions == 1
    dispositions = [
        event.sanitized_details["idempotency_disposition"] for event in harness.audit.events
    ]
    assert dispositions == ["reserved", "replay"]


async def test_client_binding_injection_wrong_command_and_missing_scope_fail_closed() -> None:
    harness = _harness()
    injected = _arguments("start_ai_review")
    injected["payload"] = {
        "credential_binding_id": str(UUID(int=123)),
        "credential_binding_version": 1,
    }
    with pytest.raises(MCPServerError) as invalid:
        await _call(harness, "start_ai_review", injected)
    assert (invalid.value.status_code, invalid.value.code) == (
        400,
        "invalid_mcp_arguments",
    )

    wrong = _arguments("request_review_publication")
    with pytest.raises(MCPServerError) as mismatch:
        await _call(harness, "start_ai_review", wrong)
    assert (mismatch.value.status_code, mismatch.value.code) == (
        400,
        "invalid_mcp_arguments",
    )

    harness.actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=4,
        auth_epoch=3,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=2,
        scopes={"publication_requests:write"},
        expires_at=NOW + timedelta(hours=1),
    )
    with pytest.raises(MCPServerError) as denied:
        await _call(harness, "start_ai_review", _arguments("start_ai_review"))
    assert (denied.value.status_code, denied.value.code) == (
        403,
        "mcp_scope_denied",
    )
    assert not harness.dispatch.calls


async def _call(
    harness: Harness,
    tool_name: str,
    arguments: Mapping[str, Any],
) -> Mapping[str, Any]:
    handler = start_ai_review if tool_name == "start_ai_review" else request_review_publication
    return await handler(
        actor=harness.actor,
        arguments=arguments,
        transaction=harness.transaction,
        context=harness.context,
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
