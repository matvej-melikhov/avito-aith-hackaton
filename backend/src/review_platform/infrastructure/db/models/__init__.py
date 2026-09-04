"""Import all currently migrated SQLAlchemy models for metadata discovery."""

from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    ExternalCredential,
    ExternalIdentity,
    Invitation,
    OAuthState,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.models.learning import (
    Course,
    CourseMembership,
    CourseRun,
    DestinationBinding,
    ExternalCourseBinding,
)
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    CommandReceipt,
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.organization import Organization

__all__ = [
    "AgentAuthorization",
    "AuditEvent",
    "CommandReceipt",
    "Course",
    "CourseMembership",
    "CourseRun",
    "CourseRunHomework",
    "CourseRunHomeworkPublication",
    "Criterion",
    "CriterionSet",
    "DestinationBinding",
    "ExternalCourseBinding",
    "ExternalCredential",
    "ExternalIdentity",
    "Homework",
    "HomeworkVersion",
    "Invitation",
    "OAuthState",
    "Operation",
    "OperationAttempt",
    "Organization",
    "OrganizationMembership",
    "OutboxMessage",
    "Session",
    "User",
]
