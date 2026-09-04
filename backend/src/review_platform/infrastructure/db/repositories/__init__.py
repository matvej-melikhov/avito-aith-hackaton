"""Tenant-scoped persistence repositories."""

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

__all__ = [
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
    "UserIdentityRepository",
]
