from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update
from taskiq.abc.broker import AsyncBroker

from review_platform.infrastructure.db.models.learning import CourseRun
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    Operation,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactVersion,
    SubmissionVersion,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.tasks.broker import BrokerPolicy
from review_platform.infrastructure.tasks.registry import (
    REGISTRY,
    HandlerRegistry,
    bind_handlers,
    load_handler_modules,
)
from review_platform.infrastructure.tasks.retention import (
    RETENTION_EVENT,
    RETENTION_TASK_HANDLER_FACTORY_ENV,
    RetentionTaskError,
    RetentionTaskHandler,
    SqlRetentionAudit,
    SqlRetentionRepository,
    SqlRetentionScheduler,
)
from review_platform.settings import Settings

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
OPERATION = UUID("00000000-0000-7000-8000-000000196001")
MESSAGE = UUID("00000000-0000-7000-8000-000000196002")
ARTIFACT = UUID("00000000-0000-7000-8000-000000196003")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000196004")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class _NoArtifactObjects:
    def delete(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        key: str,
    ) -> None:
        del organization_id, artifact_version_id, key
        raise AssertionError("operation retention must not touch object storage")


class _RecordingArtifactObjects:
    def __init__(self) -> None:
        self.deleted: list[tuple[UUID, UUID, str]] = []

    def delete(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        key: str,
    ) -> None:
        assert key.startswith(f"{organization_id}/{artifact_version_id}/")
        self.deleted.append((organization_id, artifact_version_id, key))


class _Broker:
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def register_task(
        self,
        _handler: object,
        *,
        task_name: str,
        **_labels: object,
    ) -> None:
        self.tasks.append(task_name)


def _retention_registry() -> HandlerRegistry:
    load_handler_modules()
    source = REGISTRY.resolve_event(RETENTION_EVENT)
    registry = HandlerRegistry()
    registry.register(
        name=source.name,
        kind=source.kind,
        event_type=source.event_type,
        handler=source.handler,
        requires_auth_revalidation=source.requires_auth_revalidation,
        startup_validator=source.startup_validator,
    )
    return registry


def _policy() -> BrokerPolicy:
    return BrokerPolicy(
        redis_url="redis://localhost:6379/0",
        max_attempts=3,
        initial_retry_seconds=1,
        max_retry_seconds=10,
        concurrency_by_kind={"course_import": 1},
        queue_by_kind={"course_import": "review-platform:worker"},
    )


def test_retention_registry_fails_closed_then_binds_bounded_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _retention_registry()
    broker = _Broker()
    monkeypatch.delenv(RETENTION_TASK_HANDLER_FACTORY_ENV, raising=False)
    with pytest.raises(RetentionTaskError, match=RETENTION_TASK_HANDLER_FACTORY_ENV):
        bind_handlers(
            broker=cast(AsyncBroker, broker),
            policy=_policy(),
            queue_name="review-platform:worker",
            registry=registry,
        )
    assert broker.tasks == []

    async def configured(*, organization_id: str, message_id: str) -> object:
        return {"organization_id": organization_id, "message_id": message_id}

    module = ModuleType("retention_task_fixture_factory")
    module.build = lambda: configured  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv(
        RETENTION_TASK_HANDLER_FACTORY_ENV,
        f"{module.__name__}:build",
    )
    names = bind_handlers(
        broker=cast(AsyncBroker, broker),
        policy=_policy(),
        queue_name="review-platform:worker",
        registry=registry,
    )

    assert names == ("review_platform.retention_purge",)
    assert broker.tasks == ["review_platform.retention_purge"]


