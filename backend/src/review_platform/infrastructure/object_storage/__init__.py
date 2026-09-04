"""Object-storage infrastructure adapters."""

from .s3 import (
    ObjectDigestMismatch,
    ObjectSizeLimitExceeded,
    S3ObjectStorage,
    StoredObject,
    TenantObjectBoundaryError,
    build_object_key,
)

__all__ = [
    "ObjectDigestMismatch",
    "ObjectSizeLimitExceeded",
    "S3ObjectStorage",
    "StoredObject",
    "TenantObjectBoundaryError",
    "build_object_key",
]
