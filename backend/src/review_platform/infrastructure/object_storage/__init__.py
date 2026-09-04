"""Object-storage infrastructure adapters."""

from .promotions import (
    ArtifactOrphanCleaner,
    ArtifactPromotionCoordinator,
    ArtifactPromotionRepository,
    S3ArtifactStaging,
    S3PromotionObjects,
)
from .s3 import (
    ObjectDigestMismatch,
    ObjectSizeLimitExceeded,
    S3ObjectStorage,
    StoredObject,
    TenantObjectBoundaryError,
    build_object_key,
)

__all__ = [
    "ArtifactOrphanCleaner",
    "ArtifactPromotionCoordinator",
    "ArtifactPromotionRepository",
    "ObjectDigestMismatch",
    "ObjectSizeLimitExceeded",
    "S3ArtifactStaging",
    "S3ObjectStorage",
    "S3PromotionObjects",
    "StoredObject",
    "TenantObjectBoundaryError",
    "build_object_key",
]
