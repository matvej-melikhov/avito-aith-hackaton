from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.services.ai_review_start import AIReviewDispatch
from review_platform.contracts.ai_review import AIReviewRequest
from review_platform.infrastructure.db.models.ai_review import AIReviewAttempt, AIReviewRun
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.operations import (
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.providers.mocks import FrozenFixtureStore
from review_platform.infrastructure.tasks.ai_review import (
    AIReviewDispatchTaskHandler,
    AIReviewEventTaskHandler,
    AIReviewTaskError,
    AIReviewTaskRuntime,
    SqlAIEventOperationRecorder,
    SqlAIReviewScheduler,
)
from review_platform.infrastructure.tasks.broker import RetryableTaskError
from review_platform.infrastructure.tasks.registry import HANDLER_MODULES, REGISTRY

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
RUN = UUID("00000000-0000-7000-8000-000000000060")
ATTEMPT = UUID("00000000-0000-7000-8000-000000000061")
CRITERION = UUID("00000000-0000-7000-8000-000000000063")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000011001")
OTHER_CREDENTIAL = UUID("00000000-0000-7000-8000-000000011002")
REQUEST_ID = UUID("00000000-0000-7000-8000-000000011003")
TRACE_ID = UUID("00000000-0000-7000-8000-000000011004")
EVENT_MESSAGE = UUID("00000000-0000-7000-8000-000000011005")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
FINGERPRINT = "sha256:8487b948022750a39653da4a76d721c9b16e0961903ad84ce80ca49be67fd14e"


class IDs:
    def __init__(self, value: int = 12000) -> None:
        self.value = value

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


class Provider:
    contract_version = "1.1.0"
    schema_name = "ai-review.schema.json"

    def __init__(self, outcome: str = "accepted") -> None:
        self.outcome = outcome
        self.requests: list[ProviderPayload] = []

    async def start_review(self, request: ProviderPayload) -> ProviderPayload:
        self.requests.append(request)
        error: dict[str, Any] | None = None
        if self.outcome != "accepted":
            error = {
                "code": "component_unavailable",
                "message": "Bearer super-secret provider body",
                "retryable": self.outcome == "retryable_failed",
                "action": "retry" if self.outcome == "retryable_failed" else "inspect_provider",
            }
        return {
            "contract_version": "1.1.0",
            "organization_id": str(ORG),
            "run_id": str(RUN),
            "attempt_id": str(ATTEMPT),
            "attempt_number": 1,
            "outcome": self.outcome,
            "error": error,
        }


class ComponentAuthorization:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, str]] = []

    async def authorize_component(
        self,
        *,
        component_id: str,
        organization_id: UUID,
        transaction: object,
    ) -> None:
        del transaction
        self.calls.append((component_id, organization_id, "early"))

    async def revalidate_component(
        self,
        *,
        component_id: str,
        organization_id: UUID,
        transaction: object,
    ) -> None:
        del transaction
        self.calls.append((component_id, organization_id, "final"))


class EventAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record_component_event(self, **values: object) -> None:
        self.events.append(values)


def _request() -> AIReviewRequest:
    return AIReviewRequest.model_validate(
        {
            "contract_version": "1.1.0",
            "run_id": RUN,
            "input_fingerprint": FINGERPRINT,
            "fingerprint_algorithm": "jcs-sha256-v1",
            "organization_id": ORG,
            "course_run_id": "00000000-0000-7000-8000-000000000002",
            "submission_version_id": "00000000-0000-7000-8000-000000000003",
            "review_iteration_id": "00000000-0000-7000-8000-000000000004",
            "credential_binding_id": CREDENTIAL,
            "credential_binding_version": 1,
            "artifact": {
                "contract_version": "1.1.0",
                "organization_id": ORG,
                "artifact_reference_id": "00000000-0000-7000-8000-000000000008",
                "artifact_version_id": "00000000-0000-7000-8000-000000000005",
                "provider": "github",
                "provider_version": "commit:" + "a" * 40,
                "content_digest": "sha256:" + "0" * 64,
                "captured_at": "2026-09-05T11:55:00Z",
                "object": {
                    "key": f"{ORG}/00000000-0000-7000-8000-000000000005/artifact.zip",
                    "media_type": "application/zip",
                    "byte_size": 128,
                },
                "metadata": {"source": "offline-fixture"},
            },
            "artifact_download": {
                "url": "https://objects.example.test/artifact?signature=opaque",
                "expires_at": NOW + timedelta(minutes=5),
            },
            "homework": {
                "version_id": "00000000-0000-7000-8000-000000000006",
                "title": "Contract homework",
                "student_text": "Submit the solution",
                "digest": "sha256:" + "1" * 64,
            },
            "criteria": {
                "set_id": "00000000-0000-7000-8000-000000000007",
                "digest": "sha256:" + "2" * 64,
                "items": [
                    {
                        "id": CRITERION,
                        "key": "correctness",
                        "title": "Correctness",
                        "description": "The solution is correct",
                        "max_points": 5,
                    }
                ],
            },
        }
    )


