from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.services.artifact_capture import (
    ArtifactContentMismatch,
    ArtifactStageRequest,
    ArtifactStagingPort,
)
from review_platform.infrastructure.object_storage.promotions import (
    ArtifactOrphanCleaner,
    ArtifactPromotionCoordinator,
    ArtifactPromotionError,
    ArtifactPromotionRepository,
    FetchedArtifactContent,
    PromotionAuditCallback,
    PromotionAuditDraft,
    PromotionFailure,
    PromotionLease,
    PromotionObjectNotFound,
    S3ArtifactStaging,
    S3PromotionObjects,
    StagedObjectCandidate,
    StagedObjectInventory,
    StalePromotionLease,
)
from review_platform.infrastructure.object_storage.s3 import (
    S3ObjectStorage,
    TenantObjectBoundaryError,
)

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
REFERENCE = UUID("00000000-0000-7000-8000-000000000101")
ARTIFACT = UUID("00000000-0000-7000-8000-000000000102")
OPERATION = UUID("00000000-0000-7000-8000-000000000103")
PROMOTION = UUID("00000000-0000-7000-8000-000000000104")
TOKEN = UUID("00000000-0000-7000-8000-000000000105")
STALE_TOKEN = UUID("00000000-0000-7000-8000-000000000106")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
MEDIA_TYPE = "application/octet-stream"
CONTENT = b"immutable artifact bytes"
DIGEST = "sha256:4e5dd5cddfe6ca669736dac91231b75e7b7e7949f5152e9aaeb810cd2ede2076"


class FixtureFetcher:
    def __init__(
        self,
        content: bytes = CONTENT,
        *,
        media_type: str = MEDIA_TYPE,
        declared_size: int | None = None,
    ) -> None:
        self.content = content
        self.media_type = media_type
        self.declared_size = len(content) if declared_size is None else declared_size
        self.calls: list[tuple[str, int]] = []

    async def fetch(self, download_url: str, *, max_bytes: int) -> FetchedArtifactContent:
        self.calls.append((download_url, max_bytes))
        return FetchedArtifactContent(
            body=io.BytesIO(self.content),
            media_type=self.media_type,
            byte_size=self.declared_size,
            metadata={"provider": "fixture"},
        )


