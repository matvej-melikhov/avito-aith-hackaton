"""Tenant-scoped persistence repositories."""

from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewRepository
from review_platform.infrastructure.db.repositories.homeworks import SqlHomeworkRepository
from review_platform.infrastructure.db.repositories.identity import (
    AgentAuthorizationRepository,
    ExternalCredentialRepository,
    InvitationRepository,
    OAuthStateRepository,
    OrganizationMembershipRepository,
    SessionRepository,
    UserIdentityRepository,
)
from review_platform.infrastructure.db.repositories.learning import (
    CourseMembershipRepository,
    CourseRepository,
    CourseRunRepository,
    DestinationBindingRepository,
    ExternalCourseBindingRepository,
)
from review_platform.infrastructure.db.repositories.operations import (
    AuditEventRepository,
    AuditRepository,
    CommandReceiptRepository,
    OperationRepository,
    OutboxMessageRepository,
    OutboxRepository,
    ReceiptRepository,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    SqlReviewRevisionRepository,
)
from review_platform.infrastructure.db.repositories.submissions import (
    SqlArtifactCaptureAuthorization,
    SqlArtifactCaptureRepository,
    SqlArtifactCredentialBindings,
    SqlArtifactPreflightArchiveGuard,
    SqlArtifactPreflightRepository,
    SqlArtifactPromotionOutbox,
    SqlCaptureScheduler,
    SqlReviewIterationRepository,
    SqlSubmissionRepository,
    SqlSubmissionScopeAuthorization,
)

__all__ = [
    "AIReviewRepository",
    "AgentAuthorizationRepository",
    "AuditEventRepository",
    "AuditRepository",
    "CommandReceiptRepository",
    "CourseMembershipRepository",
    "CourseRepository",
    "CourseRunRepository",
    "DestinationBindingRepository",
    "ExternalCourseBindingRepository",
    "ExternalCredentialRepository",
    "InvitationRepository",
    "OAuthStateRepository",
    "OperationRepository",
    "OrganizationMembershipRepository",
    "OutboxMessageRepository",
    "OutboxRepository",
    "ReceiptRepository",
    "SessionRepository",
    "SqlArtifactCaptureAuthorization",
    "SqlArtifactCaptureRepository",
    "SqlArtifactCredentialBindings",
    "SqlArtifactPreflightArchiveGuard",
    "SqlArtifactPreflightRepository",
    "SqlArtifactPromotionOutbox",
    "SqlCaptureScheduler",
    "SqlHomeworkRepository",
    "SqlReviewIterationRepository",
    "SqlReviewRevisionRepository",
    "SqlSubmissionRepository",
    "SqlSubmissionScopeAuthorization",
    "UserIdentityRepository",
]
