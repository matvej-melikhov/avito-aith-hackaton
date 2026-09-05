"""Tenant-scoped delivery claims, CAS transitions, and recovery history."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.services.deliveries import (
    DeliveryProvenance,
    DeliveryRecord,
    DeliveryRepository,
    DeliveryState,
)
from review_platform.domain.primitives import require_utc, sanitize_error, utc_now, uuid7
from review_platform.infrastructure.db.models.delivery import (
    DELIVERY_ATTEMPT_OUTCOMES,
    DELIVERY_RECONCILIATION_OUTCOMES,
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
from review_platform.infrastructure.db.models.identity import ExternalCredential
from review_platform.infrastructure.db.models.learning import DestinationBinding
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.models.publication import (
    ExternalDelivery,
    ReviewPublication,
)
from review_platform.infrastructure.db.models.review_case import ReviewIteration

_CLAIMABLE_STATES = ("pending", "retryable_failed")
_TERMINAL_STATES = ("succeeded", "action_required", "superseded")


class DeliveryRepositoryError(RuntimeError):
    pass


class InvalidDeliveryTransaction(DeliveryRepositoryError):
    pass


class DeliveryPersistenceConflict(DeliveryRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    delivery: DeliveryRecord
    attempt_id: UUID
    claim_token: UUID
    worker_identity: str
    lease_expires_at: datetime
    delivery_key: str
    provider_kind: str
    credential_binding_id: UUID
    credential_binding_version: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReconciliationObservationDraft:
    observation_id: UUID
    organization_id: UUID
    delivery_id: UUID
    delivery_attempt_id: UUID
    attempt_number: int
    operation_id: UUID
    credential_binding_id: UUID
    credential_binding_version: int
    request_payload_version: str
    request_digest: str
    result_payload_version: str
    result_digest: str
    observed_at: datetime
    outcome: str
    external_id: str | None = None
    external_url: str | None = None
    error: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class DeliveryHistory:
    delivery: DeliveryRecord
    attempts: tuple[DeliveryAttempt, ...]
    observations: tuple[DeliveryReconciliationObservation, ...]


class SqlDeliveryRepository:
    """MySQL delivery repository; every mutation remains caller-transactional."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
        default_lease: timedelta = timedelta(minutes=5),
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if default_lease <= timedelta(0):
            raise ValueError("default lease must be positive")
        self._id_factory = id_factory
        self._clock = clock
        self._max_attempts = max_attempts
        self._default_lease = default_lease

    async def lock(
        self,
        organization_id: UUID,
        delivery_id: UUID,
        *,
        transaction: object,
    ) -> DeliveryRecord | None:
        session = _session(transaction)
        row = await session.scalar(
            select(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == organization_id,
                ExternalDelivery.id == delivery_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            return None
        await self._validate_snapshot(session, row)
        return self._record(row)

    async def transition(
        self,
        delivery: DeliveryRecord,
        *,
        target_state: DeliveryState,
        next_attempt_at: datetime | None,
        error: Mapping[str, object] | None,
        transaction: object,
    ) -> DeliveryRecord:
        session = _session(transaction)
        if target_state not in {
            "pending",
            "processing",
            "retryable_failed",
            "unknown_outcome",
            "reconciling",
            "succeeded",
            "action_required",
            "superseded",
        }:
            raise DeliveryPersistenceConflict("unknown delivery target state")
        now = require_utc(self._clock())
        retry_at = require_utc(next_attempt_at) if next_attempt_at is not None else None
        sanitized = sanitize_error(error) if error is not None else None
        row = await session.scalar(
            select(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == delivery.organization_id,
                ExternalDelivery.id == delivery.delivery_id,
                ExternalDelivery.revision == delivery.revision,
                ExternalDelivery.state == delivery.state,
                ExternalDelivery.attempt_count == delivery.attempt_count,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise DeliveryPersistenceConflict("delivery transition CAS lost")
        await self._validate_snapshot(session, row)
        next_count = row.attempt_count
        if target_state == "processing":
            if row.attempt_count >= self._max_attempts:
                raise DeliveryPersistenceConflict("delivery attempt budget is exhausted")
            next_count += 1
            await self._append_processing_attempt(
                session,
                row,
                attempt_number=next_count,
                worker_identity="delivery-service",
                claim_token=self._id_factory(),
                started_at=now,
                lease_expires_at=now + self._default_lease,
            )
        elif target_state in DELIVERY_ATTEMPT_OUTCOMES and row.attempt_count > 0:
            await self._finish_current_attempt(
                session,
                row,
                target_state=target_state,
                finished_at=now,
                error=sanitized,
            )
        result = await session.execute(
            update(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == row.organization_id,
                ExternalDelivery.id == row.id,
                ExternalDelivery.revision == row.revision,
                ExternalDelivery.state == row.state,
                ExternalDelivery.attempt_count == row.attempt_count,
            )
            .values(
                state=target_state,
                attempt_count=next_count,
                next_attempt_at=retry_at,
                last_error_code=(str(sanitized.get("code")) if sanitized is not None else None),
                sanitized_error=sanitized,
                revision=row.revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        if result.rowcount != 1:
            raise DeliveryPersistenceConflict("delivery transition CAS lost")
        stored = await session.scalar(
            select(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == row.organization_id,
                ExternalDelivery.id == row.id,
            )
            .execution_options(populate_existing=True)
        )
        if stored is None:
            raise DeliveryPersistenceConflict("transitioned delivery disappeared")
        return self._record(stored)

    async def claim_batch(
        self,
        organization_id: UUID,
        provider_kind: str,
        *,
        worker_identity: str,
        now: datetime,
        lease_for: timedelta,
        limit: int,
        transaction: object,
    ) -> tuple[DeliveryClaim, ...]:
        session = _session(transaction)
        claimed_at = require_utc(now)
        if provider_kind not in {"github", "stepik"}:
            raise ValueError("provider_kind must be github or stepik")
        if not worker_identity or len(worker_identity) > 255:
            raise ValueError("worker_identity must contain 1..255 characters")
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive")
        if limit < 1 or limit > 100:
            raise ValueError("claim limit must be in 1..100")
        expired_processing = exists(
            select(DeliveryAttempt.id).where(
                DeliveryAttempt.organization_id == ExternalDelivery.organization_id,
                DeliveryAttempt.delivery_id == ExternalDelivery.id,
                DeliveryAttempt.attempt_number == ExternalDelivery.attempt_count,
                DeliveryAttempt.state == "processing",
                DeliveryAttempt.lease_expires_at <= claimed_at,
            )
        )
        rows = (
            await session.scalars(
                select(ExternalDelivery)
                .where(
                    ExternalDelivery.organization_id == organization_id,
                    ExternalDelivery.destination_kind == provider_kind,
                    or_(
                        and_(
                            ExternalDelivery.state.in_(_CLAIMABLE_STATES),
                            or_(
                                ExternalDelivery.next_attempt_at.is_(None),
                                ExternalDelivery.next_attempt_at <= claimed_at,
                            ),
                        ),
                        and_(
                            ExternalDelivery.state == "processing",
                            expired_processing,
                        ),
                    ),
                )
                .order_by(
                    ExternalDelivery.next_attempt_at,
                    ExternalDelivery.created_at,
                    ExternalDelivery.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).all()
        claims: list[DeliveryClaim] = []
        for row in rows:
            if await self._is_stale_publication(session, row):
                await self._set_locked_state(session, row, "superseded", claimed_at)
                continue
            await self._validate_snapshot(session, row)
            if row.state == "processing":
                if row.attempt_count >= self._max_attempts:
                    await self._finish_current_attempt(
                        session,
                        row,
                        target_state="action_required",
                        finished_at=claimed_at,
                        error={
                            "code": "delivery_lease_expired",
                            "message": "Delivery worker lease expired at attempt limit",
                            "action": "operator_review",
                        },
                    )
                    await self._set_locked_state(
                        session,
                        row,
                        "action_required",
                        claimed_at,
                    )
                    continue
                await self._finish_current_attempt(
                    session,
                    row,
                    target_state="retryable_failed",
                    finished_at=claimed_at,
                    error={
                        "code": "delivery_lease_expired",
                        "message": "Delivery worker lease expired",
                        "action": "retry",
                    },
                )
            if row.attempt_count >= self._max_attempts:
                await self._set_locked_state(
                    session,
                    row,
                    "action_required",
                    claimed_at,
                )
                continue
            attempt_number = row.attempt_count + 1
            claim_token = self._id_factory()
            attempt = await self._append_processing_attempt(
                session,
                row,
                attempt_number=attempt_number,
                worker_identity=worker_identity,
                claim_token=claim_token,
                started_at=claimed_at,
                lease_expires_at=claimed_at + lease_for,
            )
            row.state = "processing"
            row.attempt_count = attempt_number
            row.next_attempt_at = None
            row.last_error_code = None
            row.sanitized_error = None
            row.revision += 1
            row.updated_at = claimed_at
            await session.flush([row])
            claims.append(
                DeliveryClaim(
                    delivery=self._record(row),
                    attempt_id=attempt.id,
                    claim_token=attempt.claim_token,
                    worker_identity=attempt.worker_identity,
                    lease_expires_at=attempt.lease_expires_at,
                    delivery_key=row.delivery_key,
                    provider_kind=row.destination_kind,
                    credential_binding_id=row.credential_binding_id,
                    credential_binding_version=row.credential_binding_version,
                    payload=cast(Mapping[str, Any], row.payload),
                )
            )
        return tuple(claims)

    async def supersede_stale_publications(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        review_iteration_id: UUID,
        publication_id: UUID,
        publication_version: int,
        *,
        transaction: object,
    ) -> int:
        session = _session(transaction)
        if publication_version < 1:
            raise ValueError("publication_version must be positive")
        current = (
            await session.execute(
                select(
                    ReviewPublication.id,
                    ReviewIteration.review_case_id,
                    ReviewIteration.iteration_number,
                )
                .join(
                    ReviewIteration,
                    and_(
                        ReviewIteration.organization_id == ReviewPublication.organization_id,
                        ReviewIteration.id == ReviewPublication.review_iteration_id,
                    ),
                )
                .where(
                    ReviewPublication.organization_id == organization_id,
                    ReviewPublication.id == publication_id,
                    ReviewPublication.review_iteration_id == review_iteration_id,
                    ReviewPublication.publication_version == publication_version,
                )
            )
        ).one_or_none()
        if current is None:
            return 0
        _, review_case_id, iteration_number = current
        stale_ids = (
            await session.scalars(
                select(ExternalDelivery.id)
                .join(
                    ReviewPublication,
                    and_(
                        ReviewPublication.organization_id == ExternalDelivery.organization_id,
                        ReviewPublication.id == ExternalDelivery.publication_id,
                    ),
                )
                .join(
                    ReviewIteration,
                    and_(
                        ReviewIteration.organization_id == ExternalDelivery.organization_id,
                        ReviewIteration.id == ExternalDelivery.review_iteration_id,
                    ),
                )
                .where(
                    ExternalDelivery.organization_id == organization_id,
                    ExternalDelivery.course_run_id == course_run_id,
                    ExternalDelivery.publication_id != publication_id,
                    ExternalDelivery.state.not_in(_TERMINAL_STATES),
                    ReviewIteration.review_case_id == review_case_id,
                    or_(
                        ReviewIteration.iteration_number < iteration_number,
                        and_(
                            ReviewIteration.iteration_number == iteration_number,
                            ReviewPublication.publication_version < publication_version,
                        ),
                    ),
                )
                .order_by(ExternalDelivery.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not stale_ids:
            return 0
        result = await session.execute(
            update(ExternalDelivery)
            .where(
                ExternalDelivery.organization_id == organization_id,
                ExternalDelivery.id.in_(stale_ids),
                ExternalDelivery.state.not_in(_TERMINAL_STATES),
            )
            .values(
                state="superseded",
                next_attempt_at=None,
                last_error_code=None,
                sanitized_error=None,
                revision=ExternalDelivery.revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount

    async def append_reconciliation_observation(
        self,
        draft: ReconciliationObservationDraft,
        *,
        transaction: object,
    ) -> tuple[DeliveryReconciliationObservation, bool]:
        session = _session(transaction)
        if draft.outcome not in DELIVERY_RECONCILIATION_OUTCOMES:
            raise DeliveryPersistenceConflict("unknown reconciliation outcome")
        _digest(draft.request_digest, field="request_digest")
        _digest(draft.result_digest, field="result_digest")
        attempt = await session.scalar(
            select(DeliveryAttempt).where(
                DeliveryAttempt.organization_id == draft.organization_id,
                DeliveryAttempt.delivery_id == draft.delivery_id,
                DeliveryAttempt.id == draft.delivery_attempt_id,
                DeliveryAttempt.attempt_number == draft.attempt_number,
                DeliveryAttempt.operation_id == draft.operation_id,
                DeliveryAttempt.credential_binding_id == draft.credential_binding_id,
                DeliveryAttempt.credential_binding_version == draft.credential_binding_version,
            )
        )
        if attempt is None:
            raise DeliveryPersistenceConflict("reconciliation attempt snapshot is invalid")
        existing = await session.scalar(
            select(DeliveryReconciliationObservation).where(
                DeliveryReconciliationObservation.organization_id == draft.organization_id,
                DeliveryReconciliationObservation.delivery_id == draft.delivery_id,
                DeliveryReconciliationObservation.delivery_attempt_id == draft.delivery_attempt_id,
                DeliveryReconciliationObservation.result_digest == draft.result_digest,
            )
        )
        if existing is not None:
            self._require_same_observation(existing, draft)
            return existing, False
        sanitized = sanitize_error(draft.error) if draft.error is not None else None
        row = DeliveryReconciliationObservation(
            id=draft.observation_id,
            organization_id=draft.organization_id,
            delivery_id=draft.delivery_id,
            external_delivery_id=draft.delivery_id,
            delivery_attempt_id=draft.delivery_attempt_id,
            attempt_number=draft.attempt_number,
            operation_id=draft.operation_id,
            credential_binding_id=draft.credential_binding_id,
            credential_binding_version=draft.credential_binding_version,
            request_payload_version=draft.request_payload_version,
            request_digest=draft.request_digest,
            result_payload_version=draft.result_payload_version,
            result_digest=draft.result_digest,
            observed_at=require_utc(draft.observed_at),
            outcome=draft.outcome,
            external_id=draft.external_id,
            external_url=draft.external_url,
            error_code=(str(sanitized.get("code")) if sanitized is not None else None),
            sanitized_error=sanitized,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush([row])
        except IntegrityError:
            existing = await session.scalar(
                select(DeliveryReconciliationObservation)
                .where(
                    DeliveryReconciliationObservation.organization_id == draft.organization_id,
                    DeliveryReconciliationObservation.delivery_id == draft.delivery_id,
                    DeliveryReconciliationObservation.delivery_attempt_id
                    == draft.delivery_attempt_id,
                    DeliveryReconciliationObservation.result_digest == draft.result_digest,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if existing is None:
                raise
            self._require_same_observation(existing, draft)
            return existing, False
        return row, True

    async def history(
        self,
        organization_id: UUID,
        delivery_id: UUID,
        *,
        transaction: object,
    ) -> DeliveryHistory | None:
        session = _session(transaction)
        row = await session.scalar(
            select(ExternalDelivery).where(
                ExternalDelivery.organization_id == organization_id,
                ExternalDelivery.id == delivery_id,
            )
        )
        if row is None:
            return None
        attempts = (
            await session.scalars(
                select(DeliveryAttempt)
                .where(
                    DeliveryAttempt.organization_id == organization_id,
                    DeliveryAttempt.delivery_id == delivery_id,
                )
                .order_by(DeliveryAttempt.attempt_number, DeliveryAttempt.id)
            )
        ).all()
        observations = (
            await session.scalars(
                select(DeliveryReconciliationObservation)
                .where(
                    DeliveryReconciliationObservation.organization_id == organization_id,
                    DeliveryReconciliationObservation.delivery_id == delivery_id,
                )
                .order_by(
                    DeliveryReconciliationObservation.attempt_number,
                    DeliveryReconciliationObservation.observed_at,
                    DeliveryReconciliationObservation.id,
                )
            )
        ).all()
        return DeliveryHistory(self._record(row), tuple(attempts), tuple(observations))

    async def _validate_snapshot(
        self,
        session: AsyncSession,
        row: ExternalDelivery,
    ) -> None:
        binding = await session.scalar(
            select(DestinationBinding).where(
                DestinationBinding.organization_id == row.organization_id,
                DestinationBinding.id == row.destination_binding_id,
                DestinationBinding.binding_version == row.binding_version,
            )
        )
        credential = await session.scalar(
            select(ExternalCredential.id).where(
                ExternalCredential.organization_id == row.organization_id,
                ExternalCredential.id == row.credential_binding_id,
                ExternalCredential.binding_version == row.credential_binding_version,
            )
        )
        operation = await session.scalar(
            select(Operation).where(
                Operation.organization_id == row.organization_id,
                Operation.id == row.operation_id,
                Operation.kind == "external_delivery",
            )
        )
        publication = await session.scalar(
            select(ReviewPublication.id).where(
                ReviewPublication.organization_id == row.organization_id,
                ReviewPublication.id == row.publication_id,
                ReviewPublication.review_iteration_id == row.review_iteration_id,
                ReviewPublication.review_revision_id == row.review_revision_id,
            )
        )
        if (
            binding is None
            or credential is None
            or operation is None
            or publication is None
            or binding.course_run_id != row.course_run_id
            or binding.kind != row.destination_kind
            or binding.recipient_ref != row.recipient_ref
            or binding.credential_id != row.credential_binding_id
            or binding.credential_binding_version != row.credential_binding_version
        ):
            raise DeliveryPersistenceConflict(
                "delivery destination, credential, operation, or publication snapshot mismatched"
            )

    async def _append_processing_attempt(
        self,
        session: AsyncSession,
        row: ExternalDelivery,
        *,
        attempt_number: int,
        worker_identity: str,
        claim_token: UUID,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> DeliveryAttempt:
        attempt = DeliveryAttempt(
            id=self._id_factory(),
            organization_id=row.organization_id,
            delivery_id=row.id,
            external_delivery_id=row.id,
            operation_id=row.operation_id,
            attempt_number=attempt_number,
            credential_binding_id=row.credential_binding_id,
            credential_binding_version=row.credential_binding_version,
            worker_identity=worker_identity,
            claim_token=claim_token,
            lease_expires_at=lease_expires_at,
            state="processing",
            started_at=started_at,
            finished_at=None,
            outcome="processing",
            error_code=None,
            sanitized_error=None,
        )
        session.add(attempt)
        await session.flush([attempt])
        return attempt

    @staticmethod
    async def _finish_current_attempt(
        session: AsyncSession,
        row: ExternalDelivery,
        *,
        target_state: str,
        finished_at: datetime,
        error: Mapping[str, object] | None,
    ) -> None:
        if target_state not in DELIVERY_ATTEMPT_OUTCOMES:
            return
        sanitized = sanitize_error(error) if error is not None else None
        result = await session.execute(
            update(DeliveryAttempt)
            .where(
                DeliveryAttempt.organization_id == row.organization_id,
                DeliveryAttempt.delivery_id == row.id,
                DeliveryAttempt.attempt_number == row.attempt_count,
                DeliveryAttempt.state == "processing",
            )
            .values(
                state=target_state,
                outcome=target_state,
                finished_at=finished_at,
                error_code=(str(sanitized.get("code")) if sanitized is not None else None),
                sanitized_error=sanitized,
            )
        )
        if result.rowcount != 1:
            raise DeliveryPersistenceConflict("current delivery attempt was not claimable")

    @staticmethod
    async def _set_locked_state(
        session: AsyncSession,
        row: ExternalDelivery,
        state: DeliveryState,
        now: datetime,
    ) -> None:
        row.state = state
        row.next_attempt_at = None
        row.revision += 1
        row.updated_at = now
        await session.flush([row])

    @staticmethod
    async def _is_stale_publication(
        session: AsyncSession,
        row: ExternalDelivery,
    ) -> bool:
        current = (
            await session.execute(
                select(
                    ReviewPublication.publication_version,
                    ReviewIteration.review_case_id,
                    ReviewIteration.iteration_number,
                )
                .join(
                    ReviewIteration,
                    and_(
                        ReviewIteration.organization_id == ReviewPublication.organization_id,
                        ReviewIteration.id == ReviewPublication.review_iteration_id,
                    ),
                )
                .where(
                    ReviewPublication.organization_id == row.organization_id,
                    ReviewPublication.id == row.publication_id,
                    ReviewIteration.id == row.review_iteration_id,
                )
            )
        ).one_or_none()
        if current is None:
            return True
        current_version, review_case_id, iteration_number = current
        newer = await session.scalar(
            select(ReviewPublication.id)
            .join(
                ReviewIteration,
                and_(
                    ReviewIteration.organization_id == ReviewPublication.organization_id,
                    ReviewIteration.id == ReviewPublication.review_iteration_id,
                ),
            )
            .where(
                ReviewPublication.organization_id == row.organization_id,
                ReviewIteration.review_case_id == review_case_id,
                or_(
                    ReviewIteration.iteration_number > iteration_number,
                    and_(
                        ReviewIteration.iteration_number == iteration_number,
                        ReviewPublication.publication_version > current_version,
                    ),
                ),
            )
            .limit(1)
        )
        return newer is not None

    def _record(self, row: ExternalDelivery) -> DeliveryRecord:
        return DeliveryRecord(
            organization_id=row.organization_id,
            delivery_id=row.id,
            state=cast(DeliveryState, row.state),
            attempt_count=row.attempt_count,
            max_attempts=self._max_attempts,
            revision=row.revision,
            provenance=DeliveryProvenance(
                course_run_id=row.course_run_id,
                homework_version_id=row.homework_version_id,
                criterion_set_id=row.criterion_set_id,
                submission_version_id=row.submission_version_id,
                artifact_version_id=row.artifact_version_id,
                artifact_content_digest=row.artifact_content_digest,
                review_iteration_id=row.review_iteration_id,
                review_revision_id=row.review_revision_id,
                contract_version=row.contract_version,
            ),
        )

    @staticmethod
    def _require_same_observation(
        row: DeliveryReconciliationObservation,
        draft: ReconciliationObservationDraft,
    ) -> None:
        fields = (
            "organization_id",
            "delivery_id",
            "delivery_attempt_id",
            "attempt_number",
            "operation_id",
            "credential_binding_id",
            "credential_binding_version",
            "request_payload_version",
            "request_digest",
            "result_payload_version",
            "result_digest",
            "outcome",
            "external_id",
            "external_url",
        )
        if any(getattr(row, field) != getattr(draft, field) for field in fields):
            raise DeliveryPersistenceConflict(
                "reconciliation observation replay provenance mismatched"
            )


def _digest(value: str, *, field: str) -> None:
    if len(value) != 71 or not value.startswith("sha256:"):
        raise DeliveryPersistenceConflict(f"{field} is not a canonical sha256 digest")


def _session(transaction: object) -> AsyncSession:
    if not isinstance(transaction, AsyncSession):
        raise InvalidDeliveryTransaction("delivery repository requires caller-owned AsyncSession")
    return transaction


_delivery_port: DeliveryRepository = SqlDeliveryRepository()


__all__ = [
    "DeliveryClaim",
    "DeliveryHistory",
    "DeliveryPersistenceConflict",
    "DeliveryRepositoryError",
    "InvalidDeliveryTransaction",
    "ReconciliationObservationDraft",
    "SqlDeliveryRepository",
]