class FixturePromotionRepository:
    def __init__(
        self,
        lease: PromotionLease,
        *,
        live_keys: set[tuple[UUID, str]] | None = None,
    ) -> None:
        self.template = lease
        self.state = "db_committed"
        self.attempts = lease.attempts
        self.active_token: UUID | None = None
        self.active_expiry: datetime | None = None
        self.stale_on_complete = False
        self.completed = 0
        self.failure: PromotionFailure | None = None
        self.live_keys = live_keys or set()

    async def claim(
        self,
        organization_id: UUID,
        promotion_id: UUID,
        *,
        owner: str,
        token: UUID,
        now: datetime,
        lease_seconds: int,
    ) -> PromotionLease | None:
        if promotion_id != self.template.promotion_id or self.state in {
            "promoted",
            "action_required",
        }:
            return None
        if (
            self.state == "promoting"
            and self.active_expiry is not None
            and self.active_expiry > now
        ):
            return None
        self.state = "promoting"
        self.attempts += 1
        self.active_token = token
        self.active_expiry = now + timedelta(seconds=lease_seconds)
        return replace(
            self.template,
            owner=owner,
            token=token,
            expires_at=self.active_expiry,
            attempts=self.attempts,
        )

    async def complete(
        self,
        lease: PromotionLease,
        *,
        now: datetime,
        audit: PromotionAuditCallback,
        recovered_existing_final: bool,
    ) -> bool:
        if self.stale_on_complete:
            self.active_token = STALE_TOKEN
        if (
            self.state != "promoting"
            or self.active_token != lease.token
            or self.active_expiry is None
            or self.active_expiry <= now
        ):
            return False
        await audit(
            PromotionAuditDraft(
                organization_id=lease.organization_id,
                promotion_id=lease.promotion_id,
                artifact_version_id=lease.artifact_version_id,
                operation_id=lease.operation_id,
                before_revision=self.attempts,
                after_revision=self.attempts + 1,
                outcome="succeeded",
                details={"recovered_existing_final": recovered_existing_final},
            ),
            object(),
        )
        self.state = "promoted"
        self.completed += 1
        self.live_keys.discard((lease.organization_id, lease.staged_key))
        return True

    async def fail(
        self,
        lease: PromotionLease,
        *,
        error: Mapping[str, object],
        available_at: datetime,
        exhausted: bool,
        audit: PromotionAuditCallback,
    ) -> PromotionFailure | None:
        del available_at
        if self.state != "promoting" or self.active_token != lease.token:
            return None
        state = "action_required" if exhausted else "db_committed"
        await audit(
            PromotionAuditDraft(
                organization_id=lease.organization_id,
                promotion_id=lease.promotion_id,
                artifact_version_id=lease.artifact_version_id,
                operation_id=lease.operation_id,
                before_revision=self.attempts,
                after_revision=self.attempts + 1,
                outcome="action_required" if exhausted else "retryable_failed",
                details={"error": error},
            ),
            object(),
        )
        self.state = state
        self.failure = PromotionFailure(
            state=self.state,
            attempts=lease.attempts,
            max_attempts=lease.max_attempts,
            error=error,
        )
        self.active_token = None
        self.active_expiry = None
        return self.failure

    async def has_live_staged_intent(self, organization_id: UUID, staged_key: str) -> bool:
        return (organization_id, staged_key) in self.live_keys


class FixtureInventory:
    def __init__(self, candidates: list[StagedObjectCandidate]) -> None:
        self.candidates = candidates
        self.cutoffs: list[datetime] = []

    async def list_staged(self, *, older_than: datetime) -> list[StagedObjectCandidate]:
        self.cutoffs.append(older_than)
        return list(self.candidates)


class FixturePromotionAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[PromotionAuditDraft] = []

    async def record(self, draft: PromotionAuditDraft, *, transaction: object) -> None:
        del transaction
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(draft)


def _storage(runtime: FoundationRuntime) -> S3ObjectStorage:
    return cast(S3ObjectStorage, runtime.components()["object_storage"])


def _request(
    *,
    organization_id: UUID = ORG,
    artifact_version_id: UUID = ARTIFACT,
    expected_digest: str = DIGEST,
    expected_size: int = len(CONTENT),
    max_bytes: int = 1024,
) -> ArtifactStageRequest:
    return ArtifactStageRequest(
        organization_id=organization_id,
        artifact_version_id=artifact_version_id,
        operation_id=OPERATION,
        download_url="fixture://provider/artifact",
        expected_byte_size=expected_size,
        expected_content_digest=expected_digest,
        expected_media_type=MEDIA_TYPE,
        max_bytes=max_bytes,
        staged_key=(
            f"{organization_id}/{artifact_version_id}/staged/{OPERATION}/artifact.bin"
        ),
        final_key=f"{organization_id}/{artifact_version_id}/artifact.bin",
    )


def _lease(
    request: ArtifactStageRequest | None = None,
    *,
    attempts: int = 0,
    max_attempts: int = 3,
) -> PromotionLease:
    selected = request or _request()
    return PromotionLease(
        organization_id=selected.organization_id,
        promotion_id=PROMOTION,
        artifact_version_id=selected.artifact_version_id,
        operation_id=selected.operation_id,
        staged_key=selected.staged_key,
        final_key=selected.final_key,
        content_digest=selected.expected_content_digest,
        byte_size=selected.expected_byte_size,
        media_type=selected.expected_media_type,
        owner="unclaimed",
        token=TOKEN,
        expires_at=NOW,
        attempts=attempts,
        max_attempts=max_attempts,
    )


