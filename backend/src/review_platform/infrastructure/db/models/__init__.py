"""Import all currently migrated SQLAlchemy models for metadata discovery."""

from review_platform.infrastructure.db.models.ai_review import (
    AICriterionSuggestion,
    AIReviewAttempt,
    AIReviewEventReceipt,
    AIReviewRun,
    AISignal,
)
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
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactPromotion,
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)

__all__ = [
    "AICriterionSuggestion",
    "AIReviewAttempt",
    "AIReviewEventReceipt",
    "AIReviewRun",
    "AISignal",
    "AgentAuthorization",
    "ArtifactPromotion",
    "ArtifactReference",
    "ArtifactVersion",
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
    "ReviewCase",
    "ReviewCriterionDecision",
    "ReviewIteration",
    "ReviewNote",
    "ReviewRevision",
    "Session",
    "Submission",
    "SubmissionVersion",
    "User",
]
