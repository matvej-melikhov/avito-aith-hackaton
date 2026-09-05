"""Tenant-safe retention scanning, claims, tombstones, purge, and audit."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from importlib import import_module
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import boto3
from anyio import to_thread
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import sanitize_shared_value
from review_platform.domain.primitives import require_utc, utc_now, uuid7, validate_digest
from review_platform.infrastructure.db.models.ai_review import AIReviewRun
from review_platform.infrastructure.db.models.delivery import (
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
from review_platform.infrastructure.db.models.learning import CourseRun
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    Operation,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.publication import (
    ExternalDelivery,
    ReviewIterationRelation,
    ReviewPublication,
)
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactPromotion,
    ArtifactVersion,
    SubmissionVersion,
)
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxService
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.object_storage.s3 import S3Client, S3ObjectStorage
from review_platform.settings import Settings, get_settings

from .registry import Handler, HandlerRegistryError, task_handler

RETENTION_TASK_HANDLER_FACTORY_ENV = "REVIEW_PLATFORM_RETENTION_TASK_HANDLER_FACTORY"
IN_TREE_RETENTION_FACTORY = (
    "review_platform.infrastructure.tasks.retention:build_sql_retention_handler"
)
RETENTION_EVENT = "RetentionPurgeRequested"
_TOMBSTONE_TEXT = "[REMOVED_BY_RETENTION]"
_LIVE_PROMOTION_STATES = frozenset(
    {"staged", "db_committed", "promoting", "action_required"}
)
_TERMINAL_OPERATION_STATES = frozenset(
    {"succeeded", "action_required", "stale"}
)

type RetentionKind = Literal["artifact_bytes", "personal_fields", "operation"]


class RetentionTaskError(HandlerRegistryError):
    """Retention configuration, candidate, or tenant provenance is invalid."""


class RetentionCandidate(Protocol):
    candidate_id: UUID
    organization_id: UUID
    kind: RetentionKind
    eligible_at: datetime
    object_key: str | None
    content_digest: str | None
    live_promotion: bool
    active_course_run: bool
    preserved: Mapping[str, object]


class RetentionRepository(Protocol):
    async def scan_candidates(
        self,
        organization_id: UUID,
        *,
        artifact_before: datetime,
        history_before: datetime,
        operation_before: datetime,
        limit: int,
    ) -> Sequence[RetentionCandidate]: ...

    async def claim(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> RetentionCandidate | None: ...

    async def write_tombstone(
        self,
        candidate: RetentionCandidate,
        tombstone: Mapping[str, object],
    ) -> None: ...

    async def delete_row(self, candidate: RetentionCandidate) -> None: ...

    async def assert_no_dangling_references(self, organization_id: UUID) -> None: ...


class RetentionObjects(Protocol):
    def delete(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        key: str,
    ) -> None: ...


class RetentionAudit(Protocol):
    async def record_purge(self, event: Mapping[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class SqlRetentionCandidate:
    candidate_id: UUID
    organization_id: UUID
    kind: RetentionKind
    eligible_at: datetime
    object_key: str | None = None
    content_digest: str | None = None
    live_promotion: bool = False
    active_course_run: bool = False
    preserved: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    organization_id: UUID
    dry_run: bool
    scanned: int
    eligible: int
    protected: int
    claimed: int
    tombstoned: int
    objects_deleted: int
    rows_deleted: int


class RetentionTaskHandler:
    """Apply one tenant's configured retention windows without hidden commits."""

    def __init__(
        self,
        *,
        repository: RetentionRepository,
        objects: RetentionObjects,
        audit: RetentionAudit,
        settings: Settings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._objects = objects
        self._audit = audit
        self._settings = settings
        self._clock = clock

    async def run(
        self,
        *,
        organization_id: UUID,
        dry_run: bool,
        limit: int,
    ) -> RetentionResult:
        if not isinstance(organization_id, UUID):
            raise RetentionTaskError("retention organization_id must be a UUID")
        if not isinstance(dry_run, bool):
            raise RetentionTaskError("retention dry_run must be boolean")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise RetentionTaskError("retention limit must be between 1 and 1000")
        now = require_utc(self._clock())
        artifact_before = now - timedelta(
            days=self._settings.artifact_bytes_retention_days
        )
        history_before = now - timedelta(days=self._settings.history_retention_days)
        operation_before = now - timedelta(
            days=self._settings.operation_retention_days
        )
        candidates = tuple(
            await self._repository.scan_candidates(
                organization_id,
                artifact_before=artifact_before,
                history_before=history_before,
                operation_before=operation_before,
                limit=limit,
            )
        )
        eligible_candidates: list[RetentionCandidate] = []
        protected = 0
        for candidate in candidates:
            self._validate_candidate(candidate, organization_id=organization_id)
            if candidate.live_promotion or candidate.active_course_run:
                protected += 1
                continue
            if self._eligible(
                candidate,
                artifact_before=artifact_before,
                history_before=history_before,
                operation_before=operation_before,
            ):
                eligible_candidates.append(candidate)

        result = RetentionResult(
            organization_id=organization_id,
            dry_run=dry_run,
            scanned=len(candidates),
            eligible=len(eligible_candidates),
            protected=protected,
            claimed=0,
            tombstoned=0,
            objects_deleted=0,
            rows_deleted=0,
        )
        if dry_run or not eligible_candidates:
            return result

        await self._repository.assert_no_dangling_references(organization_id)
        claimed = tombstoned = objects_deleted = rows_deleted = 0
        for candidate in eligible_candidates:
            current = await self._repository.claim(
                organization_id,
                candidate.candidate_id,
            )
            if current is None:
                continue
            self._validate_claim(current, candidate)
            if current.live_promotion or current.active_course_run:
                continue
            claimed += 1
            if current.kind == "artifact_bytes":
                tombstone = self._artifact_tombstone(current, now=now)
                await self._repository.write_tombstone(current, tombstone)
                tombstoned += 1
                assert current.object_key is not None
                current_organization_id = current.organization_id
                current_artifact_id = current.candidate_id
                current_key = current.object_key

                await to_thread.run_sync(
                    partial(
                        self._objects.delete,
                        organization_id=current_organization_id,
                        artifact_version_id=current_artifact_id,
                        key=current_key,
                    )
                )
                finalizer = getattr(
                    self._repository,
                    "complete_object_deletion",
                    None,
                )
                if callable(finalizer):
                    await finalizer(current, removed_at=now)
                objects_deleted += 1
            elif current.kind == "personal_fields":
                await self._repository.write_tombstone(
                    current,
                    {
                        "kind": "personal_fields_removed",
                        "removed_at": now.isoformat(),
                        "provenance": dict(current.preserved),
                    },
                )
                tombstoned += 1
            else:
                await self._repository.delete_row(current)
                rows_deleted += 1

        result = RetentionResult(
            organization_id=organization_id,
            dry_run=False,
            scanned=len(candidates),
            eligible=len(eligible_candidates),
            protected=protected,
            claimed=claimed,
            tombstoned=tombstoned,
            objects_deleted=objects_deleted,
            rows_deleted=rows_deleted,
        )
        if claimed:
            await self._audit.record_purge(
                {
                    "organization_id": organization_id,
                    "action": "retention_purge",
                    "dry_run": False,
                    "scanned": result.scanned,
                    "eligible": result.eligible,
                    "protected": result.protected,
                    "claimed": result.claimed,
                    "tombstoned": result.tombstoned,
                    "objects_deleted": result.objects_deleted,
                    "rows_deleted": result.rows_deleted,
                    "occurred_at": now.isoformat(),
                }
            )
        return result

    @staticmethod
    def _validate_candidate(
        candidate: RetentionCandidate,
        *,
        organization_id: UUID,
    ) -> None:
        if (
            candidate.organization_id != organization_id
            or candidate.kind not in {"artifact_bytes", "personal_fields", "operation"}
        ):
            raise RetentionTaskError("retention repository crossed tenant or kind scope")
        require_utc(candidate.eligible_at)
        if not isinstance(candidate.live_promotion, bool) or not isinstance(
            candidate.active_course_run, bool
        ):
            raise RetentionTaskError("retention protection flags must be boolean")
        if candidate.kind == "artifact_bytes":
            if candidate.object_key is None or candidate.content_digest is None:
                raise RetentionTaskError("artifact retention candidate lacks key or digest")
            validate_digest(candidate.content_digest)
            expected = f"{organization_id}/{candidate.candidate_id}/"
            if not candidate.object_key.startswith(expected):
                raise RetentionTaskError("artifact retention object key crossed tenant scope")

    @classmethod
    def _validate_claim(
        cls,
        current: RetentionCandidate,
        scanned: RetentionCandidate,
    ) -> None:
        cls._validate_candidate(current, organization_id=scanned.organization_id)
        if (
            current.candidate_id != scanned.candidate_id
            or current.kind != scanned.kind
            or current.eligible_at != scanned.eligible_at
        ):
            raise RetentionTaskError("retention claim changed candidate identity")

    @staticmethod
    def _eligible(
        candidate: RetentionCandidate,
        *,
        artifact_before: datetime,
        history_before: datetime,
        operation_before: datetime,
    ) -> bool:
        cutoff = {
            "artifact_bytes": artifact_before,
            "personal_fields": history_before,
            "operation": operation_before,
        }[candidate.kind]
        return candidate.eligible_at <= cutoff

    @staticmethod
    def _artifact_tombstone(
        candidate: RetentionCandidate,
        *,
        now: datetime,
    ) -> Mapping[str, object]:
        assert candidate.content_digest is not None
        return {
            "kind": "artifact_bytes_removed",
            "removed_at": now.isoformat(),
            "content_digest": candidate.content_digest,
            "provenance": dict(candidate.preserved),
        }


class SqlRetentionScheduler:
    """Append one explicit tenant retention request to the caller transaction."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        max_attempts: int = 5,
    ) -> None:
        if not 1 <= max_attempts <= 10:
            raise ValueError("retention scheduler max_attempts must be between 1 and 10")
        self._clock = clock
        self._max_attempts = max_attempts

    async def schedule(
        self,
        *,
        organization_id: UUID,
        message_id: UUID,
        dry_run: bool,
        limit: int,
        transaction: object,
    ) -> OutboxMessage:
        if not isinstance(transaction, AsyncSession):
            raise RetentionTaskError("retention scheduler requires caller-owned AsyncSession")
        if not isinstance(dry_run, bool):
            raise RetentionTaskError("retention dry_run must be boolean")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise RetentionTaskError("retention limit must be between 1 and 1000")
        repository = OutboxMessageRepository(transaction)
        existing = await repository.get(organization_id, message_id, for_update=True)
        payload = {"dry_run": dry_run, "limit": limit}
        if existing is not None:
            _validate_scheduled_message(existing, organization_id, payload)
            return existing
        try:
            async with transaction.begin_nested():
                return await OutboxService(
                    repository,
                    clock=self._clock,
                ).create(
                    OutboxDraft(
                        organization_id=organization_id,
                        message_id=message_id,
                        aggregate_type="organization",
                        aggregate_id=organization_id,
                        event_type=RETENTION_EVENT,
                        payload_version="1.1.0",
                        payload=payload,
                        available_at=require_utc(self._clock()),
                        max_attempts=self._max_attempts,
                    )
                )
        except IntegrityError:
            winner = await repository.get(
                organization_id,
                message_id,
                for_update=True,
            )
            if winner is None:
                raise
            _validate_scheduled_message(winner, organization_id, payload)
            return winner


class SqlRetentionRepository:
    """Short-transaction SQL adapter with append-only expiring deletion claims."""

    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        settings: Settings,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        claim_seconds: int = 900,
    ) -> None:
        if not 1 <= claim_seconds <= 3600:
            raise ValueError("retention claim_seconds must be between 1 and 3600")
        self._sessions = session_factory
        self._settings = settings
        self._id_factory = id_factory
        self._clock = clock
        self._claim_seconds = claim_seconds

    async def scan_candidates(
        self,
        organization_id: UUID,
        *,
        artifact_before: datetime,
        history_before: datetime,
        operation_before: datetime,
        limit: int,
    ) -> Sequence[RetentionCandidate]:
        cutoffs = (
            require_utc(artifact_before),
            require_utc(history_before),
            require_utc(operation_before),
        )
        async with self._sessions() as session:
            candidates: list[RetentionCandidate] = [
                *cast(
                    Sequence[RetentionCandidate],
                    await self._artifact_candidates(
                        session,
                        organization_id,
                        before=cutoffs[0],
                    ),
                ),
                *cast(
                    Sequence[RetentionCandidate],
                    await self._personal_candidates(
                        session,
                        organization_id,
                        before=cutoffs[1],
                    ),
                ),
                *cast(
                    Sequence[RetentionCandidate],
                    await self._operation_candidates(
                        session,
                        organization_id,
                        before=cutoffs[2],
                    ),
                ),
            ]
        return tuple(
            sorted(
                candidates,
                key=lambda item: (item.eligible_at, item.kind, item.candidate_id.int),
            )[:limit]
        )

    async def claim(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> RetentionCandidate | None:
        now = require_utc(self._clock())
        cutoffs = (
            now - timedelta(days=self._settings.artifact_bytes_retention_days),
            now - timedelta(days=self._settings.history_retention_days),
            now - timedelta(days=self._settings.operation_retention_days),
        )
        async with session_scope(self._sessions) as session:
            candidates = await self._locked_candidates(
                session,
                organization_id,
                candidate_id,
                cutoffs=cutoffs,
            )
            if len(candidates) != 1:
                return None
            candidate = candidates[0]
            latest_claim = await session.scalar(
                select(AuditEvent.occurred_at)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.action == "retention_claim",
                    AuditEvent.entity_type == f"retention_{candidate.kind}",
                    AuditEvent.entity_id == candidate_id,
                )
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .limit(1)
            )
            if latest_claim is not None and require_utc(latest_claim) > now - timedelta(
                seconds=self._claim_seconds
            ):
                return None
            claim_id = self._id_factory()
            session.add(
                AuditEvent(
                    id=claim_id,
                    organization_id=organization_id,
                    actor_type="worker",
                    actor_user_id=None,
                    installation_operator_id=None,
                    agent_id=None,
                    agent_authorization_id=None,
                    action="retention_claim",
                    entity_type=f"retention_{candidate.kind}",
                    entity_id=candidate_id,
                    before_revision=None,
                    after_revision=None,
                    request_id=claim_id,
                    trace_id=claim_id,
                    outcome="claimed",
                    sanitized_details={
                        "kind": candidate.kind,
                        "provenance": cast(
                            dict[str, Any],
                            sanitize_shared_value(dict(candidate.preserved)),
                        ),
                    },
                    occurred_at=now,
                )
            )
            await session.flush()
            return cast(RetentionCandidate, candidate)

    async def write_tombstone(
        self,
        candidate: RetentionCandidate,
        tombstone: Mapping[str, object],
    ) -> None:
        async with session_scope(self._sessions) as session:
            if candidate.kind == "artifact_bytes":
                row = await session.scalar(
                    select(ArtifactVersion)
                    .where(
                        ArtifactVersion.organization_id == candidate.organization_id,
                        ArtifactVersion.id == candidate.candidate_id,
                    )
                    .with_for_update()
                )
                if row is None or row.content_digest != candidate.content_digest:
                    raise RetentionTaskError("artifact tombstone candidate changed")
                metadata = dict(row.artifact_metadata)
                existing = metadata.get("retention_tombstone")
                expected = dict(tombstone)
                if (
                    existing is not None
                    and (
                        not isinstance(existing, Mapping)
                        or existing.get("tombstone") != expected
                    )
                ):
                    raise RetentionTaskError("artifact has a conflicting retention tombstone")
                metadata["retention_tombstone"] = {
                    "state": "delete_pending",
                    "tombstone": expected,
                }
                row.artifact_metadata = metadata
                await session.flush([row])
            elif candidate.kind == "personal_fields":
                row = await session.scalar(
                    select(ReviewRevision)
                    .where(
                        ReviewRevision.organization_id == candidate.organization_id,
                        ReviewRevision.id == candidate.candidate_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise RetentionTaskError("personal tombstone candidate disappeared")
                row.feedback = _TOMBSTONE_TEXT
                notes = (
                    await session.scalars(
                        select(ReviewNote).where(
                            ReviewNote.organization_id == candidate.organization_id,
                            ReviewNote.review_revision_id == candidate.candidate_id,
                        )
                    )
                ).all()
                decisions = (
                    await session.scalars(
                        select(ReviewCriterionDecision).where(
                            ReviewCriterionDecision.organization_id
                            == candidate.organization_id,
                            ReviewCriterionDecision.review_revision_id
                            == candidate.candidate_id,
                        )
                    )
                ).all()
                for note in notes:
                    note.text = _TOMBSTONE_TEXT
                for decision in decisions:
                    decision.reason = _TOMBSTONE_TEXT
                await session.flush([row, *notes, *decisions])
            else:
                raise RetentionTaskError("operation candidates do not use tombstones")
            await self._append_tombstone_audit(session, candidate, tombstone)

    async def complete_object_deletion(
        self,
        candidate: RetentionCandidate,
        *,
        removed_at: datetime,
    ) -> None:
        if candidate.kind != "artifact_bytes":
            raise RetentionTaskError("only artifact bytes have object deletion state")
        async with session_scope(self._sessions) as session:
            row = await session.scalar(
                select(ArtifactVersion)
                .where(
                    ArtifactVersion.organization_id == candidate.organization_id,
                    ArtifactVersion.id == candidate.candidate_id,
                )
                .with_for_update()
            )
            if row is None or row.content_digest != candidate.content_digest:
                raise RetentionTaskError("artifact deletion finalization changed scope")
            metadata = dict(row.artifact_metadata)
            tombstone = metadata.get("retention_tombstone")
            if not isinstance(tombstone, Mapping) or tombstone.get("state") not in {
                "delete_pending",
                "removed",
            }:
                raise RetentionTaskError("artifact deletion has no durable tombstone")
            metadata["retention_tombstone"] = {
                **dict(tombstone),
                "state": "removed",
                "object_deleted_at": require_utc(removed_at).isoformat(),
            }
            row.artifact_metadata = metadata
            await session.flush([row])

    async def delete_row(self, candidate: RetentionCandidate) -> None:
        if candidate.kind != "operation":
            raise RetentionTaskError("only operational telemetry rows may be deleted")
        async with session_scope(self._sessions) as session:
            row = await session.scalar(
                select(Operation)
                .where(
                    Operation.organization_id == candidate.organization_id,
                    Operation.id == candidate.candidate_id,
                    Operation.state.in_(_TERMINAL_OPERATION_STATES),
                )
                .with_for_update()
            )
            if row is None:
                return
            if await self._operation_referenced(session, candidate.organization_id, row.id):
                raise RetentionTaskError("operation still has durable domain references")
            await session.delete(row)
            await session.flush()

    async def assert_no_dangling_references(self, organization_id: UUID) -> None:
        async with self._sessions() as session:
            references = (
                (ArtifactPromotion, ArtifactPromotion.operation_id),
                (SubmissionVersion, SubmissionVersion.capture_operation_id),
                (ExternalDelivery, ExternalDelivery.operation_id),
                (DeliveryAttempt, DeliveryAttempt.operation_id),
                (
                    DeliveryReconciliationObservation,
                    DeliveryReconciliationObservation.operation_id,
                ),
            )
            for model, operation_column in references:
                dangling = await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .outerjoin(
                        Operation,
                        (Operation.organization_id == model.organization_id)
                        & (Operation.id == operation_column),
                    )
                    .where(
                        model.organization_id == organization_id,
                        operation_column.is_not(None),
                        Operation.id.is_(None),
                    )
                )
                if dangling:
                    raise RetentionTaskError(
                        "retention detected dangling Operation references"
                    )

    async def _artifact_candidates(
        self,
        session: AsyncSession,
        organization_id: UUID,
        *,
        before: datetime,
        candidate_id: UUID | None = None,
        for_update: bool = False,
    ) -> list[SqlRetentionCandidate]:
        tombstone_state = func.json_unquote(
            func.json_extract(
                ArtifactVersion.artifact_metadata,
                "$.retention_tombstone.state",
            )
        )
        statement = select(ArtifactVersion).where(
            ArtifactVersion.organization_id == organization_id,
            ArtifactVersion.captured_at <= before,
            or_(tombstone_state.is_(None), tombstone_state != "removed"),
        )
        if candidate_id is not None:
            statement = statement.where(ArtifactVersion.id == candidate_id)
        if for_update:
            statement = statement.with_for_update()
        rows = (await session.scalars(statement)).all()
        result: list[SqlRetentionCandidate] = []
        for row in rows:
            live_promotion = bool(
                await session.scalar(
                    select(func.count(ArtifactPromotion.id)).where(
                        ArtifactPromotion.organization_id == organization_id,
                        ArtifactPromotion.artifact_version_id == row.id,
                        ArtifactPromotion.state.in_(_LIVE_PROMOTION_STATES),
                    )
                )
            )
            review_run_rows = (
                await session.execute(
                    select(CourseRun.status, CourseRun.updated_at)
                    .select_from(ReviewIteration)
                    .join(
                        CourseRun,
                        (CourseRun.organization_id == ReviewIteration.organization_id)
                        & (CourseRun.id == ReviewIteration.course_run_id),
                    )
                    .where(
                        ReviewIteration.organization_id == organization_id,
                        ReviewIteration.artifact_version_id == row.id,
                    )
                )
            ).all()
            submission_run_rows = (
                await session.execute(
                    select(CourseRun.status, CourseRun.updated_at)
                    .select_from(SubmissionVersion)
                    .join(
                        CourseRun,
                        (CourseRun.organization_id == SubmissionVersion.organization_id)
                        & (CourseRun.id == SubmissionVersion.course_run_id),
                    )
                    .where(
                        SubmissionVersion.organization_id == organization_id,
                        SubmissionVersion.artifact_version_id == row.id,
                    )
                )
            ).all()
            run_rows = [*review_run_rows, *submission_run_rows]
            active_run = any(status == "active" for status, _ in run_rows)
            archive_times = [
                require_utc(updated_at)
                for status, updated_at in run_rows
                if status == "archived"
            ]
            eligible_at = max([require_utc(row.captured_at), *archive_times])
            result.append(
                SqlRetentionCandidate(
                    candidate_id=row.id,
                    organization_id=organization_id,
                    kind="artifact_bytes",
                    eligible_at=eligible_at,
                    object_key=row.object_key,
                    content_digest=row.content_digest,
                    live_promotion=live_promotion,
                    active_course_run=active_run,
                    preserved=await self._artifact_provenance(
                        session,
                        organization_id,
                        row,
                    ),
                )
            )
        return result

    async def _personal_candidates(
        self,
        session: AsyncSession,
        organization_id: UUID,
        *,
        before: datetime,
        candidate_id: UUID | None = None,
        for_update: bool = False,
    ) -> list[SqlRetentionCandidate]:
        statement = select(ReviewRevision).where(
            ReviewRevision.organization_id == organization_id,
            ReviewRevision.created_at <= before,
            ReviewRevision.feedback != _TOMBSTONE_TEXT,
        )
        if candidate_id is not None:
            statement = statement.where(ReviewRevision.id == candidate_id)
        if for_update:
            statement = statement.with_for_update()
        rows = (await session.scalars(statement)).all()
        result: list[SqlRetentionCandidate] = []
        for row in rows:
            iteration = await session.scalar(
                select(ReviewIteration).where(
                    ReviewIteration.organization_id == organization_id,
                    ReviewIteration.id == row.review_iteration_id,
                )
            )
            if iteration is None:
                continue
            run = await session.scalar(
                select(CourseRun).where(
                    CourseRun.organization_id == organization_id,
                    CourseRun.id == iteration.course_run_id,
                )
            )
            eligible_at = require_utc(row.created_at)
            if run is not None and run.status == "archived":
                eligible_at = max(eligible_at, require_utc(run.updated_at))
            result.append(
                SqlRetentionCandidate(
                    candidate_id=row.id,
                    organization_id=organization_id,
                    kind="personal_fields",
                    eligible_at=eligible_at,
                    active_course_run=run is not None and run.status == "active",
                    preserved=await self._revision_provenance(
                        session,
                        organization_id,
                        row,
                        iteration,
                    ),
                )
            )
        return result

    async def _operation_candidates(
        self,
        session: AsyncSession,
        organization_id: UUID,
        *,
        before: datetime,
        candidate_id: UUID | None = None,
        for_update: bool = False,
    ) -> list[SqlRetentionCandidate]:
        statement = select(Operation).where(
            Operation.organization_id == organization_id,
            Operation.updated_at <= before,
            Operation.state.in_(_TERMINAL_OPERATION_STATES),
        )
        if candidate_id is not None:
            statement = statement.where(Operation.id == candidate_id)
        if for_update:
            statement = statement.with_for_update()
        rows = (await session.scalars(statement)).all()
        result = []
        for row in rows:
            if not await self._operation_referenced(session, organization_id, row.id):
                result.append(
                    SqlRetentionCandidate(
                        candidate_id=row.id,
                        organization_id=organization_id,
                        kind="operation",
                        eligible_at=row.updated_at,
                        preserved={"kind": row.kind, "input_version": row.input_version},
                    )
                )
        return result

    async def _locked_candidates(
        self,
        session: AsyncSession,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        cutoffs: tuple[datetime, datetime, datetime],
    ) -> list[SqlRetentionCandidate]:
        return [
            *await self._artifact_candidates(
                session,
                organization_id,
                before=cutoffs[0],
                candidate_id=candidate_id,
                for_update=True,
            ),
            *await self._personal_candidates(
                session,
                organization_id,
                before=cutoffs[1],
                candidate_id=candidate_id,
                for_update=True,
            ),
            *await self._operation_candidates(
                session,
                organization_id,
                before=cutoffs[2],
                candidate_id=candidate_id,
                for_update=True,
            ),
        ]

    @staticmethod
    async def _artifact_provenance(
        session: AsyncSession,
        organization_id: UUID,
        artifact: ArtifactVersion,
    ) -> Mapping[str, object]:
        submission_ids = tuple(
            str(value)
            for value in (
                await session.scalars(
                    select(SubmissionVersion.id)
                    .where(
                        SubmissionVersion.organization_id == organization_id,
                        SubmissionVersion.artifact_version_id == artifact.id,
                    )
                    .order_by(SubmissionVersion.id)
                )
            ).all()
        )
        iteration_ids = tuple(
            str(value)
            for value in (
                await session.scalars(
                    select(ReviewIteration.id)
                    .where(
                        ReviewIteration.organization_id == organization_id,
                        ReviewIteration.artifact_version_id == artifact.id,
                    )
                    .order_by(ReviewIteration.id)
                )
            ).all()
        )
        revision_ids: tuple[UUID, ...] = ()
        publication_ids: tuple[UUID, ...] = ()
        predecessor_ids: tuple[UUID, ...] = ()
        successor_ids: tuple[UUID, ...] = ()
        if iteration_ids:
            parsed_iterations = tuple(UUID(value) for value in iteration_ids)
            revision_ids = tuple(
                (
                    await session.scalars(
                        select(ReviewRevision.id)
                        .where(
                            ReviewRevision.organization_id == organization_id,
                            ReviewRevision.review_iteration_id.in_(parsed_iterations),
                        )
                        .order_by(ReviewRevision.id)
                    )
                ).all()
            )
            if revision_ids:
                publication_ids = tuple(
                    (
                        await session.scalars(
                            select(ReviewPublication.id)
                            .where(
                                ReviewPublication.organization_id == organization_id,
                                ReviewPublication.review_revision_id.in_(revision_ids),
                            )
                            .order_by(ReviewPublication.id)
                        )
                    ).all()
                )
            predecessor_ids = tuple(
                (
                    await session.scalars(
                        select(ReviewIterationRelation.predecessor_iteration_id)
                        .where(
                            ReviewIterationRelation.organization_id == organization_id,
                            ReviewIterationRelation.successor_iteration_id.in_(
                                parsed_iterations
                            ),
                        )
                        .order_by(ReviewIterationRelation.predecessor_iteration_id)
                    )
                ).all()
            )
            successor_ids = tuple(
                (
                    await session.scalars(
                        select(ReviewIterationRelation.successor_iteration_id)
                        .where(
                            ReviewIterationRelation.organization_id == organization_id,
                            ReviewIterationRelation.predecessor_iteration_id.in_(
                                parsed_iterations
                            ),
                        )
                        .order_by(ReviewIterationRelation.successor_iteration_id)
                    )
                ).all()
            )
        return {
            "artifact_version_id": str(artifact.id),
            "artifact_reference_id": str(artifact.artifact_reference_id),
            "content_digest": artifact.content_digest,
            "object_key": artifact.object_key,
            "provider_version": artifact.provider_version,
            "submission_version_ids": list(submission_ids),
            "review_iteration_ids": list(iteration_ids),
            "review_revision_ids": [str(value) for value in revision_ids],
            "review_publication_ids": [str(value) for value in publication_ids],
            "predecessor_iteration_ids": [str(value) for value in predecessor_ids],
            "successor_iteration_ids": [str(value) for value in successor_ids],
        }

    @staticmethod
    async def _revision_provenance(
        session: AsyncSession,
        organization_id: UUID,
        revision: ReviewRevision,
        iteration: ReviewIteration,
    ) -> Mapping[str, object]:
        publications = (
            await session.scalars(
                select(ReviewPublication.id)
                .where(
                    ReviewPublication.organization_id == organization_id,
                    ReviewPublication.review_revision_id == revision.id,
                )
                .order_by(ReviewPublication.id)
            )
        ).all()
        predecessors = (
            await session.scalars(
                select(ReviewIterationRelation.predecessor_iteration_id)
                .where(
                    ReviewIterationRelation.organization_id == organization_id,
                    ReviewIterationRelation.successor_iteration_id == iteration.id,
                )
                .order_by(ReviewIterationRelation.predecessor_iteration_id)
            )
        ).all()
        successors = (
            await session.scalars(
                select(ReviewIterationRelation.successor_iteration_id)
                .where(
                    ReviewIterationRelation.organization_id == organization_id,
                    ReviewIterationRelation.predecessor_iteration_id == iteration.id,
                )
                .order_by(ReviewIterationRelation.successor_iteration_id)
            )
        ).all()
        artifact = await session.scalar(
            select(ArtifactVersion).where(
                ArtifactVersion.organization_id == organization_id,
                ArtifactVersion.id == iteration.artifact_version_id,
            )
        )
        return {
            "review_revision_id": str(revision.id),
            "review_iteration_id": str(iteration.id),
            "artifact_version_id": str(iteration.artifact_version_id),
            "content_digest": artifact.content_digest if artifact is not None else None,
            "review_publication_ids": [str(value) for value in publications],
            "predecessor_iteration_ids": [str(value) for value in predecessors],
            "successor_iteration_ids": [str(value) for value in successors],
        }

    @staticmethod
    async def _operation_referenced(
        session: AsyncSession,
        organization_id: UUID,
        operation_id: UUID,
    ) -> bool:
        lookups = (
            select(ArtifactPromotion.id).where(
                ArtifactPromotion.organization_id == organization_id,
                ArtifactPromotion.operation_id == operation_id,
            ),
            select(SubmissionVersion.id).where(
                SubmissionVersion.organization_id == organization_id,
                SubmissionVersion.capture_operation_id == operation_id,
            ),
            select(ExternalDelivery.id).where(
                ExternalDelivery.organization_id == organization_id,
                ExternalDelivery.operation_id == operation_id,
            ),
            select(DeliveryAttempt.id).where(
                DeliveryAttempt.organization_id == organization_id,
                DeliveryAttempt.operation_id == operation_id,
            ),
            select(DeliveryReconciliationObservation.id).where(
                DeliveryReconciliationObservation.organization_id == organization_id,
                DeliveryReconciliationObservation.operation_id == operation_id,
            ),
            select(AIReviewRun.id).where(
                AIReviewRun.organization_id == organization_id,
                AIReviewRun.id == operation_id,
            ),
            select(OutboxMessage.message_id).where(
                OutboxMessage.organization_id == organization_id,
                OutboxMessage.aggregate_id == operation_id,
                OutboxMessage.enqueue_state != "completed",
            ),
        )
        for statement in lookups:
            if await session.scalar(statement.limit(1)) is not None:
                return True
        return False

    async def _append_tombstone_audit(
        self,
        session: AsyncSession,
        candidate: RetentionCandidate,
        tombstone: Mapping[str, object],
    ) -> None:
        event_id = self._id_factory()
        session.add(
            AuditEvent(
                id=event_id,
                organization_id=candidate.organization_id,
                actor_type="worker",
                actor_user_id=None,
                installation_operator_id=None,
                agent_id=None,
                agent_authorization_id=None,
                action="retention_tombstone",
                entity_type=f"retention_{candidate.kind}",
                entity_id=candidate.candidate_id,
                before_revision=None,
                after_revision=None,
                request_id=event_id,
                trace_id=event_id,
                outcome="succeeded",
                sanitized_details=cast(
                    dict[str, Any],
                    sanitize_shared_value(dict(tombstone)),
                ),
                occurred_at=require_utc(self._clock()),
            )
        )
        await session.flush()


class S3RetentionObjects:
    def __init__(self, storage: S3ObjectStorage) -> None:
        self._storage = storage

    def delete(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        key: str,
    ) -> None:
        rendered_organization = str(organization_id)
        self._storage.delete(
            organization_id=rendered_organization,
            artifact_version_id=str(artifact_version_id),
            requested_by_organization_id=rendered_organization,
            key=key,
        )


class SqlRetentionAudit:
    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        *,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = session_factory
        self._id_factory = id_factory
        self._clock = clock

    async def record_purge(self, event: Mapping[str, object]) -> None:
        organization_id = event.get("organization_id")
        if not isinstance(organization_id, UUID):
            raise RetentionTaskError("retention audit organization is invalid")
        event_id = self._id_factory()
        async with session_scope(self._sessions) as session:
            session.add(
                AuditEvent(
                    id=event_id,
                    organization_id=organization_id,
                    actor_type="worker",
                    actor_user_id=None,
                    installation_operator_id=None,
                    agent_id=None,
                    agent_authorization_id=None,
                    action="retention_purge",
                    entity_type="organization",
                    entity_id=organization_id,
                    before_revision=None,
                    after_revision=None,
                    request_id=event_id,
                    trace_id=event_id,
                    outcome="succeeded",
                    sanitized_details=cast(
                        dict[str, Any],
                        sanitize_shared_value(dict(event)),
                    ),
                    occurred_at=require_utc(self._clock()),
                )
            )
            await session.flush()


def build_sql_retention_handler() -> Handler:
    """Validate mandatory config and return in-tree SQL/S3 composition."""

    settings = get_settings()
    if settings.database_url is None:
        raise RetentionTaskError("REVIEW_PLATFORM_DATABASE_URL is required")
    if settings.s3_endpoint_url is None:
        raise RetentionTaskError("REVIEW_PLATFORM_S3_ENDPOINT_URL is required")
    if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
        raise RetentionTaskError("explicit S3 credentials are required for retention")
    database_url = settings.database_url
    access_key = (
        settings.s3_access_key_id.get_secret_value()
        if settings.s3_access_key_id is not None
        else None
    )
    secret_key = (
        settings.s3_secret_access_key.get_secret_value()
        if settings.s3_secret_access_key is not None
        else None
    )
    client = cast(
        S3Client,
        boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        ),
    )
    storage = S3ObjectStorage(
        client=client,
        bucket=settings.s3_bucket,
        max_object_bytes=settings.artifact_total_max_bytes,
    )

    async def configured(*, organization_id: str, message_id: str) -> object:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        engine = create_database_engine(database_url)
        sessions = create_session_factory(engine)
        try:
            async with sessions() as session:
                message = await OutboxMessageRepository(session).get(
                    organization_uuid,
                    message_uuid,
                )
            dry_run, limit = _retention_payload(
                message,
                organization_id=organization_uuid,
            )
            result = await RetentionTaskHandler(
                repository=SqlRetentionRepository(sessions, settings=settings),
                objects=S3RetentionObjects(storage),
                audit=SqlRetentionAudit(sessions),
                settings=settings,
            ).run(
                organization_id=organization_uuid,
                dry_run=dry_run,
                limit=limit,
            )
            return _result_payload(result)
        finally:
            await engine.dispose()

    return cast(Handler, configured)


_configured_spec: str | None = None
_configured_handler: Handler | None = None


def validate_retention_configuration() -> None:
    _resolve_configured_handler()


def _resolve_configured_handler() -> Handler:
    global _configured_handler, _configured_spec
    specification = os.environ.get(RETENTION_TASK_HANDLER_FACTORY_ENV)
    if not specification:
        raise RetentionTaskError(f"{RETENTION_TASK_HANDLER_FACTORY_ENV} is required")
    if _configured_handler is not None and _configured_spec == specification:
        return _configured_handler
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise RetentionTaskError(
            f"{RETENTION_TASK_HANDLER_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise RetentionTaskError("retention handler factory is not callable")
    configured = factory()
    if not callable(configured):
        raise RetentionTaskError("retention handler factory result is not callable")
    _configured_spec = specification
    _configured_handler = cast(Handler, configured)
    return _configured_handler


@task_handler(
    name="review_platform.retention_purge",
    # Retention shares the bounded general-maintenance lane.
    kind="course_import",
    event_type=RETENTION_EVENT,
    requires_auth_revalidation=False,
    startup_validator=validate_retention_configuration,
)
async def handle_retention_purge(
    *,
    organization_id: str,
    message_id: str,
) -> object:
    handler = _resolve_configured_handler()
    result = handler(organization_id=organization_id, message_id=message_id)
    if isinstance(result, Awaitable):
        return await result
    return result


def _retention_payload(
    message: OutboxMessage | None,
    *,
    organization_id: UUID,
) -> tuple[bool, int]:
    if (
        message is None
        or message.organization_id != organization_id
        or message.event_type != RETENTION_EVENT
        or message.aggregate_type != "organization"
        or message.aggregate_id != organization_id
        or message.payload_version != "1.1.0"
    ):
        raise RetentionTaskError("tenant retention outbox message is missing or invalid")
    dry_run = message.payload.get("dry_run")
    limit = message.payload.get("limit")
    if not isinstance(dry_run, bool):
        raise RetentionTaskError("retention outbox dry_run must be boolean")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise RetentionTaskError("retention outbox limit must be an integer")
    return dry_run, limit


def _validate_scheduled_message(
    message: OutboxMessage,
    organization_id: UUID,
    payload: Mapping[str, object],
) -> None:
    if (
        message.organization_id != organization_id
        or message.aggregate_type != "organization"
        or message.aggregate_id != organization_id
        or message.event_type != RETENTION_EVENT
        or message.payload_version != "1.1.0"
        or message.payload != payload
    ):
        raise RetentionTaskError("retention message identity was reused")


def _result_payload(result: RetentionResult) -> Mapping[str, object]:
    return {
        "organization_id": str(result.organization_id),
        "dry_run": result.dry_run,
        "scanned": result.scanned,
        "eligible": result.eligible,
        "protected": result.protected,
        "claimed": result.claimed,
        "tombstoned": result.tombstoned,
        "objects_deleted": result.objects_deleted,
        "rows_deleted": result.rows_deleted,
    }


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise RetentionTaskError(f"retention {field} must be a UUID") from error


_repository_protocol: RetentionRepository
_objects_protocol: RetentionObjects
_audit_protocol: RetentionAudit


__all__ = [
    "IN_TREE_RETENTION_FACTORY",
    "RETENTION_EVENT",
    "RETENTION_TASK_HANDLER_FACTORY_ENV",
    "RetentionAudit",
    "RetentionCandidate",
    "RetentionObjects",
    "RetentionRepository",
    "RetentionResult",
    "RetentionTaskError",
    "RetentionTaskHandler",
    "S3RetentionObjects",
    "SqlRetentionAudit",
    "SqlRetentionCandidate",
    "SqlRetentionRepository",
    "SqlRetentionScheduler",
    "build_sql_retention_handler",
    "handle_retention_purge",
    "validate_retention_configuration",
]