def _upload(
    storage: S3ObjectStorage,
    *,
    organization_id: UUID,
    artifact_version_id: UUID,
    filename: str,
    content: bytes = CONTENT,
) -> str:
    return storage.upload(
        organization_id=str(organization_id),
        artifact_version_id=str(artifact_version_id),
        source=io.BytesIO(content),
        media_type=MEDIA_TYPE,
        filename=filename,
    ).key


def _assert_exists(storage: S3ObjectStorage, lease: PromotionLease, key: str) -> bytes:
    output = io.BytesIO()
    S3PromotionObjects(storage).download_verified(lease, key, output)
    return output.getvalue()


def _assert_missing(storage: S3ObjectStorage, lease: PromotionLease, key: str) -> None:
    with pytest.raises(PromotionObjectNotFound):
        S3PromotionObjects(storage).download_verified(lease, key, io.BytesIO())


async def test_staging_is_bounded_and_uses_only_the_injected_fetcher(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    fetcher = FixtureFetcher()
    staging: ArtifactStagingPort = S3ArtifactStaging(storage=storage, fetcher=fetcher)

    staged = await staging.stage(_request())

    assert fetcher.calls == [("fixture://provider/artifact", 1024)]
    assert staged.staged_key == _request().staged_key
    assert staged.final_key == _request().final_key
    assert staged.content_digest == DIGEST
    assert _assert_exists(storage, _lease(), staged.staged_key) == CONTENT

    with pytest.raises(ArtifactContentMismatch, match="byte size"):
        await S3ArtifactStaging(
            storage=storage,
            fetcher=FixtureFetcher(declared_size=len(CONTENT) + 1),
        ).stage(_request())
    with pytest.raises(ArtifactContentMismatch, match="media type"):
        await S3ArtifactStaging(
            storage=storage,
            fetcher=FixtureFetcher(media_type="text/html"),
        ).stage(_request())
    with pytest.raises(ArtifactContentMismatch, match="digest"):
        await S3ArtifactStaging(storage=storage, fetcher=FixtureFetcher()).stage(
            _request(expected_digest="sha256:" + "0" * 64)
        )
    with pytest.raises(ArtifactContentMismatch, match="exceeds bound"):
        await S3ArtifactStaging(storage=storage, fetcher=FixtureFetcher()).stage(
            _request(max_bytes=len(CONTENT) - 1)
        )


async def test_promotion_moves_verified_bytes_then_completes_and_deletes_staged(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    request = _request(artifact_version_id=UUID("00000000-0000-7000-8000-000000000111"))
    await S3ArtifactStaging(storage=storage, fetcher=FixtureFetcher()).stage(request)
    repository = FixturePromotionRepository(_lease(request))
    audit = FixturePromotionAudit()
    coordinator = ArtifactPromotionCoordinator(
        repository=repository,
        objects=S3PromotionObjects(storage),
        audit=audit,
        owner="worker-1",
        token_factory=lambda: TOKEN,
        clock=lambda: NOW,
    )

    result = await coordinator.promote(
        organization_id=request.organization_id,
        promotion_id=PROMOTION,
    )

    assert result.state == "promoted"
    assert result.recovered_existing_final is False
    assert result.staged_deleted is True
    assert repository.completed == 1
    assert len(audit.events) == 1
    assert audit.events[0].outcome == "succeeded"
    assert audit.events[0].organization_id == ORG
    assert _assert_exists(storage, _lease(request), request.final_key) == CONTENT
    _assert_missing(storage, _lease(request), request.staged_key)


async def test_promotion_audit_failure_keeps_terminal_db_mutation_uncommitted(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    request = _request(artifact_version_id=UUID("00000000-0000-7000-8000-000000000119"))
    await S3ArtifactStaging(storage=storage, fetcher=FixtureFetcher()).stage(request)
    repository = FixturePromotionRepository(_lease(request))

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await ArtifactPromotionCoordinator(
            repository=repository,
            objects=S3PromotionObjects(storage),
            audit=FixturePromotionAudit(fail=True),
            owner="worker-1",
            token_factory=lambda: TOKEN,
            clock=lambda: NOW,
        ).promote(organization_id=ORG, promotion_id=PROMOTION)

    assert repository.state == "promoting"
    assert repository.completed == 0


async def test_promotion_recovers_when_final_exists_after_crash(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    request = _request(artifact_version_id=UUID("00000000-0000-7000-8000-000000000112"))
    await S3ArtifactStaging(storage=storage, fetcher=FixtureFetcher()).stage(request)
    _upload(
        storage,
        organization_id=request.organization_id,
        artifact_version_id=request.artifact_version_id,
        filename="artifact.bin",
    )
    repository = FixturePromotionRepository(_lease(request))

    result = await ArtifactPromotionCoordinator(
        repository=repository,
        objects=S3PromotionObjects(storage),
        audit=FixturePromotionAudit(),
        owner="recovery-worker",
        token_factory=lambda: TOKEN,
        clock=lambda: NOW,
    ).promote(organization_id=ORG, promotion_id=PROMOTION)

    assert result.recovered_existing_final is True
    assert repository.state == "promoted"
    assert _assert_exists(storage, _lease(request), request.final_key) == CONTENT
    _assert_missing(storage, _lease(request), request.staged_key)


async def test_digest_failure_is_sanitized_bounded_and_exhausts_attempts(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    request = _request(artifact_version_id=UUID("00000000-0000-7000-8000-000000000113"))
    _upload(
        storage,
        organization_id=ORG,
        artifact_version_id=request.artifact_version_id,
        filename=f"staged/{OPERATION}/artifact.bin",
        content=b"corrupt bytes",
    )
    repository = FixturePromotionRepository(_lease(request, max_attempts=1))

    with pytest.raises(ArtifactPromotionError, match="promotion failed"):
        await ArtifactPromotionCoordinator(
            repository=repository,
            objects=S3PromotionObjects(storage),
            audit=FixturePromotionAudit(),
            owner="worker-1",
            token_factory=lambda: TOKEN,
            clock=lambda: NOW,
        ).promote(organization_id=ORG, promotion_id=PROMOTION)

    assert repository.state == "action_required"
    assert repository.failure is not None
    assert len(str(repository.failure.error).encode()) <= 2048
    assert "corrupt bytes" not in str(repository.failure.error)
    _assert_exists(
        storage,
        replace(_lease(request), content_digest=_upload_digest(b"corrupt bytes")),
        request.staged_key,
    )
    _assert_missing(storage, _lease(request), request.final_key)


async def test_tenant_mismatch_and_stale_completion_cannot_mutate_intent(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    other_request = _request(
        organization_id=OTHER_ORG,
        artifact_version_id=UUID("00000000-0000-7000-8000-000000000114"),
    )
    mismatched_repository = FixturePromotionRepository(_lease(other_request))
    coordinator = ArtifactPromotionCoordinator(
        repository=mismatched_repository,
        objects=S3PromotionObjects(storage),
        audit=FixturePromotionAudit(),
        owner="worker-1",
        token_factory=lambda: TOKEN,
        clock=lambda: NOW,
    )
    with pytest.raises(TenantObjectBoundaryError, match="tenant/identity"):
        await coordinator.promote(organization_id=ORG, promotion_id=PROMOTION)
    assert mismatched_repository.completed == 0

    request = _request(artifact_version_id=UUID("00000000-0000-7000-8000-000000000115"))
    await S3ArtifactStaging(storage=storage, fetcher=FixtureFetcher()).stage(request)
    stale_repository = FixturePromotionRepository(_lease(request))
    stale_repository.stale_on_complete = True
    with pytest.raises(StalePromotionLease, match="lost CAS"):
        await ArtifactPromotionCoordinator(
            repository=stale_repository,
            objects=S3PromotionObjects(storage),
            audit=FixturePromotionAudit(),
            owner="worker-1",
            token_factory=lambda: TOKEN,
            clock=lambda: NOW,
        ).promote(organization_id=ORG, promotion_id=PROMOTION)
    assert stale_repository.completed == 0
    assert stale_repository.state == "promoting"
    assert _assert_exists(storage, _lease(request), request.staged_key) == CONTENT
    assert _assert_exists(storage, _lease(request), request.final_key) == CONTENT


async def test_orphan_cleanup_is_dry_runnable_and_intent_aware(
    foundation_runtime: FoundationRuntime,
) -> None:
    storage = _storage(foundation_runtime)
    dead_artifact = UUID("00000000-0000-7000-8000-000000000121")
    live_artifact = UUID("00000000-0000-7000-8000-000000000122")
    final_artifact = UUID("00000000-0000-7000-8000-000000000123")
    recent_artifact = UUID("00000000-0000-7000-8000-000000000124")
    dead_key = _upload(
        storage,
        organization_id=ORG,
        artifact_version_id=dead_artifact,
        filename=f"staged/{OPERATION}/artifact.bin",
    )
    live_key = _upload(
        storage,
        organization_id=ORG,
        artifact_version_id=live_artifact,
        filename=f"staged/{OPERATION}/artifact.bin",
    )
    final_key = _upload(
        storage,
        organization_id=ORG,
        artifact_version_id=final_artifact,
        filename="artifact.bin",
    )
    recent_key = _upload(
        storage,
        organization_id=ORG,
        artifact_version_id=recent_artifact,
        filename=f"staged/{OPERATION}/artifact.bin",
    )
    candidates = [
        StagedObjectCandidate(ORG, dead_artifact, dead_key, NOW - timedelta(days=2)),
        StagedObjectCandidate(ORG, live_artifact, live_key, NOW - timedelta(days=2)),
        StagedObjectCandidate(ORG, final_artifact, final_key, NOW - timedelta(days=2)),
        StagedObjectCandidate(ORG, recent_artifact, recent_key, NOW),
    ]
    repository = FixturePromotionRepository(
        _lease(),
        live_keys={(ORG, live_key)},
    )
    repository_port: ArtifactPromotionRepository = repository
    inventory: StagedObjectInventory = FixtureInventory(candidates)
    cleaner = ArtifactOrphanCleaner(
        repository=repository_port,
        inventory=inventory,
        objects=S3PromotionObjects(storage),
    )

    preview = await cleaner.cleanup(older_than=NOW - timedelta(days=1), dry_run=True)
    assert preview.eligible == (dead_key,)
    assert preview.deleted == ()
    assert preview.protected == 3
    assert _read_exact(storage, dead_artifact, dead_key, DIGEST) == CONTENT

    applied = await cleaner.cleanup(older_than=NOW - timedelta(days=1), dry_run=False)
    assert applied.eligible == (dead_key,)
    assert applied.deleted == (dead_key,)
    _assert_missing(storage, _lease(_request(artifact_version_id=dead_artifact)), dead_key)
    assert _read_exact(storage, live_artifact, live_key, DIGEST) == CONTENT
    assert _read_exact(storage, final_artifact, final_key, DIGEST) == CONTENT
    assert _read_exact(storage, recent_artifact, recent_key, DIGEST) == CONTENT


def _upload_digest(content: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _read_exact(
    storage: S3ObjectStorage,
    artifact_version_id: UUID,
    key: str,
    digest: str,
) -> bytes:
    destination = io.BytesIO()
    storage.download_to(
        organization_id=str(ORG),
        artifact_version_id=str(artifact_version_id),
        key=key,
        destination=destination,
        expected_digest=digest,
    )
    return destination.getvalue()
