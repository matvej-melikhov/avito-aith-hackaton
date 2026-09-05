"""Sequenced idempotent AI event ingestion tests."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from tests.support.contracts import load_fixture

from review_platform.application.services.ai_review_events import (
    AIEventCollision,
    AIEventContext,
    AIEventRejected,
    AIEventSequenceConflict,
    AIReviewEventService,
)
from review_platform.contracts.ai_review import AIReviewRequest
from review_platform.infrastructure.db.models import (
    AICriterionSuggestion,
    AIReviewAttempt,
    AIReviewEventReceipt,
    AIReviewRun,
    AISignal,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
RUN = UUID("00000000-0000-7000-8000-000000000060")
ATTEMPT = UUID("00000000-0000-7000-8000-000000000061")
CRITERION = UUID("00000000-0000-7000-8000-000000000063")


def _request() -> AIReviewRequest:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    immutable = vector["input"]
    return AIReviewRequest.model_validate(
        {
            "contract_version": "1.1.0",
            "run_id": str(RUN),
            "input_fingerprint": vector["expected_fingerprint"],
            "fingerprint_algorithm": vector["algorithm"],
            "organization_id": immutable["organization_id"],
            "course_run_id": immutable["course_run_id"],
            "submission_version_id": immutable["submission_version_id"],
            "review_iteration_id": immutable["review_iteration_id"],
            "credential_binding_id": "00000000-0000-7000-8000-000000000051",
            "credential_binding_version": 7,
            "artifact": {
                "contract_version": "1.1.0",
                "organization_id": immutable["organization_id"],
                "artifact_reference_id": "00000000-0000-7000-8000-000000000008",
                "artifact_version_id": immutable["artifact_version_id"],
                "provider": "github",
                "provider_version": "fixture-1",
                "content_digest": immutable["artifact_content_digest"],
                "captured_at": "2026-09-05T11:55:00Z",
                "object": {
                    "key": "tenant/artifact.zip",
                    "media_type": "application/zip",
                    "byte_size": 128,
                },
                "metadata": {},
            },
            "artifact_download": {
                "url": "https://objects.example.test/signed-artifact",
                "expires_at": "2026-09-05T12:15:00Z",
            },
            "homework": {
                "version_id": immutable["homework_version_id"],
                "title": "Homework",
                "student_text": "Submit",
                "digest": immutable["homework_digest"],
            },
            "criteria": {
                "set_id": immutable["criterion_set_id"],
                "digest": immutable["criterion_set_digest"],
                "items": [
                    {
                        "id": str(CRITERION),
                        "key": "correctness",
                        "title": "Correctness",
                        "description": "Correct",
                        "max_points": 5,
                    }
                ],
            },
        }
    )


def _run(*, current_attempt: int = 1, status: str = "running") -> AIReviewRun:
    request = _request()
    return AIReviewRun(
        id=RUN,
        organization_id=ORG,
        review_iteration_id=request.review_iteration_id,
        course_run_id=request.course_run_id,
        submission_version_id=request.submission_version_id,
        artifact_version_id=request.artifact.artifact_version_id,
        content_digest=request.artifact.content_digest,
        homework_version_id=request.homework.version_id,
        homework_digest=request.homework.digest,
        criterion_set_id=request.criteria.set_id,
        criteria_digest=request.criteria.digest,
        contract_version="1.1.0",
        fingerprint_algorithm="jcs-sha256-v1",
        input_fingerprint=request.input_fingerprint,
        status=status,
        current_attempt_no=current_attempt,
        revision=0,
    )


def _attempt(*, number: int = 1, status: str = "running", sequence: int = 0) -> AIReviewAttempt:
    return AIReviewAttempt(
        id=ATTEMPT,
        organization_id=ORG,
        ai_review_run_id=RUN,
        attempt_number=number,
        credential_binding_id=UUID("00000000-0000-7000-8000-000000000051"),
        credential_binding_version=7,
        status=status,
        last_sequence=sequence,
    )


class FakeRepository:
    def __init__(self, context: AIEventContext) -> None:
        self.context = context
        self.events: dict[UUID, AIReviewEventReceipt] = {}
        self.suggestions: list[AICriterionSuggestion] = []
        self.signals: list[AISignal] = []

    async def reserve_event(
        self, candidate: AIReviewEventReceipt
    ) -> tuple[AIReviewEventReceipt, bool]:
        existing = self.events.get(candidate.event_id)
        if existing is not None:
            if existing.payload_digest != candidate.payload_digest:
                raise ValueError("event collision")
            return existing, False
        self.events[candidate.event_id] = candidate
        return candidate, True

    async def append_outputs(
        self, suggestions: Sequence[AICriterionSuggestion], signal: AISignal | None
    ) -> None:
        self.suggestions.extend(suggestions)
        if signal is not None:
            self.signals.append(signal)

    async def transition_attempt(
        self,
        organization_id: UUID,
        attempt_id: UUID,
        *,
        expected_status: str,
        expected_last_sequence: int,
        new_status: str,
        new_sequence: int,
        error: Any = None,
    ) -> bool:
        del error
        attempt = self.context.attempt
        if (
            organization_id != ORG
            or attempt_id != attempt.id
            or attempt.status != expected_status
            or attempt.last_sequence != expected_last_sequence
        ):
            return False
        attempt.status = new_status
        attempt.last_sequence = new_sequence
        return True

    async def transition_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        expected_revision: int,
        expected_status: str,
        current_attempt_no: int,
        new_status: str,
    ) -> bool:
        run = self.context.run
        if run.status in {"succeeded", "action_required", "stale"}:
            raise ValueError("terminal run")
        if (
            organization_id != ORG
            or run_id != run.id
            or run.revision != expected_revision
            or run.status != expected_status
            or run.current_attempt_no != current_attempt_no
        ):
            return False
        run.status = new_status
        run.revision += 1
        return True


class FakeContexts:
    def __init__(self, context: AIEventContext) -> None:
        self.context = context

    async def resolve(self, **_: object) -> AIEventContext:
        return self.context


class FakeAuthorization:
    def __init__(self) -> None:
        self.authorized = 0
        self.revalidated = 0

    async def authorize_component(self, **_: object) -> None:
        self.authorized += 1

    async def revalidate_component(self, **_: object) -> None:
        self.revalidated += 1


class FakeOperations:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record_event(self, **values: object) -> None:
        self.events.append(values)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def record_component_event(self, **values: object) -> None:
        self.events.append(values)


def _service(
    context: AIEventContext,
) -> tuple[AIReviewEventService, FakeRepository, FakeAuthorization, FakeOperations, FakeAudit]:
    repository = FakeRepository(context)
    authorization = FakeAuthorization()
    operations = FakeOperations()
    audit = FakeAudit()
    counter = iter(UUID(f"00000000-0000-7000-8000-{value:012d}") for value in range(200, 220))
    return (
        AIReviewEventService(
            repository=repository,
            contexts=FakeContexts(context),
            authorization=authorization,
            operations=operations,
            audit=audit,
            id_factory=lambda: next(counter),
            clock=lambda: datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        ),
        repository,
        authorization,
        operations,
        audit,
    )


def _events() -> dict[str, dict[str, Any]]:
    return load_fixture("ai-events-v1.1.0.json")


@pytest.mark.anyio
async def test_success_applies_once_and_identical_event_replays_without_human_mutation() -> None:
    human_bytes = b"immutable-human-review"
    context = AIEventContext(_run(), _attempt(), _request(), True)
    service, repository, authorization, operations, audit = _service(context)
    payload = _events()["succeeded"]
    first = await service.ingest(
        payload, organization_id=ORG, component_id="ai-component", transaction=object()
    )
    replay = await service.ingest(
        payload, organization_id=ORG, component_id="ai-component", transaction=object()
    )
    assert (first.disposition, first.run_state) == ("applied", "succeeded")
    assert replay.disposition == "replay"
    assert len(repository.events) == len(repository.suggestions) == len(repository.signals) == 1
    assert authorization.authorized == 2 and authorization.revalidated == 1
    assert len(operations.events) == len(audit.events) == 1
    assert human_bytes == b"immutable-human-review"


@pytest.mark.anyio
async def test_event_id_collision_old_attempt_and_stale_inputs_never_replace_current() -> None:
    payload = _events()["succeeded"]
    context = AIEventContext(_run(current_attempt=2), _attempt(number=1), _request(), True)
    service, repository, _, _, _ = _service(context)
    old = await service.ingest(
        payload, organization_id=ORG, component_id="ai", transaction=object()
    )
    assert old.disposition == "old_attempt"
    assert context.run.status == "running" and context.run.current_attempt_no == 2
    collision = deepcopy(payload)
    collision["suggestions"][0]["reason"] = "different bytes"
    with pytest.raises(AIEventCollision):
        await service.ingest(
            collision, organization_id=ORG, component_id="ai", transaction=object()
        )

    stale_context = AIEventContext(_run(), _attempt(), _request(), False)
    stale_service, _, _, _, _ = _service(stale_context)
    stale = await stale_service.ingest(
        payload, organization_id=ORG, component_id="ai", transaction=object()
    )
    assert stale.disposition == stale.run_state == "stale"


@pytest.mark.anyio
async def test_out_of_order_unsupported_version_and_fingerprint_mismatch_are_rejected() -> None:
    context = AIEventContext(_run(), _attempt(), _request(), True)
    service, repository, _, _, _ = _service(context)
    out_of_order = _events()["retryable_failed"]
    with pytest.raises(AIEventSequenceConflict):
        await service.ingest(
            out_of_order, organization_id=ORG, component_id="ai", transaction=object()
        )
    assert len(repository.events) == 1

    unsupported = deepcopy(_events()["succeeded"])
    unsupported["contract_version"] = "1.0.0"
    with pytest.raises(AIEventRejected, match="1.1.0"):
        await service.ingest(
            unsupported, organization_id=ORG, component_id="ai", transaction=object()
        )
    mismatch = deepcopy(_events()["succeeded"])
    mismatch["input_fingerprint"] = "sha256:" + "f" * 64
    with pytest.raises(AIEventRejected, match="fingerprint"):
        await service.ingest(mismatch, organization_id=ORG, component_id="ai", transaction=object())


@pytest.mark.anyio
async def test_typed_failure_updates_current_attempt_and_operation_without_outputs() -> None:
    context = AIEventContext(_run(), _attempt(sequence=1), _request(), True)
    service, repository, authorization, operations, audit = _service(context)
    result = await service.ingest(
        _events()["retryable_failed"], organization_id=ORG, component_id="ai", transaction=object()
    )
    assert result.run_state == "retryable_failed"
    assert context.attempt.status == "retryable_failed" and context.attempt.last_sequence == 2
    assert repository.suggestions == []
    assert repository.signals == []
    assert operations.events[0]["error"] == {
        "code": "model_unavailable",
        "message": "Model unavailable",
        "retryable": True,
    }
    assert authorization.revalidated == len(audit.events) == 1