async def test_sql_scheduler_replays_exact_request_in_caller_transaction(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    scheduler = SqlRetentionScheduler(clock=lambda: NOW, max_attempts=3)
    async with session_scope(foundation_session_factory) as session:
        first = await scheduler.schedule(
            organization_id=ORG,
            message_id=MESSAGE,
            dry_run=True,
            limit=25,
            transaction=session,
        )
        replay = await scheduler.schedule(
            organization_id=ORG,
            message_id=MESSAGE,
            dry_run=True,
            limit=25,
            transaction=session,
        )
        assert replay is first

    async with foundation_session_factory() as session:
        message = await session.get(OutboxMessage, MESSAGE)
        count = await session.scalar(
            select(func.count()).select_from(OutboxMessage)
        )
    assert count == 1
    assert message is not None
    assert message.organization_id == ORG
    assert message.aggregate_type == "organization"
    assert message.aggregate_id == ORG
    assert message.event_type == RETENTION_EVENT
    assert message.payload == {"dry_run": True, "limit": 25}


async def test_sql_operation_retention_dry_run_then_claims_audits_and_deletes(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    settings = Settings(
        artifact_bytes_retention_days=90,
        history_retention_days=365,
        operation_retention_days=90,
    )
    async with session_scope(foundation_session_factory) as session:
        session.add(
            Operation(
                id=OPERATION,
                organization_id=ORG,
                kind="course_import",
                input_version="retention-test-v1",
                state="succeeded",
                revision=1,
                created_at=NOW - timedelta(days=100),
                updated_at=NOW - timedelta(days=91),
                finished_at=NOW - timedelta(days=91),
                error_code=None,
                sanitized_error=None,
            )
        )
    repository = SqlRetentionRepository(
        foundation_session_factory,
        settings=settings,
        clock=lambda: NOW,
    )
    handler = RetentionTaskHandler(
        repository=repository,
        objects=_NoArtifactObjects(),
        audit=SqlRetentionAudit(
            foundation_session_factory,
            clock=lambda: NOW,
        ),
        settings=settings,
        clock=lambda: NOW,
    )

    dry_run = await handler.run(organization_id=ORG, dry_run=True, limit=10)
    assert dry_run.scanned == dry_run.eligible == 1
    assert dry_run.claimed == dry_run.rows_deleted == 0
    async with foundation_session_factory() as session:
        assert await session.get(Operation, OPERATION) is not None
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 0

    purged = await handler.run(organization_id=ORG, dry_run=False, limit=10)
    assert purged.scanned == purged.eligible == purged.claimed == 1
    assert purged.rows_deleted == 1
    assert purged.tombstoned == purged.objects_deleted == 0
    async with foundation_session_factory() as session:
        assert await session.get(Operation, OPERATION) is None
        actions = (
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.organization_id == ORG)
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        ).all()
    assert actions == ["retention_claim", "retention_purge"]


async def test_sql_artifact_window_starts_after_every_active_run_is_archived(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    settings = Settings(
        artifact_bytes_retention_days=90,
        history_retention_days=365,
        operation_retention_days=90,
    )
    async with session_scope(foundation_session_factory) as session:
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        session.add_all(
            [
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=UUID("00000000-0000-7000-8000-000000196010"),
                    title="Retention course run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                    created_at=NOW - timedelta(days=200),
                    updated_at=NOW - timedelta(days=200),
                ),
                ArtifactVersion(
                    id=ARTIFACT,
                    organization_id=ORG,
                    artifact_reference_id=UUID(
                        "00000000-0000-7000-8000-000000196011"
                    ),
                    provider_version="commit:retention",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG}/{ARTIFACT}/artifact.bin",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW - timedelta(days=200),
                    artifact_metadata={},
                ),
                SubmissionVersion(
                    id=UUID("00000000-0000-7000-8000-000000196012"),
                    organization_id=ORG,
                    submission_id=UUID("00000000-0000-7000-8000-000000196013"),
                    course_run_id=COURSE_RUN,
                    homework_id=UUID("00000000-0000-7000-8000-000000196014"),
                    sequence=1,
                    homework_version_id=UUID(
                        "00000000-0000-7000-8000-000000196015"
                    ),
                    artifact_reference_id=UUID(
                        "00000000-0000-7000-8000-000000196011"
                    ),
                    artifact_version_id=ARTIFACT,
                    submitted_at=NOW - timedelta(days=200),
                    effective_deadline=NOW - timedelta(days=190),
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    repository = SqlRetentionRepository(
        foundation_session_factory,
        settings=settings,
        clock=lambda: NOW,
    )
    handler = RetentionTaskHandler(
        repository=repository,
        objects=_NoArtifactObjects(),
        audit=SqlRetentionAudit(foundation_session_factory, clock=lambda: NOW),
        settings=settings,
        clock=lambda: NOW,
    )

    active = await handler.run(organization_id=ORG, dry_run=True, limit=10)
    assert active.scanned == active.protected == 1
    assert active.eligible == 0

    async with session_scope(foundation_session_factory) as session:
        await session.execute(
            update(CourseRun)
            .where(CourseRun.organization_id == ORG, CourseRun.id == COURSE_RUN)
            .values(status="archived", updated_at=NOW - timedelta(days=89))
        )
    recently_archived = await handler.run(
        organization_id=ORG,
        dry_run=True,
        limit=10,
    )
    assert recently_archived.scanned == 1
    assert recently_archived.protected == recently_archived.eligible == 0

    async with session_scope(foundation_session_factory) as session:
        await session.execute(
            update(CourseRun)
            .where(CourseRun.organization_id == ORG, CourseRun.id == COURSE_RUN)
            .values(updated_at=NOW - timedelta(days=91))
        )
    expired = await handler.run(organization_id=ORG, dry_run=True, limit=10)
    assert expired.scanned == expired.eligible == 1
    assert expired.protected == 0

    objects = _RecordingArtifactObjects()
    purge_handler = RetentionTaskHandler(
        repository=repository,
        objects=objects,
        audit=SqlRetentionAudit(foundation_session_factory, clock=lambda: NOW),
        settings=settings,
        clock=lambda: NOW,
    )
    purged = await purge_handler.run(organization_id=ORG, dry_run=False, limit=10)
    assert purged.tombstoned == purged.objects_deleted == 1
    assert objects.deleted == [(ORG, ARTIFACT, f"{ORG}/{ARTIFACT}/artifact.bin")]
    async with foundation_session_factory() as session:
        artifact = await session.get(ArtifactVersion, ARTIFACT)
        assert artifact is not None
        tombstone = artifact.artifact_metadata["retention_tombstone"]
        assert tombstone["state"] == "removed"
        assert tombstone["tombstone"]["content_digest"] == "sha256:" + "a" * 64
    replay = await purge_handler.run(organization_id=ORG, dry_run=False, limit=10)
    assert replay.scanned == replay.claimed == replay.objects_deleted == 0
