"""Tenant-scoped, bounded S3-compatible object primitives.

This module deliberately does not own artifact lifecycle state.  Application
services persist promotion intents and outbox messages; this adapter only moves,
reads, signs, and deletes bytes after enforcing tenant and digest boundaries.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Protocol, cast

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DEFAULT_CHUNK_BYTES = 1024 * 1024
_SPOOL_MEMORY_BYTES = 8 * 1024 * 1024


class ObjectStorageError(RuntimeError):
    """Base failure for a local object-storage invariant."""


class TenantObjectBoundaryError(ObjectStorageError):
    """An object operation crossed an organization/artifact namespace."""


class ObjectSizeLimitExceeded(ObjectStorageError):
    """The stream exceeded its declared byte budget."""


class ObjectDigestMismatch(ObjectStorageError):
    """The stream digest differs from immutable metadata."""


class S3Client(Protocol):
    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, object],
        ExpiresIn: int,
    ) -> str: ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    byte_size: int
    content_digest: str
    media_type: str
    etag: str | None = None


def _validate_segment(value: str, *, field: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value) or value in {".", ".."}:
        raise TenantObjectBoundaryError(f"invalid {field} path segment")
    return value


def _validate_object_name(value: str) -> str:
    parts = value.split("/")
    if not parts:
        raise TenantObjectBoundaryError("object name is empty")
    for part in parts:
        _validate_segment(part, field="object name")
    return "/".join(parts)


def build_object_key(
    *,
    organization_id: str,
    artifact_version_id: str,
    filename: str = "artifact.bin",
) -> str:
    """Return the canonical organization/artifact-version S3 key."""

    organization = _validate_segment(organization_id, field="organization_id")
    artifact_version = _validate_segment(artifact_version_id, field="artifact_version_id")
    name = _validate_object_name(filename)
    return f"{organization}/{artifact_version}/{name}"


def require_tenant_object_key(
    *,
    key: str,
    organization_id: str,
    artifact_version_id: str,
) -> None:
    prefix = (
        build_object_key(
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
            filename="placeholder",
        ).rsplit("/", 1)[0]
        + "/"
    )
    if not key.startswith(prefix):
        raise TenantObjectBoundaryError(
            "object key does not belong to the requested organization/artifact version"
        )
    remainder = key.removeprefix(prefix)
    _validate_object_name(remainder)


def _chunks(source: BinaryIO | Iterable[bytes], chunk_size: int) -> Iterator[bytes]:
    if hasattr(source, "read"):
        stream = cast(BinaryIO, source)
        while chunk := stream.read(chunk_size):
            if not isinstance(chunk, bytes):
                raise TypeError("binary object stream must yield bytes")
            yield chunk
        return
    for chunk in source:
        if not isinstance(chunk, bytes):
            raise TypeError("binary object stream must yield bytes")
        if chunk:
            yield chunk


def _validated_digest(expected_digest: str | None) -> str | None:
    if expected_digest is not None and not _DIGEST.fullmatch(expected_digest):
        raise ValueError("expected_digest must use sha256:<64 lowercase hex> format")
    return expected_digest


class S3ObjectStorage:
    """Bounded sync adapter suitable for boto3/MinIO clients and thread pools."""

    def __init__(
        self,
        *,
        client: S3Client,
        bucket: str,
        max_object_bytes: int,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
        signing_client: S3Client | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("bucket is required")
        if max_object_bytes < 1 or chunk_bytes < 1:
            raise ValueError("object and chunk limits must be positive")
        self._client = client
        self._signing_client = signing_client or client
        self._bucket = bucket
        self._max_object_bytes = max_object_bytes
        self._chunk_bytes = chunk_bytes

    @property
    def bucket(self) -> str:
        return self._bucket

    def key(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        filename: str = "artifact.bin",
    ) -> str:
        return build_object_key(
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
            filename=filename,
        )

    def upload(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        source: BinaryIO | Iterable[bytes],
        media_type: str,
        expected_digest: str | None = None,
        filename: str = "artifact.bin",
        max_bytes: int | None = None,
    ) -> StoredObject:
        """Verify a bounded stream before exposing any object in S3."""

        if not media_type or len(media_type) > 255:
            raise ValueError("media_type must contain 1..255 characters")
        digest_expected = _validated_digest(expected_digest)
        limit = self._effective_limit(max_bytes)
        key = self.key(
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
            filename=filename,
        )
        digest = hashlib.sha256()
        total = 0
        with SpooledTemporaryFile(max_size=min(limit, _SPOOL_MEMORY_BYTES), mode="w+b") as staged:
            for chunk in _chunks(source, self._chunk_bytes):
                total += len(chunk)
                if total > limit:
                    raise ObjectSizeLimitExceeded(f"object exceeds {limit} bytes")
                digest.update(chunk)
                staged.write(chunk)
            if total < 1:
                raise ObjectSizeLimitExceeded("empty artifact objects are not allowed")
            actual_digest = f"sha256:{digest.hexdigest()}"
            if digest_expected is not None and actual_digest != digest_expected:
                raise ObjectDigestMismatch("object digest does not match expected digest")
            staged.seek(0)
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=staged,
                ContentLength=total,
                ContentType=media_type,
                Metadata={
                    "organization-id": organization_id,
                    "artifact-version-id": artifact_version_id,
                    "content-digest": actual_digest,
                },
            )
        etag_value = response.get("ETag")
        return StoredObject(
            key=key,
            byte_size=total,
            content_digest=actual_digest,
            media_type=media_type,
            etag=etag_value if isinstance(etag_value, str) else None,
        )

    def download_to(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        key: str,
        destination: BinaryIO,
        expected_digest: str,
        max_bytes: int | None = None,
    ) -> StoredObject:
        """Verify tenant, size, and digest before releasing bytes to the caller."""

        require_tenant_object_key(
            key=key,
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
        )
        digest_expected = _validated_digest(expected_digest)
        assert digest_expected is not None
        limit = self._effective_limit(max_bytes)
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response.get("Body")
        if body is None:
            raise ObjectStorageError("S3 response omitted Body")
        content_length = response.get("ContentLength")
        if isinstance(content_length, int) and content_length > limit:
            self._close_body(body)
            raise ObjectSizeLimitExceeded(f"object exceeds {limit} bytes")

        digest = hashlib.sha256()
        total = 0
        try:
            with SpooledTemporaryFile(
                max_size=min(limit, _SPOOL_MEMORY_BYTES), mode="w+b"
            ) as staged:
                for chunk in self._body_chunks(body):
                    total += len(chunk)
                    if total > limit:
                        raise ObjectSizeLimitExceeded(f"object exceeds {limit} bytes")
                    digest.update(chunk)
                    staged.write(chunk)
                actual_digest = f"sha256:{digest.hexdigest()}"
                if actual_digest != digest_expected:
                    raise ObjectDigestMismatch("downloaded object digest does not match metadata")
                staged.seek(0)
                while chunk := staged.read(self._chunk_bytes):
                    destination.write(chunk)
        finally:
            self._close_body(body)
        content_type = response.get("ContentType")
        etag = response.get("ETag")
        return StoredObject(
            key=key,
            byte_size=total,
            content_digest=digest_expected,
            media_type=(
                content_type if isinstance(content_type, str) else "application/octet-stream"
            ),
            etag=etag if isinstance(etag, str) else None,
        )

    def sign_read(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        requested_by_organization_id: str,
        key: str,
        expires_in_seconds: int,
        download_filename: str | None = None,
    ) -> str:
        if requested_by_organization_id != organization_id:
            raise TenantObjectBoundaryError("requesting organization does not own the artifact")
        require_tenant_object_key(
            key=key,
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
        )
        if not 1 <= expires_in_seconds <= 3600:
            raise ValueError("signed read TTL must be between 1 and 3600 seconds")
        parameters = {"Bucket": self._bucket, "Key": key}
        if download_filename is not None:
            from urllib.parse import quote

            parameters["ResponseContentDisposition"] = "attachment; filename*=UTF-8''" + quote(
                download_filename, safe=""
            )
        return self._signing_client.generate_presigned_url(
            "get_object",
            Params=parameters,
            ExpiresIn=expires_in_seconds,
        )

    def delete(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        requested_by_organization_id: str,
        key: str,
    ) -> None:
        if requested_by_organization_id != organization_id:
            raise TenantObjectBoundaryError("requesting organization does not own the artifact")
        require_tenant_object_key(
            key=key,
            organization_id=organization_id,
            artifact_version_id=artifact_version_id,
        )
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def _effective_limit(self, requested: int | None) -> int:
        if requested is None:
            return self._max_object_bytes
        if requested < 1:
            raise ValueError("max_bytes must be positive")
        return min(requested, self._max_object_bytes)

    def _body_chunks(self, body: object) -> Iterator[bytes]:
        iter_chunks = getattr(body, "iter_chunks", None)
        if callable(iter_chunks):
            for chunk in iter_chunks(chunk_size=self._chunk_bytes):
                if not isinstance(chunk, bytes):
                    raise TypeError("S3 body must yield bytes")
                if chunk:
                    yield chunk
            return
        read = getattr(body, "read", None)
        if not callable(read):
            raise ObjectStorageError("S3 Body is not readable")
        while chunk := read(self._chunk_bytes):
            if not isinstance(chunk, bytes):
                raise TypeError("S3 body must yield bytes")
            yield chunk

    @staticmethod
    def _close_body(body: object) -> None:
        close = getattr(body, "close", None)
        if callable(close):
            close()


__all__ = [
    "ObjectDigestMismatch",
    "ObjectSizeLimitExceeded",
    "ObjectStorageError",
    "S3Client",
    "S3ObjectStorage",
    "StoredObject",
    "TenantObjectBoundaryError",
    "build_object_key",
    "require_tenant_object_key",
]