def _dispatch() -> AIReviewDispatch:
    return AIReviewDispatch(
        organization_id=ORG,
        operation_id=RUN,
        run_id=RUN,
        attempt_id=ATTEMPT,
        attempt_number=1,
        input_version=f"ai-review:1.1.0:{FINGERPRINT}",
        event_type="AIReviewRequested",
        payload_version="1.1.0",
        request=_request(),
        request_id=REQUEST_ID,
        trace_id=TRACE_ID,
        requested_at=NOW,
    )


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="ai_review",
                    binding_version=1,
                    ciphertext="encrypted-ai-component",
                    key_id="fixture-key",
                    status="active",
                ),
                ExternalCredential(
                    id=OTHER_CREDENTIAL,
                    organization_id=ORG,
                    provider="ai_review",
                    binding_version=1,
                    ciphertext="other-encrypted-component",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        session.add(
            AIReviewRun(
                id=RUN,
                organization_id=ORG,
                review_iteration_id=_request().review_iteration_id,
                course_run_id=_request().course_run_id,
                submission_version_id=_request().submission_version_id,
                artifact_version_id=_request().artifact.artifact_version_id,
                content_digest=_request().artifact.content_digest,
                homework_version_id=_request().homework.version_id,
                homework_digest=_request().homework.digest,
                criterion_set_id=_request().criteria.set_id,
                criteria_digest=_request().criteria.digest,
                contract_version="1.1.0",
                fingerprint_algorithm="jcs-sha256-v1",
                input_fingerprint=FINGERPRINT,
                status="running",
                current_attempt_no=1,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            AIReviewAttempt(
                id=ATTEMPT,
                organization_id=ORG,
                ai_review_run_id=RUN,
                attempt_number=1,
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                status="pending",
                last_sequence=0,
                started_at=NOW,
            )
        )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))


async def _schedule(factory: AsyncSessionFactory, ids: IDs) -> UUID:
    async with session_scope(factory) as session:
        await SqlAIReviewScheduler(id_factory=ids, clock=lambda: NOW).schedule(
            _dispatch(),
            transaction=session,
        )
    async with factory() as session:
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "AIReviewRequested"
            )
        )
        assert message is not None
        return message.message_id


def _runtime(
    factory: AsyncSessionFactory,
    provider: Provider,
    ids: IDs,
    *,
    max_attempts: int = 3,
    authorization: ComponentAuthorization | None = None,
    audit: EventAudit | None = None,
) -> AIReviewTaskRuntime:
    return AIReviewTaskRuntime(
        session_factory=factory,
        provider=provider,
        component_authorization=authorization or ComponentAuthorization(),
        event_audit=audit or EventAudit(),
        id_factory=ids,
        clock=lambda: NOW,
        dispatch_max_attempts=max_attempts,
    )


async def test_scheduler_dispatches_exact_binding_once_and_records_processing_attempt(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    ids = IDs()
    message_id = await _schedule(foundation_session_factory, ids)
    async with session_scope(foundation_session_factory) as session:
        await SqlAIReviewScheduler(id_factory=ids, clock=lambda: NOW).schedule(
            _dispatch(),
            transaction=session,
        )
    provider = Provider()
    handler = AIReviewDispatchTaskHandler(
        _runtime(foundation_session_factory, provider, ids)
    )

    result = await handler(organization_id=str(ORG), message_id=str(message_id))
    replay = await handler(organization_id=str(ORG), message_id=str(message_id))

    assert result["state"] == "processing"
    assert replay["replayed"] is True
    assert len(provider.requests) == 1
    assert provider.requests[0]["credential_binding_id"] == str(CREDENTIAL)
    assert provider.requests[0]["credential_binding_version"] == 1
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, RUN)
        attempt = await session.scalar(select(OperationAttempt))
        message_count = await session.scalar(
            select(func.count()).select_from(OutboxMessage)
        )
        assert operation is not None and operation.state == "processing"
        assert attempt is not None and attempt.outcome == "processing"
        assert str(CREDENTIAL) in attempt.worker_identity
        assert "@1" in attempt.worker_identity
        assert message_count == 1


async def test_dispatch_retry_then_exhaustion_is_sanitized_and_terminal(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    ids = IDs(13000)
    message_id = await _schedule(foundation_session_factory, ids)
    provider = Provider("retryable_failed")
    handler = AIReviewDispatchTaskHandler(
        _runtime(
            foundation_session_factory,
            provider,
            ids,
            max_attempts=2,
        )
    )

    with pytest.raises(RetryableTaskError):
        await handler(organization_id=str(ORG), message_id=str(message_id))
    exhausted = await handler(organization_id=str(ORG), message_id=str(message_id))

    assert exhausted["state"] == "action_required"
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, RUN)
        run = await session.get(AIReviewRun, RUN)
        attempt = await session.get(AIReviewAttempt, ATTEMPT)
        history = (
            await session.scalars(
                select(OperationAttempt).order_by(OperationAttempt.attempt_number)
            )
        ).all()
        assert operation is not None and operation.state == "action_required"
        assert run is not None and run.status == "action_required"
        assert attempt is not None and attempt.status == "action_required"
        assert [item.outcome for item in history] == [
            "retryable_failed",
            "action_required",
        ]
        rendered = str(history[0].sanitized_error).lower()
        assert "super-secret" not in rendered


