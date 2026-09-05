"""Component-authorized, sequenced, idempotent AI event ingestion."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from review_platform.contracts.ai_review import (
    AIReviewEvent,
    AIReviewRequest,
    validate_event_against_request,
)
from review_platform.domain.primitives import canonical_json_sha256, utc_now, uuid7
from review_platform.infrastructure.db.models.ai_review import (
    AICriterionSuggestion,
    AIReviewAttempt,
    AIReviewEventReceipt,
    AIReviewRun,
)
from review_platform.infrastructure.db.models.ai_review import (
    AISignal as AISignalRow,
)


class AIEventIngestionError(RuntimeError):
    pass


class AIEventRejected(AIEventIngestionError):
    pass


class AIEventCollision(AIEventIngestionError):
    pass


class AIEventSequenceConflict(AIEventIngestionError):
    pass


@dataclass(frozen=True, slots=True)
class AIEventContext:
    run: AIReviewRun
    attempt: AIReviewAttempt
    request: AIReviewRequest
    immutable_inputs_current: bool


@dataclass(frozen=True, slots=True)
class AIEventIngestionResult:
    event_id: UUID
    run_id: UUID
    attempt_id: UUID
    sequence: int
    disposition: str
    run_state: str


class AIEventRepositoryPort(Protocol):
    async def reserve_event(
        self, candidate: AIReviewEventReceipt
    ) -> tuple[AIReviewEventReceipt, bool]: ...

    async def append_outputs(
        self,
        suggestions: Sequence[AICriterionSuggestion],
        signal: AISignalRow | None,
    ) -> None: ...

    async def transition_attempt(
        self,
        organization_id: UUID,
        attempt_id: UUID,
        *,
        expected_status: str,
        expected_last_sequence: int,
        new_status: str,
        new_sequence: int,
        error: Mapping[str, Any] | None = None,
    ) -> bool: ...

    async def transition_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        expected_revision: int,
        expected_status: str,
        current_attempt_no: int,
        new_status: str,
    ) -> bool: ...


class AIEventContextPort(Protocol):
    async def resolve(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        transaction: object,
    ) -> AIEventContext | None: ...


class AIComponentAuthorizationPort(Protocol):
    async def authorize_component(
        self, *, component_id: str, organization_id: UUID, transaction: object
    ) -> None: ...

    async def revalidate_component(
        self, *, component_id: str, organization_id: UUID, transaction: object
    ) -> None: ...


class AIEventOperationPort(Protocol):
    async def record_event(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        sequence: int,
        state: str,
        error: Mapping[str, Any] | None,
        transaction: object,
    ) -> None: ...


class AIEventAuditPort(Protocol):
    async def record_component_event(
        self,
        *,
        organization_id: UUID,
        component_id: str,
        run_id: UUID,
        attempt_id: UUID,
        event_id: UUID,
        sequence: int,
        disposition: str,
        transaction: object,
    ) -> None: ...


class AIReviewEventService:
    def __init__(
        self,
        *,
        repository: AIEventRepositoryPort,
        contexts: AIEventContextPort,
        authorization: AIComponentAuthorizationPort,
        operations: AIEventOperationPort,
        audit: AIEventAuditPort,
        id_factory: Any = uuid7,
        clock: Any = utc_now,
    ) -> None:
        self._repository = repository
        self._contexts = contexts
        self._authorization = authorization
        self._operations = operations
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def ingest(
        self,
        payload: Mapping[str, Any],
        *,
        organization_id: UUID,
        component_id: str,
        transaction: object,
    ) -> AIEventIngestionResult:
        try:
            event = AIReviewEvent.model_validate(dict(payload))
        except ValidationError as error:
            raise AIEventRejected("AI event violates frozen 1.1.0 contract") from error
        await self._authorization.authorize_component(
            component_id=component_id,
            organization_id=organization_id,
            transaction=transaction,
        )
        context = await self._contexts.resolve(
            organization_id=organization_id,
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            transaction=transaction,
        )
        if context is None:
            raise AIEventRejected("exact tenant run/attempt/request context was not found")
        self._validate_context(context, event, organization_id=organization_id)
        try:
            validate_event_against_request(event, context.request)
        except ValueError as error:
            raise AIEventRejected(str(error)) from error
        receipt = AIReviewEventReceipt(
            event_id=event.event_id,
            organization_id=organization_id,
            ai_review_run_id=event.run_id,
            attempt_id=event.attempt_id,
            attempt_number=event.attempt_number,
            sequence=event.sequence,
            payload_digest=canonical_json_sha256(dict(payload)),
            status=event.status,
            is_final=event.is_final,
            received_at=self._clock(),
        )
        try:
            _, created = await self._repository.reserve_event(receipt)
        except Exception as error:
            raise AIEventCollision("global event ID collision") from error
        if not created:
            return AIEventIngestionResult(
                event.event_id,
                event.run_id,
                event.attempt_id,
                event.sequence,
                "replay",
                context.run.status,
            )
        if event.sequence != context.attempt.last_sequence + 1:
            raise AIEventSequenceConflict("event sequence is duplicate or out of order")
        suggestions, signal = self._outputs(event, organization_id=organization_id)
        await self._repository.append_outputs(suggestions, signal)
        error_payload = event.error.model_dump(mode="json") if event.error is not None else None
        if context.attempt.status not in {"succeeded", "action_required", "stale"}:
            updated_attempt = await self._repository.transition_attempt(
                organization_id,
                event.attempt_id,
                expected_status=context.attempt.status,
                expected_last_sequence=context.attempt.last_sequence,
                new_status=event.status,
                new_sequence=event.sequence,
                error=error_payload,
            )
            if not updated_attempt:
                raise AIEventSequenceConflict("attempt sequence/state CAS lost")
        is_current_attempt = context.run.current_attempt_no == event.attempt_number
        disposition = "old_attempt"
        run_state = context.run.status
        if is_current_attempt:
            target_state = event.status if context.immutable_inputs_current else "stale"
            updated_run = await self._repository.transition_run(
                organization_id,
                event.run_id,
                expected_revision=context.run.revision,
                expected_status=context.run.status,
                current_attempt_no=event.attempt_number,
                new_status=target_state,
            )
            if not updated_run:
                raise AIEventSequenceConflict("current run state CAS lost")
            disposition = "applied" if context.immutable_inputs_current else "stale"
            run_state = target_state
        await self._operations.record_event(
            organization_id=organization_id,
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            sequence=event.sequence,
            state=run_state,
            error=error_payload,
            transaction=transaction,
        )
        await self._audit.record_component_event(
            organization_id=organization_id,
            component_id=component_id,
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            event_id=event.event_id,
            sequence=event.sequence,
            disposition=disposition,
            transaction=transaction,
        )
        await self._authorization.revalidate_component(
            component_id=component_id,
            organization_id=organization_id,
            transaction=transaction,
        )
        return AIEventIngestionResult(
            event.event_id,
            event.run_id,
            event.attempt_id,
            event.sequence,
            disposition,
            run_state,
        )

    @staticmethod
    def _validate_context(
        context: AIEventContext,
        event: AIReviewEvent,
        *,
        organization_id: UUID,
    ) -> None:
        if (
            context.run.organization_id != organization_id
            or context.attempt.organization_id != organization_id
            or context.run.id != event.run_id
            or context.attempt.id != event.attempt_id
            or context.attempt.ai_review_run_id != event.run_id
            or context.attempt.attempt_number != event.attempt_number
            or context.request.organization_id != organization_id
            or context.request.run_id != event.run_id
            or context.run.input_fingerprint != event.input_fingerprint
        ):
            raise AIEventRejected("event tenant/run/attempt/fingerprint provenance mismatched")

    def _outputs(
        self,
        event: AIReviewEvent,
        *,
        organization_id: UUID,
    ) -> tuple[list[AICriterionSuggestion], AISignalRow | None]:
        suggestions = [
            AICriterionSuggestion(
                id=self._id_factory(),
                organization_id=organization_id,
                ai_review_run_id=event.run_id,
                event_id=event.event_id,
                criterion_id=item.criterion_id,
                status=item.status,
                proposed_points=(
                    Decimal(str(item.proposed_points))
                    if item.proposed_points is not None
                    else None
                ),
                reason=item.reason,
                evidence=[entry.model_dump(mode="json") for entry in item.evidence],
                confidence=item.confidence,
                reviewer_note=item.reviewer_note,
                student_feedback=item.student_feedback,
                flags=list(item.flags),
            )
            for item in event.suggestions
        ]
        signal = None
        if event.ai_signal is not None:
            signal = AISignalRow(
                id=self._id_factory(),
                organization_id=organization_id,
                ai_review_run_id=event.run_id,
                event_id=event.event_id,
                level=event.ai_signal.level,
                evidence=[entry.model_dump(mode="json") for entry in event.ai_signal.evidence],
                limitations=list(event.ai_signal.limitations),
                questions=list(event.ai_signal.questions),
            )
        return suggestions, signal


__all__ = [
    "AIComponentAuthorizationPort",
    "AIEventAuditPort",
    "AIEventCollision",
    "AIEventContext",
    "AIEventContextPort",
    "AIEventIngestionError",
    "AIEventIngestionResult",
    "AIEventOperationPort",
    "AIEventRejected",
    "AIEventRepositoryPort",
    "AIEventSequenceConflict",
    "AIReviewEventService",
]
