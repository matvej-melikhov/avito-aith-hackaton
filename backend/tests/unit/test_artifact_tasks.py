from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from sqlalchemy import func, select

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.services.artifact_capture import ArtifactCaptureLimits
from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.operations import (
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactPromotion,
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.repositories.submissions import (
    SqlArtifactPromotionRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.object_storage.promotions import (
    ArtifactPromotionRepository,
    FetchedArtifactContent,
    StagedObjectCandidate,
)
from review_platform.infrastructure.object_storage.s3 import S3ObjectStorage
from review_platform.infrastructure.tasks.artifacts import (
    ArtifactCaptureTaskHandler,
    ArtifactCleanupTaskHandler,
    ArtifactPromotionTaskHandler,
    ArtifactTaskError,
    ArtifactTaskRuntime,
)
from review_platform.infrastructure.tasks.registry import HANDLER_MODULES, REGISTRY

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
USER = UUID("00000000-0000-7000-8000-000000008001")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000008002")
COURSE = UUID("00000000-0000-7000-8000-000000008003")
RUN = UUID("00000000-0000-7000-8000-000000008004")
HOMEWORK = UUID("00000000-0000-7000-8000-000000008005")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000008006")
RELATION = UUID("00000000-0000-7000-8000-000000008007")
PUBLICATION = UUID("00000000-0000-7000-8000-000000008008")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000008009")
CREDENTIAL_OTHER = UUID("00000000-0000-7000-8000-000000008099")
REFERENCE = UUID("00000000-0000-7000-8000-000000008010")
SUBMISSION = UUID("00000000-0000-7000-8000-000000008011")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000008012")
OPERATION = UUID("00000000-0000-7000-8000-000000008013")
CAPTURE_MESSAGE = UUID("00000000-0000-7000-8000-000000008014")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
CONTENT = b"verified immutable archive bytes"
DIGEST = sha256_digest(CONTENT)


class IDs:
    def __init__(self, value: int = 9000) -> None:
        self.value = value

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Provider:
    contract_version = "1.1.0"
    schema_name = "artifact-provider.schema.json"

    def __init__(self, *, outcome: str = "succeeded") -> None:
        self.outcome = outcome
        self.requests: list[ProviderPayload] = []

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        raise AssertionError("capture task must not repeat preflight")

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        self.requests.append(request)
        if self.outcome == "retryable_failed":
            return {
                "contract_version": "1.1.0",
                "organization_id": request["organization_id"],
                "artifact_reference_id": request["artifact_reference_id"],
                "outcome": "failed",
                "provider_version": "fixture-v1",
                "content": None,
                "error": {
                    "code": "provider_unavailable",
                    "message": "Provider temporarily unavailable",
                    "retryable": True,
                    "action": "retry",
                },
            }
        return {
            "contract_version": "1.1.0",
            "organization_id": request["organization_id"],
            "artifact_reference_id": request["artifact_reference_id"],
            "outcome": "succeeded",
            "provider_version": "fixture-v1",
            "content": {
                "download_url": "https://provider.example.test/handoff/artifact",
                "media_type": "application/zip",
                "byte_size": len(CONTENT),
                "content_digest": DIGEST,
            },
            "error": None,
        }


class Fetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def fetch(self, download_url: str, *, max_bytes: int) -> FetchedArtifactContent:
        self.calls.append((download_url, max_bytes))
        return FetchedArtifactContent(
            body=io.BytesIO(CONTENT),
            media_type="application/zip",
            byte_size=len(CONTENT),
            metadata={"handoff": "fixture"},
        )


class Inventory:
    def __init__(self) -> None:
        self.candidates: list[StagedObjectCandidate] = []

    async def list_staged(self, *, older_than: datetime) -> Sequence[StagedObjectCandidate]:
        del older_than
        return tuple(self.candidates)


def _storage(runtime: FoundationRuntime) -> S3ObjectStorage:
    return cast(S3ObjectStorage, runtime.components()["object_storage"])


def _runtime(
    foundation_runtime: FoundationRuntime,
    factory: AsyncSessionFactory,
    *,
    ids: Callable[[], UUID] | None = None,
    clock: Clock | None = None,
    provider: Provider | None = None,
    inventory: Inventory | None = None,
    capture_max_attempts: int = 3,
) -> ArtifactTaskRuntime:
    return ArtifactTaskRuntime(
        session_factory=factory,
        storage=_storage(foundation_runtime),
        providers={"github": provider or Provider()},
        fetcher=Fetcher(),
        inventory=inventory or Inventory(),
        capture_limits=ArtifactCaptureLimits(
            max_files=100,
            max_single_blob_bytes=1024,
            max_total_bytes=1024,
            max_archive_bytes=1024,
            max_unpacked_bytes=2048,
        ),
        id_factory=ids or IDs(),
        clock=clock or Clock(),
        capture_max_attempts=capture_max_attempts,
        promotion_lease_seconds=60,
        promotion_retry_seconds=30,
    )


async def _seed_capture(factory: AsyncSessionFactory, *, promotion_attempts: int = 3) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=USER, display_name="Artifact Student", status="active"),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Artifact Course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=USER,
                    roles=["student"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                CourseRun(
                    id=RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Artifact Run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Artifact Homework",
                    revision=0,
                ),
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted",
                    key_id="fixture-key",
                    status="active",
                ),
                ExternalCredential(
                    id=CREDENTIAL_OTHER,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="other-encrypted",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add(
            HomeworkVersion(
                id=HOMEWORK_VERSION,
                organization_id=ORG,
                homework_id=HOMEWORK,
                version_number=1,
                student_text="Submit repository",
                max_score=Decimal("10"),
                artifact_kinds=["github"],
                estimated_review_minutes=30,
                revision=0,
            )
        )
        await session.flush()
        session.add(
            CourseRunHomework(
                id=RELATION,
                organization_id=ORG,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                current_publication_id=None,
                status="active",
                revision=1,
            )
        )
        await session.flush()
        session.add(
            CourseRunHomeworkPublication(
                id=PUBLICATION,
                organization_id=ORG,
                course_run_homework_id=RELATION,
                homework_id=HOMEWORK,
                homework_version_id=HOMEWORK_VERSION,
                publication_sequence=1,
                submission_deadline=NOW + timedelta(days=1),
                review_deadline=NOW + timedelta(days=2),
                published_at=NOW,
            )
        )
        await session.flush()
        relation = await session.get(CourseRunHomework, RELATION)
        assert relation is not None
        relation.current_publication_id = PUBLICATION
        session.add(
            ArtifactReference(
                id=REFERENCE,
                organization_id=ORG,
                provider="github",
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                original_url="https://github.com/example/repository",
                locator={
                    "canonical_url": "https://github.com/example/repository",
                    "external_id": "example/repository",
                },
                read_capability="available",
                feedback_capability="available",
                last_checked_at=NOW,
                revision=0,
            )
        )
        await session.flush()
        session.add(
            Submission(
                id=SUBMISSION,
                organization_id=ORG,
                course_run_homework_id=RELATION,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                student_id=USER,
                current_predeadline_version_id=None,
                revision=0,
            )
        )
        session.add(
            Operation(
                id=OPERATION,
                organization_id=ORG,
                kind="artifact_capture",
                input_version="artifact-capture-request:1.1.0:fixture",
                state="pending",
                revision=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            SubmissionVersion(
                id=SUBMISSION_VERSION,
                organization_id=ORG,
                submission_id=SUBMISSION,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                sequence=1,
                homework_version_id=HOMEWORK_VERSION,
                artifact_reference_id=REFERENCE,
                artifact_version_id=None,
                submitted_at=NOW,
                effective_deadline=NOW + timedelta(days=1),
                phase="before_deadline",
                status="validating",
                capture_operation_id=OPERATION,
                revision=0,
            )
        )
        session.add(
            OutboxMessage(
                message_id=CAPTURE_MESSAGE,
                organization_id=ORG,
                aggregate_type="operation",
                aggregate_id=OPERATION,
                event_type="ArtifactCaptureRequested",
                payload_version="1.1.0",
                payload=_capture_payload(),
                available_at=NOW,
                enqueue_state="enqueued",
                attempts=1,
                max_attempts=promotion_attempts,
            )
        )


def _capture_payload() -> dict[str, object]:
    return {
        "organization_id": str(ORG),
        "operation_id": str(OPERATION),
        "submission_id": str(SUBMISSION),
        "submission_version_id": str(SUBMISSION_VERSION),
        "artifact_reference_id": str(REFERENCE),
        "provider": "github",
        "credential_binding_id": str(CREDENTIAL),
        "credential_binding_version": 1,
        "course_run_id": str(RUN),
        "homework_id": str(HOMEWORK),
        "homework_version_id": str(HOMEWORK_VERSION),
        "course_run_homework_id": str(RELATION),
        "homework_publication_id": str(PUBLICATION),
        "actor": {
            "type": "user",
            "user_id": str(USER),
            "roles": ["student"],
            "membership_revision": 0,
            "auth_epoch": 0,
        },
    }


async def _promotion_message(factory: AsyncSessionFactory) -> OutboxMessage:
    async with factory() as session:
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.organization_id == ORG,
                OutboxMessage.event_type == "ArtifactPromotionRequested",
            )
        )
        assert message is not None
        return message


async def test_capture_bundle_promotes_and_duplicate_message_is_idempotent(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_capture(foundation_session_factory)
    provider = Provider()
    runtime = _runtime(
        foundation_runtime,
        foundation_session_factory,
        provider=provider,
    )
    capture = ArtifactCaptureTaskHandler(runtime)

    captured = await capture(organization_id=str(ORG), message_id=str(CAPTURE_MESSAGE))
    assert captured["state"] == "db_committed"
    assert captured["message_id"] == str(CAPTURE_MESSAGE)
    assert len(provider.requests) == 1
    assert provider.requests[0]["credential_binding_id"] == str(CREDENTIAL)
    assert provider.requests[0]["credential_binding_version"] == 1
    promotion_message = await _promotion_message(foundation_session_factory)
    promoted = await ArtifactPromotionTaskHandler(runtime, owner="promotion-worker")(
        organization_id=str(ORG),
        message_id=str(promotion_message.message_id),
    )
    assert promoted["state"] == "promoted"

    replay = await capture(organization_id=str(ORG), message_id=str(CAPTURE_MESSAGE))
    assert replay["replayed"] is True
    assert replay["state"] == "promoted"
    assert len(provider.requests) == 1
    async with foundation_session_factory() as session:
        version = await session.get(SubmissionVersion, SUBMISSION_VERSION)
        operation = await session.get(Operation, OPERATION)
        attempts = (
            await session.scalars(
                select(OperationAttempt)
                .where(OperationAttempt.organization_id == ORG)
                .order_by(OperationAttempt.attempt_number)
            )
        ).all()
        assert version is not None and version.artifact_version_id is not None
        assert version.status == "ready"
        assert operation is not None and operation.state == "succeeded"
        assert [attempt.outcome for attempt in attempts] == ["succeeded", "succeeded"]


async def test_promotion_recovers_existing_final_and_sql_stale_lease_loses_cas(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_capture(foundation_session_factory)
    clock = Clock()
    runtime = _runtime(foundation_runtime, foundation_session_factory, clock=clock)
    await ArtifactCaptureTaskHandler(runtime)(
        organization_id=str(ORG),
        message_id=str(CAPTURE_MESSAGE),
    )
    promotion_message = await _promotion_message(foundation_session_factory)
    async with foundation_session_factory() as session:
        promotion = await session.scalar(select(ArtifactPromotion))
        version = await session.scalar(select(ArtifactVersion))
        assert promotion is not None and version is not None
        _storage(foundation_runtime).upload(
            organization_id=str(ORG),
            artifact_version_id=str(version.id),
            source=io.BytesIO(CONTENT),
            media_type="application/zip",
            expected_digest=DIGEST,
            filename="artifact.bin",
        )
    recovered = await ArtifactPromotionTaskHandler(runtime, owner="recovery-worker")(
        organization_id=str(ORG),
        message_id=str(promotion_message.message_id),
    )
    assert recovered["recovered_existing_final"] is True

    # A separate unpromoted row proves the concrete SQL adapter's token CAS.
    await _add_manual_promotion(
        foundation_session_factory,
        artifact_id=UUID("00000000-0000-7000-8000-000000008101"),
        promotion_id=UUID("00000000-0000-7000-8000-000000008102"),
        operation_id=UUID("00000000-0000-7000-8000-000000008103"),
    )
    repository = SqlArtifactPromotionRepository(
        foundation_session_factory,
        id_factory=IDs(9500),
        clock=clock,
    )
    promotion_id = UUID("00000000-0000-7000-8000-000000008102")
    first = await repository.claim(
        ORG,
        promotion_id,
        owner="worker-a",
        token=UUID("00000000-0000-7000-8000-000000008104"),
        now=NOW,
        lease_seconds=30,
    )
    assert first is not None
    clock.now = NOW + timedelta(seconds=31)
    second = await repository.claim(
        ORG,
        promotion_id,
        owner="worker-b",
        token=UUID("00000000-0000-7000-8000-000000008105"),
        now=clock.now,
        lease_seconds=30,
    )
    assert second is not None
    assert await repository.complete(first, now=clock.now) is False
    async with foundation_session_factory() as session:
        expired_attempts = (
            await session.scalars(
                select(OperationAttempt).where(
                    OperationAttempt.organization_id == ORG,
                    OperationAttempt.operation_id
                    == UUID("00000000-0000-7000-8000-000000008103"),
                )
            )
        ).all()
        assert [attempt.outcome for attempt in expired_attempts] == ["retryable_failed"]
        assert expired_attempts[0].error_code == "artifact_promotion_lease_expired"


async def test_promotion_digest_failure_reaches_action_required(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_capture(foundation_session_factory, promotion_attempts=1)
    runtime = _runtime(
        foundation_runtime,
        foundation_session_factory,
        capture_max_attempts=1,
    )
    await ArtifactCaptureTaskHandler(runtime)(
        organization_id=str(ORG),
        message_id=str(CAPTURE_MESSAGE),
    )
    promotion_message = await _promotion_message(foundation_session_factory)
    async with foundation_session_factory() as session:
        promotion = await session.scalar(select(ArtifactPromotion))
        assert promotion is not None
        _storage(foundation_runtime).upload(
            organization_id=str(ORG),
            artifact_version_id=str(promotion.artifact_version_id),
            source=io.BytesIO(b"x" * len(CONTENT)),
            media_type="application/zip",
            filename=promotion.staged_key.removeprefix(
                f"{ORG}/{promotion.artifact_version_id}/"
            ),
        )
    failed = await ArtifactPromotionTaskHandler(runtime, owner="promotion-worker")(
        organization_id=str(ORG),
        message_id=str(promotion_message.message_id),
    )
    assert failed["state"] == "action_required"
    assert failed["error"] is not None
    assert len(str(failed["error"]).encode()) <= 2048
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, OPERATION)
        assert operation is not None and operation.state == "action_required"


async def test_capture_retry_budget_records_attempt_and_action_required(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_capture(foundation_session_factory)
    runtime = _runtime(
        foundation_runtime,
        foundation_session_factory,
        provider=Provider(outcome="retryable_failed"),
        capture_max_attempts=1,
    )

    failed = await ArtifactCaptureTaskHandler(runtime)(
        organization_id=str(ORG),
        message_id=str(CAPTURE_MESSAGE),
    )

    assert failed["state"] == "action_required"
    assert failed["attempt_number"] == 1
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, OPERATION)
        version = await session.get(SubmissionVersion, SUBMISSION_VERSION)
        attempt = await session.scalar(
            select(OperationAttempt).where(
                OperationAttempt.organization_id == ORG,
                OperationAttempt.operation_id == OPERATION,
            )
        )
        assert operation is not None and operation.state == "action_required"
        assert version is not None and version.status == "access_error"
        assert attempt is not None and attempt.outcome == "action_required"
        assert attempt.sanitized_error is not None


async def test_capture_task_rejects_binding_different_from_opaque_reference(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_capture(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        message = await session.get(OutboxMessage, CAPTURE_MESSAGE)
        assert message is not None
        payload = dict(message.payload)
        payload["credential_binding_id"] = str(CREDENTIAL_OTHER)
        message.payload = payload
    provider = Provider()
    runtime = _runtime(
        foundation_runtime,
        foundation_session_factory,
        provider=provider,
    )

    with pytest.raises(ArtifactTaskError, match="persisted reference"):
        await ArtifactCaptureTaskHandler(runtime)(
            organization_id=str(ORG),
            message_id=str(CAPTURE_MESSAGE),
        )

    assert provider.requests == []
    async with foundation_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(OperationAttempt)) == 0


async def test_cleanup_preserves_live_intent_and_final_but_deletes_orphan(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_capture(foundation_session_factory)
    inventory = Inventory()
    runtime = _runtime(
        foundation_runtime,
        foundation_session_factory,
        inventory=inventory,
    )
    await ArtifactCaptureTaskHandler(runtime)(
        organization_id=str(ORG),
        message_id=str(CAPTURE_MESSAGE),
    )
    async with foundation_session_factory() as session:
        promotion = await session.scalar(select(ArtifactPromotion))
        assert promotion is not None
    orphan_artifact = UUID("00000000-0000-7000-8000-000000008201")
    final_artifact = UUID("00000000-0000-7000-8000-000000008202")
    orphan_key = _upload_named(
        _storage(foundation_runtime),
        artifact_id=orphan_artifact,
        filename="staged/orphan/artifact.bin",
    )
    final_key = _upload_named(
        _storage(foundation_runtime),
        artifact_id=final_artifact,
        filename="artifact.bin",
    )
    old = NOW - timedelta(days=2)
    inventory.candidates = [
        StagedObjectCandidate(ORG, promotion.artifact_version_id, promotion.staged_key, old),
        StagedObjectCandidate(ORG, orphan_artifact, orphan_key, old),
        StagedObjectCandidate(ORG, final_artifact, final_key, old),
    ]
    cleanup = ArtifactCleanupTaskHandler(runtime)
    preview = await cleanup.cleanup(ORG, older_than=NOW - timedelta(days=1), dry_run=True)
    assert preview.eligible == (orphan_key,)
    assert preview.deleted == ()
    applied = await cleanup.cleanup(ORG, older_than=NOW - timedelta(days=1), dry_run=False)
    assert applied.deleted == (orphan_key,)
    _assert_object_exists(
        _storage(foundation_runtime),
        promotion.artifact_version_id,
        promotion.staged_key,
        DIGEST,
    )
    _assert_object_exists(_storage(foundation_runtime), final_artifact, final_key, DIGEST)
    with pytest.raises(ClientError):
        _read_object(_storage(foundation_runtime), orphan_artifact, orphan_key, DIGEST)


def test_registry_exposes_only_real_artifact_handlers() -> None:
    assert "review_platform.infrastructure.tasks.artifacts" in HANDLER_MODULES
    expected = {
        "ArtifactCaptureRequested": "review_platform.artifact_capture",
        "ArtifactPromotionRequested": "review_platform.artifact_promotion",
        "ArtifactPromotionRecoveryRequested": "review_platform.artifact_promotion_recovery",
        "ArtifactStagedCleanupRequested": "review_platform.artifact_staged_cleanup",
    }
    for event_type, name in expected.items():
        spec = REGISTRY.resolve_event(event_type)
        assert spec.name == name
        assert spec.kind == "artifact_capture"
        assert spec.requires_auth_revalidation is False


async def _add_manual_promotion(
    factory: AsyncSessionFactory,
    *,
    artifact_id: UUID,
    promotion_id: UUID,
    operation_id: UUID,
) -> None:
    async with session_scope(factory) as session:
        session.add(
            Operation(
                id=operation_id,
                organization_id=ORG,
                kind="artifact_capture",
                input_version="manual-promotion",
                state="processing",
                revision=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ArtifactVersion(
                id=artifact_id,
                organization_id=ORG,
                artifact_reference_id=REFERENCE,
                provider_version="manual",
                content_digest=sha256_digest(b"manual lease artifact"),
                object_key=f"{ORG}/{artifact_id}/artifact.bin",
                media_type="application/zip",
                byte_size=len(CONTENT),
                captured_at=NOW,
                artifact_metadata={},
            )
        )
        await session.flush()
        session.add(
            ArtifactPromotion(
                id=promotion_id,
                organization_id=ORG,
                artifact_version_id=artifact_id,
                operation_id=operation_id,
                staged_key=f"{ORG}/{artifact_id}/staged/{operation_id}",
                final_key=f"{ORG}/{artifact_id}/artifact.bin",
                state="db_committed",
                attempts=0,
                max_attempts=3,
                revision=0,
            )
        )


def _upload_named(
    storage: S3ObjectStorage,
    *,
    artifact_id: UUID,
    filename: str,
) -> str:
    return storage.upload(
        organization_id=str(ORG),
        artifact_version_id=str(artifact_id),
        source=io.BytesIO(CONTENT),
        media_type="application/zip",
        expected_digest=DIGEST,
        filename=filename,
    ).key


def _read_object(
    storage: S3ObjectStorage,
    artifact_id: UUID,
    key: str,
    digest: str,
) -> bytes:
    destination = io.BytesIO()
    storage.download_to(
        organization_id=str(ORG),
        artifact_version_id=str(artifact_id),
        key=key,
        destination=destination,
        expected_digest=digest,
    )
    return destination.getvalue()


def _assert_object_exists(
    storage: S3ObjectStorage,
    artifact_id: UUID,
    key: str,
    digest: str,
) -> None:
    assert _read_object(storage, artifact_id, key, digest) == CONTENT


_promotion_repository_protocol: Callable[
    [AsyncSessionFactory], ArtifactPromotionRepository
] = SqlArtifactPromotionRepository