async def test_dispatch_rejects_mismatched_or_revoked_exact_credential_before_provider(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    ids = IDs(14000)
    message_id = await _schedule(foundation_session_factory, ids)
    async with session_scope(foundation_session_factory) as session:
        credential = await session.scalar(
            select(ExternalCredential).where(
                ExternalCredential.organization_id == ORG,
                ExternalCredential.id == CREDENTIAL,
                ExternalCredential.binding_version == 1,
            )
        )
        assert credential is not None
        credential.status = "revoked"
    provider = Provider()

    with pytest.raises(AIReviewTaskError, match="exact credential"):
        await AIReviewDispatchTaskHandler(
            _runtime(foundation_session_factory, provider, ids)
        )(organization_id=str(ORG), message_id=str(message_id))
    assert provider.requests == []


async def test_event_task_reuses_t106_and_appends_terminal_operation_attempt(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    ids = IDs(15000)
    dispatch_message = await _schedule(foundation_session_factory, ids)
    authorization = ComponentAuthorization()
    audit = EventAudit()
    runtime = _runtime(
        foundation_session_factory,
        Provider(),
        ids,
        authorization=authorization,
        audit=audit,
    )
    await AIReviewDispatchTaskHandler(runtime)(
        organization_id=str(ORG),
        message_id=str(dispatch_message),
    )
    fixture = dict(FrozenFixtureStore().load("ai-events-v1.1.0.json")["retryable_failed"])
    fixture["sequence"] = 1
    async with session_scope(foundation_session_factory) as session:
        await OutboxService(
            OutboxMessageRepository(session),
            token_factory=ids,
            clock=lambda: NOW,
        ).create(
            OutboxDraft(
                organization_id=ORG,
                message_id=EVENT_MESSAGE,
                aggregate_type="ai_review_run",
                aggregate_id=RUN,
                event_type="AIReviewEventReceived",
                payload_version="1.1.0",
                payload={
                    "organization_id": str(ORG),
                    "run_id": str(RUN),
                    "component_id": "offline-ai-component",
                    "event": fixture,
                },
                available_at=NOW,
                max_attempts=3,
            )
        )

    result = await AIReviewEventTaskHandler(runtime)(
        organization_id=str(ORG),
        message_id=str(EVENT_MESSAGE),
    )

    assert result["disposition"] == "stale"
    assert result["run_state"] == "stale"
    assert [entry[2] for entry in authorization.calls] == ["early", "final"]
    assert len(audit.events) == 1
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, RUN)
        attempts = (
            await session.scalars(
                select(OperationAttempt).order_by(OperationAttempt.attempt_number)
            )
        ).all()
        assert operation is not None and operation.state == "stale"
        assert [item.outcome for item in attempts] == ["processing", "action_required"]
        assert str(CREDENTIAL) in attempts[1].worker_identity
        assert "@1" in attempts[1].worker_identity


async def test_operation_recorder_deduplicates_exact_event_attempt_identity(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    ids = IDs(16000)
    await _schedule(foundation_session_factory, ids)
    recorder = SqlAIEventOperationRecorder(id_factory=ids, clock=lambda: NOW)
    async with session_scope(foundation_session_factory) as session:
        await recorder.record_event(
            organization_id=ORG,
            run_id=RUN,
            attempt_id=ATTEMPT,
            sequence=1,
            state="partial",
            error=None,
            transaction=session,
        )
        await recorder.record_event(
            organization_id=ORG,
            run_id=RUN,
            attempt_id=ATTEMPT,
            sequence=1,
            state="partial",
            error=None,
            transaction=session,
        )
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, RUN)
        attempts = (await session.scalars(select(OperationAttempt))).all()
        assert operation is not None and operation.state == "partial"
        assert len(attempts) == 1


def test_registry_loads_real_ai_handlers() -> None:
    assert "review_platform.infrastructure.tasks.ai_review" in HANDLER_MODULES
    dispatch = REGISTRY.resolve_event("AIReviewRequested")
    event = REGISTRY.resolve_event("AIReviewEventReceived")
    assert (dispatch.name, dispatch.kind) == (
        "review_platform.ai_review_dispatch",
        "ai_review",
    )
    assert (event.name, event.kind) == ("review_platform.ai_review_event", "ai_review")
    assert dispatch.requires_auth_revalidation is False
    assert event.requires_auth_revalidation is False
