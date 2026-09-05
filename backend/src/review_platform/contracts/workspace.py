"""Strict public/private schemas for workspace v2 and student self-review."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PositiveFloat,
    field_validator,
    model_validator,
)

from review_platform.contracts.commands import ReviewDecision, SaveReviewRevisionPayload

VERSION = "2.0.0"
Nonnegative = Annotated[int, Field(ge=0, strict=True)]
Points = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCommand[T](StrictModel):
    request_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=128)
    command_name: str = Field(min_length=1, max_length=100)
    target_id: UUID
    expected_revision: Nonnegative
    payload: T


class EmptyInput(StrictModel):
    pass


class DraftInput(StrictModel):
    artifact_url: str = Field(default="", max_length=2048)
    upload_id: UUID | None = None
    comment: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def one_source(self) -> DraftInput:
        if bool(self.artifact_url) == bool(self.upload_id):
            raise ValueError("exactly one artifact URL or uploaded snapshot is required")
        if self.artifact_url:
            from urllib.parse import urlsplit

            url = urlsplit(self.artifact_url)
            if (
                url.scheme != "https"
                or url.hostname not in {"github.com", "docs.google.com"}
                or url.username
                or url.password
            ):
                raise ValueError("only HTTPS GitHub and Google Docs links are supported")
        return self


class PublicationPolicyInput(StrictModel):
    self_review_limit: Nonnegative
    pass_score: Points = 0
    revision_days: Annotated[int, Field(ge=1, le=365)] = 7
    penalty_per_day: Points = 0
    max_resubmissions: Nonnegative = 3


class QuotaView(StrictModel):
    limit: Nonnegative
    used: Nonnegative
    reserved: Nonnegative
    remaining: Nonnegative
    policy_revision: Nonnegative
    active_run_id: UUID | None = None


class DraftView(StrictModel):
    id: UUID
    revision: Nonnegative
    publication_id: UUID
    artifact_url: str
    upload_id: UUID | None
    comment: str


class PublicCriterion(StrictModel):
    max_points: Points = 0
    id: UUID
    key: str
    title: str


class SelfReviewFinding(StrictModel):
    criterion_id: UUID
    status: Literal["met", "needs_attention", "not_checked"]
    feedback: str = Field(min_length=1, max_length=10000)
    evidence: str = Field(default="", max_length=4000)


class SelfReviewResult(StrictModel):
    findings: list[SelfReviewFinding] = Field(min_length=1, max_length=500)


class SelfReviewEvent(StrictModel):
    contract_version: Literal["2.0.0"]
    event_id: UUID
    run_id: UUID
    attempt: Annotated[int, Field(ge=1, strict=True)]
    sequence: Nonnegative
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["running", "succeeded", "failed"]
    result: SelfReviewResult | None = None
    error_code: (
        Literal["unavailable", "invalid_artifact", "unsupported_format", "invalid_result"] | None
    ) = None

    @model_validator(mode="after")
    def coherent(self) -> SelfReviewEvent:
        if (self.status == "succeeded") != (self.result is not None):
            raise ValueError("only successful final events contain a result")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("only failed events contain an error code")
        return self


class SelfReviewRequest(StrictModel):
    contract_version: Literal["2.0.0"] = "2.0.0"
    purpose: Literal["student_self_review"] = "student_self_review"
    run_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    input_fingerprint: str
    artifact_id: UUID
    artifact_url: str
    artifact_digest: str
    media_type: str
    student_text: str
    criteria: list[PublicCriterion]


class SelfReviewView(StrictModel):
    id: UUID
    draft_revision: Nonnegative
    status: Literal[
        "queued", "capturing", "pending", "running", "unknown_outcome", "succeeded", "failed"
    ]
    disposition: Literal["reserved", "consumed", "released"]
    artifact_id: UUID | None
    created_at: datetime
    result: SelfReviewResult | None
    error_code: str | None
    quota: QuotaView


class CourseInput(StrictModel):
    stepik_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def stepik_link(self) -> CourseInput:
        from urllib.parse import urlsplit

        if self.stepik_url is not None:
            parts = urlsplit(self.stepik_url)
            if (
                parts.scheme != "https"
                or parts.hostname != "stepik.org"
                or parts.username
                or parts.password
                or parts.port not in (None, 443)
            ):
                raise ValueError("use an HTTPS stepik.org course URL")
        return self

    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=100000)
    owner_id: UUID | None = None


class CourseRunInput(StrictModel):
    priority: Literal["assigned", "deadline"] = "assigned"
    title: str = Field(min_length=1, max_length=512)
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Europe/Moscow"

    @model_validator(mode="after")
    def dates(self) -> CourseRunInput:
        from zoneinfo import ZoneInfo

        ZoneInfo(self.timezone)
        if not self.starts_at.tzinfo or not self.ends_at.tzinfo or self.starts_at > self.ends_at:
            raise ValueError("timezone-aware ordered dates required")
        return self


class NotificationPreferences(StrictModel):
    deadline: bool = True
    revision: bool = True
    pool: bool = False


class PreferencesInput(StrictModel):
    show_pool: bool = True
    notifications: NotificationPreferences = Field(default_factory=NotificationPreferences)
    course_run_ids: list[UUID] = Field(max_length=1000)
    planned_minutes: Nonnegative
    until_at: datetime
    absent_from: datetime | None = None
    absent_until: datetime | None = None

    @model_validator(mode="after")
    def absence(self) -> PreferencesInput:
        if (self.absent_from is None) != (self.absent_until is None):
            raise ValueError("both absence dates are required")
        if self.absent_from and self.absent_until and self.absent_from > self.absent_until:
            raise ValueError("absence dates must be ordered")
        if any(v and not v.tzinfo for v in (self.until_at, self.absent_from, self.absent_until)):
            raise ValueError("timezone-aware dates required")
        return self


class AssignmentInput(StrictModel):
    student_id: UUID
    reviewer_id: UUID | None
    reason: str = Field(min_length=1, max_length=2048)


class CriterionSettings(StrictModel):
    score_step: PositiveFloat = Field(default=0.5, allow_inf_nan=False)
    evaluate_quality: bool = False


SubmissionSource = Literal["upload", "github", "google_docs"]


def default_submission_sources() -> list[SubmissionSource]:
    return ["upload", "github", "google_docs"]


class SourcePolicyInput(StrictModel):
    allowed_sources: list[SubmissionSource] = Field(
        default_factory=default_submission_sources, min_length=1, max_length=3
    )

    @field_validator("allowed_sources")
    @classmethod
    def distinct_sources(cls, value: list[SubmissionSource]) -> list[SubmissionSource]:
        if len(set(value)) != len(value):
            raise ValueError("submission sources must be distinct")
        return value


class HomeworkTitleInput(StrictModel):
    title: str = Field(min_length=1, max_length=512)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class PrivateHomeworkInput(SourcePolicyInput):
    criterion_settings: dict[str, CriterionSettings] = Field(default_factory=dict)
    material_upload_ids: list[UUID] = Field(default_factory=list, max_length=50)
    reviewer_guidance: str = Field(default="", max_length=50000)
    reference_upload_id: UUID | None = None
    criterion_classes: dict[str, Literal["formal", "content", "judgement"]] = Field(
        default_factory=dict
    )


class OutcomeInput(StrictModel):
    decision: Literal["needs_changes", "passed", "failed"]
    revision_deadline: datetime | None = None
    reason: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def requires_deadline(self) -> OutcomeInput:
        if self.decision == "needs_changes" and self.revision_deadline is None:
            raise ValueError("a revision deadline is required when requesting changes")
        if self.revision_deadline and self.revision_deadline.tzinfo is None:
            raise ValueError("revision deadline must include a timezone")
        return self


class ExportInput(StrictModel):
    course_run_id: UUID
    homework_id: UUID | None = None
    audience: Literal["team", "students"]
    columns: list[
        Literal[
            "student_id",
            "score",
            "status",
            "attempt",
            "feedback",
            "reviewer_id",
            "criterion_points",
            "artifact_url",
        ]
    ]
    include_unpublished: bool = False
    format: Literal["csv", "xlsx"]

    @field_validator("columns")
    @classmethod
    def unique_columns(cls, value: list[str]) -> list[str]:
        if not value or len(set(value)) != len(value):
            raise ValueError("select distinct columns")
        return value


class WorkspaceError(StrictModel):
    code: str
    message: str
    action: str | None = None
    quota: QuotaView | None = None


class ResourceResult(StrictModel):
    id: UUID
    revision: Nonnegative


class JsonResult(StrictModel):
    """Receipt payload, never an unconstrained public HTTP response model."""

    value: JsonValue


class DirectoryMember(StrictModel):
    id: UUID
    display_name: str
    roles: list[str]


class DirectoryView(StrictModel):
    items: list[DirectoryMember]


class CourseView(StrictModel):
    stepik_url: str | None = None
    id: UUID
    owner_id: UUID | None
    title: str
    description: str
    revision: Nonnegative
    status: str


class CourseRunView(StrictModel):
    reviewer_count: Nonnegative = 0
    id: UUID
    priority: Literal["assigned", "deadline"]
    priority_revision: Nonnegative
    course_id: UUID
    title: str
    starts_at: datetime | None
    ends_at: datetime | None
    timezone: str
    status: str
    revision: Nonnegative


class CatalogView(StrictModel):
    courses: list[CourseView]
    course_runs: list[CourseRunView]


class PreferencesView(StrictModel):
    revision: Nonnegative
    value: PreferencesInput | None


class AssignmentView(StrictModel):
    id: UUID
    student_id: UUID
    student_name: str
    reviewer_id: UUID | None
    reviewer_name: str | None
    revision: Nonnegative


class AssignmentsView(StrictModel):
    items: list[AssignmentView]


class WorkItem(StrictModel):
    reviewer_name: str | None = None
    updated_at: datetime | None = None
    taken_at: datetime | None = None
    participant_ids: list[UUID] = Field(default_factory=list)
    course_title: str = ""
    submission_deadline: datetime | None = None
    draft_id: UUID | None = None
    submission_id: UUID | None
    submission_revision: Nonnegative
    review_submission_version_id: UUID | None
    publication_id: UUID
    course_run_id: UUID
    homework_id: UUID
    title: str
    course_run_title: str
    student_id: UUID
    student_name: str
    submitted_at: datetime | None
    submission_version_id: UUID | None
    attempt: Nonnegative
    review_case_id: UUID | None
    review_case_revision: Nonnegative
    review_iteration_id: UUID | None
    review_revision: Nonnegative
    responsible_reviewer_id: UUID | None
    primary_reviewer_id: UUID | None
    status: str
    score: float | None
    feedback: str | None
    published_by: UUID | None
    review_deadline: datetime | None


class WorkList(StrictModel):
    items: list[WorkItem]
    total: Nonnegative
    offset: Nonnegative
    limit: Annotated[int, Field(ge=1, le=100)]


class DraftList(StrictModel):
    items: list[DraftView]


class StudentContext(SourcePolicyInput):
    material_upload_ids: list[UUID] = Field(default_factory=list)
    course_title: str = ""
    run_title: str = ""
    max_score: Points = 0
    submission_id: UUID | None = None
    publication_id: UUID
    homework_id: UUID
    homework_version_id: UUID
    course_run_id: UUID
    title: str
    student_text: str
    criteria: list[PublicCriterion]
    submission_deadline: datetime
    draft: DraftView | None
    quota: QuotaView | None
    self_reviews: list[SelfReviewView]
    policy: PublicationPolicyInput | None


class CriterionChangeStatistic(StrictModel):
    criterion_id: UUID
    title: str
    compared_works: Nonnegative
    changed_works: Nonnegative
    change_percent: float


class PeerComparisonStatistic(StrictModel):
    sample_count: Nonnegative = 0
    divergence_percent: float | None = None
    course_divergence_percent: float | None = None
    stricter_criteria: list[str] = Field(default_factory=list)
    softer_criteria: list[str] = Field(default_factory=list)
    fully_agreed_criteria: Nonnegative = 0
    compared_criteria: Nonnegative = 0


class StatisticView(StrictModel):
    publications: Nonnegative
    repeated_publications: Nonnegative = 0
    average_elapsed_minutes: float | None
    elapsed_sample_count: Nonnegative = 0
    average_wait_minutes: float | None = None
    wait_sample_count: Nonnegative = 0
    overdue_publications: Nonnegative = 0
    overdue_sample_count: Nonnegative = 0
    ai_acceptance_percent: float | None = None
    changed_decisions: Nonnegative
    compared_decisions: Nonnegative
    course_average_elapsed_minutes: float | None = None
    course_average_wait_minutes: float | None = None
    course_ai_acceptance_percent: float | None = None
    course_average_overdue_publications: float | None = None
    criterion_changes: list[CriterionChangeStatistic] = Field(default_factory=list)
    peer_comparison: PeerComparisonStatistic = Field(default_factory=PeerComparisonStatistic)
    from_date: datetime
    until_date: datetime


class UploadView(StrictModel):
    id: UUID
    filename: str
    media_type: str
    byte_size: Nonnegative
    digest: str


class DownloadView(StrictModel):
    filename: str | None = None
    url: str
    expires_at: datetime


class PrivateHomeworkView(PrivateHomeworkInput):
    revision: Nonnegative


class ReviewCriterionView(PublicCriterion):
    score_step: PositiveFloat = Field(default=0.5, allow_inf_nan=False)
    evaluate_quality: bool = False
    description: str
    max_points: Points
    position: Nonnegative


class ReviewDecisionEvent(StrictModel):
    timestamp: datetime
    text: str
    actor: str | None = None


class ReviewContext(StrictModel):
    decision_history: list[ReviewDecisionEvent] = Field(default_factory=list)
    submission_id: UUID | None = None
    latest_review_iteration_id: UUID | None = None
    artifact_label: str = "Снимок работы"
    ai_run_id: UUID | None = None
    signal_decisions: dict[str, Literal["confirm", "reject"]] = Field(default_factory=dict)
    title: str = ""
    student_name: str = ""
    attempt: Nonnegative = 0
    submitted_at: datetime | None = None
    homework_id: UUID
    criterion_set_id: UUID
    homework_version_id: UUID
    student_text: str
    max_score: float
    criteria: list[ReviewCriterionView]
    private_details: PrivateHomeworkView | None
    self_reviews: list[SelfReviewView]
    outcome: OutcomeInput | None
    outcome_revision: Nonnegative


class NotificationInput(StrictModel):
    reviewer_ids: list[UUID] = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=1, max_length=2000)


class MembershipInput(StrictModel):
    user_id: UUID
    kind: Literal["student", "reviewer"]
    active: bool = True


class UploadInput(StrictModel):
    filename: str = Field(min_length=1, max_length=200)
    media_type: Literal[
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    content_base64: str = Field(min_length=1, max_length=13_333_336)
    private: bool = False


class OpenWorkInput(StrictModel):
    submission_version_id: UUID


class ReleaseInput(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)


class ExportView(StrictModel):
    id: UUID
    status: Literal["queued", "processing", "succeeded", "failed"]
    rows: Nonnegative
    download: DownloadView | None
    error: str | None


class SubmissionAttemptView(StrictModel):
    id: UUID
    comment: str
    sequence: Nonnegative
    submitted_at: datetime
    status: str
    artifact_id: UUID | None
    capture_operation_id: UUID | None


class PublishedCriterionView(StrictModel):
    description: str
    title: str
    points: float
    max_points: float
    reason: str


class StudentReviewView(StrictModel):
    decision_reason: str | None = None
    id: UUID
    grade: GradePreview | None = None
    iteration_id: UUID
    submission_version_id: UUID
    published_at: datetime
    score: float
    feedback: str
    decision: str | None
    revision_deadline: datetime | None
    criteria: list[PublishedCriterionView]


class StudentSubmissionView(StrictModel):
    id: UUID
    title: str
    publication_id: UUID
    course_run_id: UUID
    attempts: list[SubmissionAttemptView]
    reviews: list[StudentReviewView]
    current_publication_id: UUID | None


class NotificationView(StrictModel):
    id: UUID
    course_run_id: UUID
    text: str
    created_at: datetime
    read: bool


class NotificationsView(StrictModel):
    items: list[NotificationView]


class PriorityInput(StrictModel):
    priority: Literal["assigned", "deadline"]


class PublicationPolicyView(PublicationPolicyInput):
    revision: Nonnegative


class EditorCriterion(StrictModel):
    score_step: PositiveFloat = Field(default=0.5, allow_inf_nan=False)
    evaluate_quality: bool = False
    key: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=512)
    description: str = Field(default="", max_length=20000)
    max_points: Points = 0
    check_class: Literal["formal", "content", "judgement"] = "content"


class EditorDraftInput(SourcePolicyInput):
    material_upload_ids: list[UUID] = Field(default_factory=list, max_length=50)
    course_run_id: UUID
    student_text: str = Field(default="", max_length=100000)
    max_score: Points = 0
    estimated_review_minutes: Annotated[int, Field(ge=1, le=10080)] = 30
    artifact_kinds: list[Literal["github", "google_docs"]] = Field(
        default_factory=list, max_length=2
    )
    criteria: list[EditorCriterion] = Field(default_factory=list, max_length=500)
    reviewer_guidance: str = Field(default="", max_length=50000)
    reference_upload_id: UUID | None = None
    policy: PublicationPolicyInput | None = None
    submission_deadline: datetime | None = None
    review_deadline: datetime | None = None


class EditorDraftView(StrictModel):
    homework_title: str = ""
    homework_revision: Nonnegative = 0
    revision: Nonnegative
    value: EditorDraftInput | None


class PublishWithPolicyInput(StrictModel):
    course_run_id: UUID
    submission_deadline: datetime
    review_deadline: datetime
    policy: PublicationPolicyInput
    expected_policy_revision: Nonnegative


class PublishedWorkspaceHomework(StrictModel):
    publication_id: UUID
    history_publication_id: UUID
    revision: Nonnegative
    policy_revision: Nonnegative


class GradePreview(StrictModel):
    raw_score: float
    penalty_days: Nonnegative
    penalty_rate: float
    penalty: float
    final_score: float
    pass_score: float | None
    policy_revision: Nonnegative | None


class PublishWorkspaceReviewInput(StrictModel):
    review_revision_id: UUID
    apply_penalty: bool


class PublishedGradeView(StrictModel):
    id: UUID
    grade: GradePreview


class ExtraRequirementInput(StrictModel):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=20000)
    max_points: Points


class PreparationView(StrictModel):
    id: UUID
    draft_revision: Nonnegative
    status: Literal["pending", "processing", "succeeded", "failed"]
    artifact_id: UUID | None
    filename: str | None
    error_code: str | None


class AssistEvidence(StrictModel):
    quote: str = Field(min_length=1, max_length=10000)
    locator: str | None = Field(default=None, max_length=2048)
    path: str | None = Field(default=None, max_length=2048)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    # Provider-supplied locations are claims, not locally verified source matches.
    verified: Literal[False] = False

    @model_validator(mode="after")
    def ordered_lines(self) -> AssistEvidence:
        if self.line_end is not None and (
            self.line_start is None or self.line_end < self.line_start
        ):
            raise ValueError("line_end requires an ordered line_start")
        return self


class AuthorshipSignal(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    probability: float | None = Field(default=None, ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=10000)
    evidence: list[AssistEvidence] = Field(default_factory=list, max_length=50)


class ReviewerSuggestion(StrictModel):
    requirement_met: bool | None = None
    sources: list[AssistEvidence] = Field(default_factory=list, max_length=50)
    criterion_id: UUID
    status: Literal["suggested", "needs_human", "not_checked"]
    proposed_points: Points | None
    reason: str = Field(min_length=1, max_length=10000)
    evidence: list[str] = Field(default_factory=list, max_length=50)
    confidence: Literal["low", "medium", "high"] = "medium"
    reviewer_note: str | None = Field(default=None, max_length=10000)
    student_feedback: str | None = Field(default=None, max_length=10000)


class ReviewAssistResult(StrictModel):
    authorship_signal: AuthorshipSignal | None = None
    feedback_draft: str | None = Field(default=None, max_length=20000)
    suggestions: list[ReviewerSuggestion] = Field(min_length=1, max_length=500)


class ReviewAssistRequest(StrictModel):
    contract_version: Literal["2.0.0"] = "2.0.0"
    purpose: Literal["reviewer_assist"] = "reviewer_assist"
    run_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    input_fingerprint: str
    review_iteration_id: UUID
    artifact_id: UUID
    artifact_url: str
    artifact_digest: str
    media_type: str
    student_text: str
    criteria: list[ReviewCriterionView]
    reviewer_guidance: str
    reference_url: str | None


class ReviewAssistEvent(StrictModel):
    contract_version: Literal["2.0.0"]
    event_id: UUID
    run_id: UUID
    attempt: Annotated[int, Field(ge=1, strict=True)]
    sequence: Nonnegative
    input_fingerprint: str
    status: Literal["running", "succeeded", "failed"]
    result: ReviewAssistResult | None = None
    error_code: (
        Literal["unavailable", "invalid_artifact", "unsupported_format", "invalid_result"] | None
    ) = None

    @model_validator(mode="after")
    def coherent(self) -> ReviewAssistEvent:
        if (self.status == "succeeded") != (self.result is not None):
            raise ValueError("only successful results contain suggestions")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("only failed results contain an error code")
        return self


class ReviewAssistView(StrictModel):
    id: UUID
    status: Literal["queued", "running", "unknown_outcome", "succeeded", "failed", "stale"]
    revision: Nonnegative
    result: ReviewAssistResult | None
    error_code: str | None
    created_at: datetime


class WorkspaceReviewSaveInput(StrictModel):
    signal_decisions: dict[str, Literal["confirm", "reject"]] = Field(
        default_factory=dict, max_length=1
    )
    draft: SaveReviewRevisionPayload
    ai_run_id: UUID | None = None


class LocalIdentity(StrictModel):
    key: str
    label: str
    roles: list[str]


class LocalIdentities(StrictModel):
    enabled: bool
    items: list[LocalIdentity]


class LocalLoginInput(StrictModel):
    identity: str


class ProfileView(StrictModel):
    user_id: UUID
    display_name: str


class CoordinatorHomeworkItem(StrictModel):
    id: UUID
    title: str
    revision: Nonnegative
    latest_version_number: int | None
    published_run_ids: list[UUID]


class CoordinatorHomeworkList(StrictModel):
    course_id: UUID
    items: list[CoordinatorHomeworkItem]


class WorkspaceRevisionSummary(StrictModel):
    id: UUID
    review_iteration_id: UUID
    revision_number: Nonnegative
    author_user_id: UUID
    feedback: str
    total_score: float
    created_at: datetime


class WorkspaceNoteView(StrictModel):
    id: UUID
    criterion_id: UUID | None
    text: str
    author_user_id: UUID
    position: Nonnegative


class ReviewDraftView(StrictModel):
    revision: Nonnegative
    status: Literal["queued", "in_review", "ready_to_publish", "published", "canceled"]
    current_review_revision_id: UUID | None
    current_review_revision: WorkspaceRevisionSummary | None
    criterion_decisions: list[ReviewDecision]
    review_notes: list[WorkspaceNoteView]


class SearchHomeworkView(StrictModel):
    id: UUID
    title: str
    course_run_id: UUID | None


class WorkspaceSearchView(StrictModel):
    students: list[WorkItem]
    homeworks: list[SearchHomeworkView]


class StudentHomeworkItem(StrictModel):
    publication_id: UUID
    title: str
    course_title: str
    course_run_title: str
    submission_deadline: datetime
    status: str
    attempt: Nonnegative
    score: float | None
    submission_id: UUID | None
    draft_id: UUID | None


class StudentHomeworkList(StrictModel):
    items: list[StudentHomeworkItem]
    total: Nonnegative
    offset: Nonnegative
    limit: Annotated[int, Field(ge=1, le=100)]


class WorkspaceStatusCounts(StrictModel):
    all: Nonnegative = 0
    draft: Nonnegative = 0
    pending_review: Nonnegative = 0
    in_review: Nonnegative = 0
    needs_changes: Nonnegative = 0
    repeat_review: Nonnegative = 0
    passed: Nonnegative = 0
    failed: Nonnegative = 0


class WorkspacePoolMetrics(StrictModel):
    waiting: Nonnegative
    submitted: Nonnegative
    stuck: Nonnegative
    active_reviewers: Nonnegative
    total_reviewers: Nonnegative
    average_wait_minutes: float | None


class TypicalCriterionFailure(StrictModel):
    criterion_id: UUID
    title: str
    failed: Nonnegative
    reviewed: Nonnegative
    ratio: float


class WorkspaceInsightsView(StrictModel):
    status_counts: WorkspaceStatusCounts
    pool_metrics: WorkspacePoolMetrics
    typical_failures: list[TypicalCriterionFailure]
