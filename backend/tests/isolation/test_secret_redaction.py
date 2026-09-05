"""Release-gate coverage for secrets and PII across every shared sink."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select, text

from review_platform.api.middleware import SanitizedExceptionMiddleware
from review_platform.application.audit import (
    AuditEventDraft,
    AuditRecorder,
    sanitize_shared_value,
)
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.domain.delivery_payload import render_delivery_error
from review_platform.domain.primitives import sanitize_error
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.ai_review import AIReviewAttempt, AIReviewRun
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.mcp.server import (
    MCPAuthenticator,
    MCPToolDispatcher,
    create_mcp_app,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
ENTITY = UUID("00000000-0000-7000-8000-000000183001")
AI_RUN = UUID("00000000-0000-7000-8000-000000183002")
AI_ATTEMPT = UUID("00000000-0000-7000-8000-000000183003")
AI_CREDENTIAL = UUID("00000000-0000-7000-8000-000000183004")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

BEARER_SECRET = "rpat_v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MAGIC_SECRET = "reviewer-magic-secret-0123456789"
MAGIC_LINK = f"https://review.example.test/invite/{MAGIC_SECRET}"
JWT_SECRET = "eyJhbGciOiJIUzI1NiJ9.cHJpdmF0ZS1waWk.signature"
EMAIL_PII = "student.private+redaction@example.test"
PROVIDER_BODY = "provider-private-response-body"
ARTIFACT_CONTENT = "private-artifact-source-bytes"


def _unsafe_details() -> dict[str, Any]:
    return {
        "code": "provider_unavailable",
        "action": "retry_later",
        "message": (
            f"failure for {EMAIL_PII}; Bearer {BEARER_SECRET}; "
            f"session={JWT_SECRET}; {MAGIC_LINK}; " + "x" * 6000
        ),
        "customer_email": EMAIL_PII,
        "authorization": f"Bearer {BEARER_SECRET}",
        "access_token": BEARER_SECRET,
        "magic_link": MAGIC_LINK,
        "invitation_token": MAGIC_SECRET,
        "provider_response": {
            "status": 502,
            "body": f"{PROVIDER_BODY}:{EMAIL_PII}:{BEARER_SECRET}",
            "content": ARTIFACT_CONTENT,
        },
        "artifact_content": ARTIFACT_CONTENT,
    }


def _assert_redacted(value: object, *, max_bytes: int) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    leaked = [
        forbidden
        for forbidden in (
            BEARER_SECRET,
            MAGIC_SECRET,
            JWT_SECRET,
            EMAIL_PII,
            PROVIDER_BODY,
            ARTIFACT_CONTENT,
        )
        if forbidden in rendered
    ]
    assert not leaked, f"secret/PII leaked through sink: {leaked}"
    assert len(rendered.encode("utf-8")) <= max_bytes


def _redaction_violations(value: object) -> list[str]:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return [
        forbidden
        for forbidden in (
            BEARER_SECRET,
            MAGIC_SECRET,
            JWT_SECRET,
            EMAIL_PII,
            PROVIDER_BODY,
            ARTIFACT_CONTENT,
        )
        if forbidden in rendered
    ]


@pytest.mark.parametrize(
    "sink",
    ["log", "api_error", "operation_attempt", "outbox_message", "audit_event"],
)
async def test_shared_sink_matrix_redacts_secrets_pii_provider_and_artifact_bytes(
    sink: str,
    foundation_runtime: FoundationRuntime,
) -> None:
    sanitized = foundation_runtime.write_shared_sink(
        sink=sink,
        organization_id=str(ORG),
        details=_unsafe_details(),
    )
    _assert_redacted(sanitized, max_bytes=2048)
    assert sanitized["code"] == "provider_unavailable"
    assert sanitized["action"] == "retry_later"


async def test_redaction_preserves_nonsecret_uuid_digest_and_actionable_fields() -> None:
    digest = "sha256:" + "a" * 64
    safe = {
        "code": "provider_unavailable",
        "action": "retry_later",
        "entity_id": str(ENTITY),
        "content_digest": digest,
        "message": "bounded diagnostic without personal data",
    }

    for sanitized in (sanitize_error(safe), sanitize_shared_value(safe)):
        assert sanitized == safe


async def test_sanitized_log_record_never_contains_raw_canaries(
    foundation_runtime: FoundationRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sanitized = foundation_runtime.write_shared_sink(
        sink="log",
        organization_id=str(ORG),
        details=_unsafe_details(),
    )
    logger = logging.getLogger("review-platform.redaction-gate")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        logger.error("provider failure: %s", sanitized)
    _assert_redacted(caplog.text, max_bytes=4096)


class _StaticMCPAuthenticator:
    async def resolve(
        self,
        authorization_header: str | None,
        *,
        transaction: object,
    ) -> RequestActor:
        del authorization_header, transaction
        return RequestActor.agent(
            organization_id=ORG,
            user_id=UUID("00000000-0000-7000-8000-000000183010"),
            roles={"reviewer"},
            membership_revision=0,
            auth_epoch=0,
            agent_id=UUID("00000000-0000-7000-8000-000000183011"),
            agent_authorization_id=UUID(
                "00000000-0000-7000-8000-000000183012"
            ),
            agent_authorization_revision=0,
            scopes={"courses:read"},
            expires_at=NOW + timedelta(hours=1),
        )


class _ExplodingMCPDispatcher:
    async def dispatch(
        self,
        *,
        name: str,
        actor: RequestActor,
        arguments: Mapping[str, Any],
        transaction: object,
    ) -> Mapping[str, Any]:
        del name, actor, arguments, transaction
        raise RuntimeError(json.dumps(_unsafe_details()))


_mcp_authenticator_protocol: MCPAuthenticator = _StaticMCPAuthenticator()
_mcp_dispatcher_protocol: MCPToolDispatcher = _ExplodingMCPDispatcher()


async def test_rest_and_mcp_unhandled_errors_are_typed_bounded_and_redacted(
    foundation_runtime: FoundationRuntime,
) -> None:
    rest = FastAPI()
    rest.add_middleware(SanitizedExceptionMiddleware)

    @rest.get("/_redaction-gate")
    async def explode_rest(_: Request) -> None:
        raise RuntimeError(json.dumps(_unsafe_details()))

    mcp = create_mcp_app(
        runtime=foundation_runtime,
        authenticator=_mcp_authenticator_protocol,
        dispatcher=_mcp_dispatcher_protocol,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rest),
        base_url="http://rest.test",
    ) as client:
        rest_response = await client.get("/_redaction-gate")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp),
        base_url="http://mcp.test",
    ) as client:
        mcp_response = await client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {BEARER_SECRET}",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "explode",
            },
            json={},
        )

    assert rest_response.status_code == mcp_response.status_code == 500
    assert set(rest_response.json()) == {"code", "message", "action"}
    assert set(mcp_response.json()) == {"code", "message", "action"}
    _assert_redacted(rest_response.json(), max_bytes=4096)
    _assert_redacted(mcp_response.json(), max_bytes=4096)


async def test_operation_attempt_outbox_and_audit_persist_only_bounded_redaction(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    operation = await foundation_runtime.create_operation(
        organization_id=str(ORG),
        kind="course_import",
        input_version="redaction-gate-v1",
    )
    await foundation_runtime.append_operation_attempt(
        organization_id=str(ORG),
        operation_id=operation.operation_id,
        outcome="action_required",
        error=_unsafe_details(),
    )
    message_id = UUID("00000000-0000-7000-8000-000000183020")
    await foundation_runtime.enqueue_outbox(
        organization_id=str(ORG),
        message_id=str(message_id),
        payload={"event_type": "RedactionGate", "safe_id": str(ENTITY)},
        max_attempts=1,
    )
    leases = await foundation_runtime.lease_outbox(
        owner="redaction-gate",
        now=NOW,
        limit=1,
        lease_seconds=30,
    )
    assert len(leases) == 1 and leases[0].message_id == str(message_id)
    await foundation_runtime.fail_outbox(
        lease=leases[0],
        error=_unsafe_details(),
        now=NOW,
    )
    actor = RequestActor.installation_operator(
        organization_id=ORG,
        installation_operator_id="redaction-gate",
        reason="verify shared sinks",
    )
    async with session_scope(foundation_session_factory) as session:
        await AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=lambda: UUID(
                "00000000-0000-7000-8000-000000183021"
            ),
            clock=lambda: NOW,
        ).record(
            AuditEventDraft(
                organization_id=ORG,
                actor=actor,
                action="redaction_gate",
                entity_type="operation",
                entity_id=UUID(operation.operation_id),
                before_revision=0,
                after_revision=1,
                request_id=UUID("00000000-0000-7000-8000-000000183022"),
                trace_id=UUID("00000000-0000-7000-8000-000000183023"),
                outcome="action_required",
                details=_unsafe_details(),
            ),
            transaction=session,
        )

    async with foundation_session_factory() as session:
        stored_operation = await session.get(Operation, UUID(operation.operation_id))
        attempt = await session.scalar(
            select(OperationAttempt).where(
                OperationAttempt.organization_id == ORG,
                OperationAttempt.operation_id == UUID(operation.operation_id),
            )
        )
        outbox = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.organization_id == ORG,
                OutboxMessage.message_id == message_id,
            )
        )
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == ORG,
                AuditEvent.action == "redaction_gate",
            )
        )
    assert stored_operation is not None
    assert attempt is not None and outbox is not None and audit is not None
    stored_sinks = {
        "operation": stored_operation.sanitized_error,
        "operation_attempt": attempt.sanitized_error,
        "outbox": outbox.sanitized_error,
        "audit": audit.sanitized_details,
    }
    for stored in stored_sinks.values():
        assert stored is not None
        assert len(
            json.dumps(stored, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ) <= 2048
    violations = {
        sink: _redaction_violations(stored)
        for sink, stored in stored_sinks.items()
        if stored is not None and _redaction_violations(stored)
    }
    assert not violations, f"persisted shared sinks leaked secrets/PII: {violations}"


async def test_ai_attempt_failure_is_bounded_and_redacted(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    async with session_scope(foundation_session_factory) as session:
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        session.add(
            AIReviewRun(
                id=AI_RUN,
                organization_id=ORG,
                review_iteration_id=UUID(
                    "00000000-0000-7000-8000-000000183030"
                ),
                course_run_id=UUID("00000000-0000-7000-8000-000000183031"),
                submission_version_id=UUID(
                    "00000000-0000-7000-8000-000000183032"
                ),
                artifact_version_id=UUID(
                    "00000000-0000-7000-8000-000000183033"
                ),
                content_digest="sha256:" + "1" * 64,
                homework_version_id=UUID(
                    "00000000-0000-7000-8000-000000183034"
                ),
                homework_digest="sha256:" + "2" * 64,
                criterion_set_id=UUID(
                    "00000000-0000-7000-8000-000000183035"
                ),
                criteria_digest="sha256:" + "3" * 64,
                contract_version="1.1.0",
                fingerprint_algorithm="sha256-jcs-v1",
                input_fingerprint="sha256:" + "4" * 64,
                status="running",
                current_attempt_no=1,
                revision=1,
            )
        )
        session.add(
            AIReviewAttempt(
                id=AI_ATTEMPT,
                organization_id=ORG,
                ai_review_run_id=AI_RUN,
                attempt_number=1,
                credential_binding_id=AI_CREDENTIAL,
                credential_binding_version=1,
                status="running",
                last_sequence=0,
                started_at=NOW,
                finished_at=None,
                error_code=None,
                sanitized_error=None,
            )
        )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        changed = await AIReviewRepository(session).transition_attempt(
            ORG,
            AI_ATTEMPT,
            expected_status="running",
            expected_last_sequence=0,
            new_status="retryable_failed",
            new_sequence=1,
            error=_unsafe_details(),
        )
        assert changed

    async with foundation_session_factory() as session:
        attempt = await session.get(AIReviewAttempt, AI_ATTEMPT)
    assert attempt is not None and attempt.sanitized_error is not None
    _assert_redacted(attempt.sanitized_error, max_bytes=2048)


async def test_delivery_provider_and_artifact_failure_is_bounded_and_redacted() -> None:
    delivery_error = render_delivery_error(
        {**_unsafe_details(), "retryable": True},
    )
    assert delivery_error["code"] == "provider_unavailable"
    assert delivery_error["action"] == "retry_later"
    _assert_redacted(delivery_error, max_bytes=2048)
