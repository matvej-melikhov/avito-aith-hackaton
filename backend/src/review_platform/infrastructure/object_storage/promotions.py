"""Durable artifact staging, promotion recovery, and intent-aware cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast
from uuid import UUID

from review_platform.application.services.artifact_capture import (
    ArtifactContentMismatch,
    ArtifactStageRequest,
    ArtifactStagingPort,
    StagedArtifact,
)
from review_platform.domain.primitives import require_utc, sanitize_error, utc_now, uuid7
from review_platform.infrastructure.object_storage.s3 import (
    ObjectDigestMismatch,
    ObjectSizeLimitExceeded,
    S3ObjectStorage,
    StoredObject,
    TenantObjectBoundaryError,
    require_tenant_object_key,
)

_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


class ArtifactPromotionError(RuntimeError):
    pass


class PromotionObjectNotFound(ArtifactPromotionError):
    pass


class PromotionNotClaimed(ArtifactPromotionError):
    pass


class StalePromotionLease(ArtifactPromotionError):
    pass


@dataclass(frozen=True, slots=True)
class FetchedArtifactContent:
    body: BinaryIO | Iterable[bytes]
    media_type: str
    byte_size: int
    metadata: Mapping[str, str]


class BoundedContentFetcher(Protocol):
    """Provider-authorized fetcher; core never opens arbitrary URLs itself."""

    async def fetch(self, download_url: str, *, max_bytes: int) -> FetchedArtifactContent: ...


class S3ArtifactStaging(ArtifactStagingPort):
    def __init__(self, *, storage: S3ObjectStorage, fetcher: BoundedContentFetcher) -> None:
        self._storage = storage
        self._fetcher = fetcher

    async def stage(self, request: ArtifactStageRequest) -> StagedArtifact:
        _validate_stage_request(request)
        fetched = await self._fetcher.fetch(request.download_url, max_bytes=request.max_bytes)
        if fetched.byte_size != request.expected_byte_size or fetched.byte_size > request.max_bytes:
            raise ArtifactContentMismatch("fetched artifact byte size mismatched")
        if fetched.media_type != request.expected_media_type:
            raise ArtifactContentMismatch("fetched artifact media type mismatched")
        suffix = _suffix(request.staged_key, request.organization_id, request.artifact_version_id)
        try:
            stored = await asyncio.to_thread(
                self._storage.upload,
                organization_id=str(request.organization_id),
                artifact_version_id=str(request.artifact_version_id),
                source=fetched.body,
                media_type=fetched.media_type,
                expected_digest=request.expected_content_digest,
                filename=suffix,
                max_bytes=request.max_bytes,
            )
        except (ObjectDigestMismatch, ObjectSizeLimitExceeded) as error:
            raise ArtifactContentMismatch(str(error)) from error
        if stored.key != request.staged_key or stored.byte_size != request.expected_byte_size:
            raise ArtifactContentMismatch("staged S3 object identity or size mismatched")
        return StagedArtifact(
            organization_id=request.organization_id,
            artifact_version_id=request.artifact_version_id,
            staged_key=stored.key,
            final_key=request.final_key,
            byte_size=stored.byte_size,
            content_digest=stored.content_digest,
            media_type=stored.media_type,
            metadata={"fetcher": dict(fetched.metadata)},
        )


@dataclass(frozen=True, slots=True)
class PromotionLease:
    organization_id: UUID
    promotion_id: UUID
    artifact_version_id: UUID
    operation_id: UUID
    staged_key: str
    final_key: str
    content_digest: str
    byte_size: int
    media_type: str
    owner: str
    token: UUID
    expires_at: datetime
    attempts: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class PromotionFailure:
    state: str
    attempts: int
    max_attempts: int
    error: Mapping[str, object]


class ArtifactPromotionRepository(Protocol):
    async def claim(
        self,
        organization_id: UUID,
        promotion_id: UUID,
        *,
        owner: str,
        token: UUID,
        now: datetime,
        lease_seconds: int,
    ) -> PromotionLease | None: ...

    async def complete(self, lease: PromotionLease, *, now: datetime) -> bool: ...

    async def fail(
        self,
        lease: PromotionLease,
        *,
        error: Mapping[str, object],
        available_at: datetime,
        exhausted: bool,
    ) -> PromotionFailure | None: ...

    async def has_live_staged_intent(self, organization_id: UUID, staged_key: str) -> bool: ...


class S3PromotionObjects:
    """Verified object operations built only from Foundation S3 primitives."""

    def __init__(self, storage: S3ObjectStorage) -> None:
        self._storage = storage

    def download_verified(
        self,
        lease: PromotionLease,
        key: str,
        destination: BinaryIO,
    ) -> StoredObject:
        _validate_lease_key(lease, key)
        try:
            return self._storage.download_to(
                organization_id=str(lease.organization_id),
                artifact_version_id=str(lease.artifact_version_id),
                key=key,
                destination=destination,
                expected_digest=lease.content_digest,
                max_bytes=lease.byte_size,
            )
        except Exception as error:
            if _not_found(error):
                raise PromotionObjectNotFound(key) from error
            raise

    def upload_final(self, lease: PromotionLease, source: BinaryIO) -> StoredObject:
        suffix = _suffix(lease.final_key, lease.organization_id, lease.artifact_version_id)
        return self._storage.upload(
            organization_id=str(lease.organization_id),
            artifact_version_id=str(lease.artifact_version_id),
            source=source,
            media_type=lease.media_type,
            expected_digest=lease.content_digest,
            filename=suffix,
            max_bytes=lease.byte_size,
        )

    def delete(self, *, organization_id: UUID, artifact_version_id: UUID, key: str) -> None:
        self._storage.delete(
            organization_id=str(organization_id),
            artifact_version_id=str(artifact_version_id),
            requested_by_organization_id=str(organization_id),
            key=key,
        )


@dataclass(frozen=True, slots=True)
class PromotionResult:
    promotion_id: UUID
    operation_id: UUID
    artifact_version_id: UUID
    state: str
    recovered_existing_final: bool
    staged_deleted: bool


class ArtifactPromotionCoordinator:
    def __init__(
        self,
        *,
        repository: ArtifactPromotionRepository,
        objects: S3PromotionObjects,
        owner: str,
        token_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
        lease_seconds: int = 60,
        retry_seconds: int = 60,
    ) -> None:
        if not owner or not 1 <= lease_seconds <= 3600 or retry_seconds < 1:
            raise ValueError("promotion owner/lease/retry configuration is invalid")
        self._repository = repository
        self._objects = objects
        self._owner = owner
        self._token_factory = token_factory
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds

    async def promote(self, *, organization_id: UUID, promotion_id: UUID) -> PromotionResult:
        now = require_utc(self._clock())
        lease = await self._repository.claim(
            organization_id,
            promotion_id,
            owner=self._owner,
            token=self._token_factory(),
            now=now,
            lease_seconds=self._lease_seconds,
        )
        if lease is None:
            raise PromotionNotClaimed("promotion is unavailable or already terminal")
        self._validate_lease(lease, organization_id=organization_id, promotion_id=promotion_id)
        recovered = False
        try:
            with SpooledTemporaryFile(
                max_size=min(lease.byte_size, _SPOOL_MEMORY_BYTES), mode="w+b"
            ) as staged:
                stored = await asyncio.to_thread(
                    self._objects.download_verified,
                    lease,
                    lease.staged_key,
                    cast(BinaryIO, staged),
                )
                if stored.byte_size != lease.byte_size or stored.media_type != lease.media_type:
                    raise ArtifactContentMismatch("staged promotion object metadata mismatched")
                with SpooledTemporaryFile(
                    max_size=min(lease.byte_size, _SPOOL_MEMORY_BYTES), mode="w+b"
                ) as existing_final:
                    try:
                        final = await asyncio.to_thread(
                            self._objects.download_verified,
                            lease,
                            lease.final_key,
                            cast(BinaryIO, existing_final),
                        )
                        if final.byte_size != lease.byte_size:
                            raise ArtifactContentMismatch("existing final object size mismatched")
                        recovered = True
                    except PromotionObjectNotFound:
                        staged.seek(0)
                        final = await asyncio.to_thread(
                            self._objects.upload_final,
                            lease,
                            cast(BinaryIO, staged),
                        )
                        if final.key != lease.final_key or final.byte_size != lease.byte_size:
                            raise ArtifactContentMismatch(
                                "final object identity or size mismatched"
                            ) from None
            if not await self._repository.complete(lease, now=require_utc(self._clock())):
                raise StalePromotionLease("promotion lease completion lost CAS")
        except StalePromotionLease:
            raise
        except Exception as error:
            await self._record_failure(lease, error)
            raise ArtifactPromotionError("artifact promotion failed") from error

        staged_deleted = True
        try:
            await asyncio.to_thread(
                self._objects.delete,
                organization_id=lease.organization_id,
                artifact_version_id=lease.artifact_version_id,
                key=lease.staged_key,
            )
        except Exception:
            # DB truth is already promoted. Intent-aware GC can safely remove
            # this staged remnant later; never regress the completed state.
            staged_deleted = False
        return PromotionResult(
            promotion_id=lease.promotion_id,
            operation_id=lease.operation_id,
            artifact_version_id=lease.artifact_version_id,
            state="promoted",
            recovered_existing_final=recovered,
            staged_deleted=staged_deleted,
        )

    async def _record_failure(self, lease: PromotionLease, error: BaseException) -> None:
        bounded = sanitize_error(error)
        exhausted = lease.attempts >= lease.max_attempts
        result = await self._repository.fail(
            lease,
            error=bounded,
            available_at=require_utc(self._clock()) + timedelta(seconds=self._retry_seconds),
            exhausted=exhausted,
        )
        if result is None:
            raise StalePromotionLease("promotion failure lost lease CAS") from error

    @staticmethod
    def _validate_lease(
        lease: PromotionLease,
        *,
        organization_id: UUID,
        promotion_id: UUID,
    ) -> None:
        if lease.organization_id != organization_id or lease.promotion_id != promotion_id:
            raise TenantObjectBoundaryError("promotion lease tenant/identity mismatched")
        if lease.attempts < 1 or lease.attempts > lease.max_attempts or lease.byte_size < 1:
            raise ArtifactPromotionError("promotion lease attempt/size is invalid")
        _validate_lease_key(lease, lease.staged_key)
        _validate_lease_key(lease, lease.final_key)
        if "/staged/" not in lease.staged_key or "/staged/" in lease.final_key:
            raise TenantObjectBoundaryError("promotion staged/final key roles are invalid")


@dataclass(frozen=True, slots=True)
class StagedObjectCandidate:
    organization_id: UUID
    artifact_version_id: UUID
    key: str
    last_modified: datetime


class StagedObjectInventory(Protocol):
    async def list_staged(self, *, older_than: datetime) -> Sequence[StagedObjectCandidate]: ...


@dataclass(frozen=True, slots=True)
class CleanupResult:
    candidates: int
    protected: int
    eligible: tuple[str, ...]
    deleted: tuple[str, ...]
    dry_run: bool


class ArtifactOrphanCleaner:
    def __init__(
        self,
        *,
        repository: ArtifactPromotionRepository,
        inventory: StagedObjectInventory,
        objects: S3PromotionObjects,
    ) -> None:
        self._repository = repository
        self._inventory = inventory
        self._objects = objects

    async def cleanup(self, *, older_than: datetime, dry_run: bool) -> CleanupResult:
        cutoff = require_utc(older_than)
        candidates = await self._inventory.list_staged(older_than=cutoff)
        protected = 0
        deletable: list[StagedObjectCandidate] = []
        for candidate in candidates:
            require_utc(candidate.last_modified)
            require_tenant_object_key(
                key=candidate.key,
                organization_id=str(candidate.organization_id),
                artifact_version_id=str(candidate.artifact_version_id),
            )
            if "/staged/" not in candidate.key or candidate.last_modified > cutoff:
                protected += 1
                continue
            if await self._repository.has_live_staged_intent(
                candidate.organization_id, candidate.key
            ):
                protected += 1
                continue
            deletable.append(candidate)
        if not dry_run:
            for candidate in deletable:
                await asyncio.to_thread(
                    self._objects.delete,
                    organization_id=candidate.organization_id,
                    artifact_version_id=candidate.artifact_version_id,
                    key=candidate.key,
                )
        return CleanupResult(
            candidates=len(candidates),
            protected=protected,
            eligible=tuple(candidate.key for candidate in deletable),
            deleted=(
                tuple(candidate.key for candidate in deletable) if not dry_run else ()
            ),
            dry_run=dry_run,
        )


def _validate_stage_request(request: ArtifactStageRequest) -> None:
    if request.expected_byte_size < 1 or request.expected_byte_size > request.max_bytes:
        raise ArtifactContentMismatch("stage request byte size exceeds bound")
    if not request.expected_media_type:
        raise ArtifactContentMismatch("stage request media type is empty")
    _suffix(request.staged_key, request.organization_id, request.artifact_version_id)
    _suffix(request.final_key, request.organization_id, request.artifact_version_id)
    if "/staged/" not in request.staged_key or "/staged/" in request.final_key:
        raise TenantObjectBoundaryError("stage request keys have invalid roles")


def _validate_lease_key(lease: PromotionLease, key: str) -> None:
    require_tenant_object_key(
        key=key,
        organization_id=str(lease.organization_id),
        artifact_version_id=str(lease.artifact_version_id),
    )


def _suffix(key: str, organization_id: UUID, artifact_version_id: UUID) -> str:
    require_tenant_object_key(
        key=key,
        organization_id=str(organization_id),
        artifact_version_id=str(artifact_version_id),
    )
    return key.removeprefix(f"{organization_id}/{artifact_version_id}/")


def _not_found(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        details = response.get("Error")
        if isinstance(details, Mapping):
            return details.get("Code") in {"NoSuchKey", "NoSuchObject", "404"}
    return False


__all__ = [
    "ArtifactOrphanCleaner",
    "ArtifactPromotionCoordinator",
    "ArtifactPromotionError",
    "ArtifactPromotionRepository",
    "BoundedContentFetcher",
    "CleanupResult",
    "FetchedArtifactContent",
    "PromotionFailure",
    "PromotionLease",
    "PromotionNotClaimed",
    "PromotionObjectNotFound",
    "PromotionResult",
    "S3ArtifactStaging",
    "S3PromotionObjects",
    "StagedObjectCandidate",
    "StagedObjectInventory",
    "StalePromotionLease",
]
