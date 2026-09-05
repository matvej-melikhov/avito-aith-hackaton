"""Strict generated-style models for the frozen AI review contract."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from review_platform.contracts.registry import CONTRACT_VERSION
from review_platform.domain.ai_fingerprint import (
    ImmutableAIReviewInput,
    verify_ai_fingerprint,
)
from review_platform.domain.primitives import validate_digest

type CanonicalDigest = Annotated[str, AfterValidator(validate_digest)]
type NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
type PositiveInteger = Annotated[int, Field(ge=1, strict=True)]
type NonNegativeNumber = Annotated[float, Field(ge=0, strict=True)]

class _StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class AIArtifactObject(_StrictContractModel):
    key: NonEmptyText
    media_type: NonEmptyText
    byte_size: PositiveInteger


class AIArtifactEnvelope(_StrictContractModel):
    contract_version: Literal["1.1.0"]
    organization_id: UUID
    artifact_reference_id: UUID
    artifact_version_id: UUID
    provider: Literal["github", "google_docs"]
    provider_version: NonEmptyText
    content_digest: CanonicalDigest
    captured_at: AwareDatetime
    object: AIArtifactObject
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AIArtifactDownload(_StrictContractModel):
    url: AnyUrl
    expires_at: AwareDatetime


class AIHomework(_StrictContractModel):
    version_id: UUID
    title: NonEmptyText
    student_text: str
    digest: CanonicalDigest


class AICriterion(_StrictContractModel):
    id: UUID
    key: NonEmptyText
    title: NonEmptyText
    description: str
    max_points: NonNegativeNumber


class AICriteria(_StrictContractModel):
    set_id: UUID
    digest: CanonicalDigest
    items: Annotated[list[AICriterion], Field(min_length=1)]

    @field_validator("items")
    @classmethod
    def require_unique_criterion_ids(
        cls, value: list[AICriterion]
    ) -> list[AICriterion]:
        _require_unique([item.id for item in value], "criteria.items criterion ids")
        return value


class AIEvidence(_StrictContractModel):
    locator: NonEmptyText
    quote: str
    verified: StrictBool


class AISuggestion(_StrictContractModel):
    criterion_id: UUID
    status: Literal["suggested", "needs_human", "not_checked"]
    proposed_points: NonNegativeNumber | None
    reason: str
    evidence: list[AIEvidence]
    confidence: Literal["low", "medium", "high"]
    reviewer_note: str | None
    student_feedback: str | None
    flags: list[str]

    @field_validator("flags")
    @classmethod
    def require_unique_flags(cls, value: list[str]) -> list[str]:
        _require_unique(value, "suggestion flags")
        return value

    @model_validator(mode="after")
    def require_null_points_when_not_checked(self) -> AISuggestion:
        if self.status == "not_checked" and self.proposed_points is not None:
            raise ValueError("not_checked suggestions require proposed_points=null")
        return self


class AICriterionCoverage(_StrictContractModel):
    expected_criterion_ids: Annotated[list[UUID], Field(min_length=1)]
    reported_criterion_ids: list[UUID]
    complete: StrictBool

    @field_validator("expected_criterion_ids", "reported_criterion_ids")
    @classmethod
    def require_unique_ids(cls, value: list[UUID]) -> list[UUID]:
        _require_unique(value, "criterion coverage ids")
        return value


class AISignal(_StrictContractModel):
    level: Literal["none", "low", "medium", "high", "insufficient_data"]
    evidence: list[AIEvidence]
    limitations: list[str]
    questions: list[str]


class AIReviewError(_StrictContractModel):
    code: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    message: Annotated[str, StringConstraints(max_length=2048)]
    retryable: StrictBool


class AIReviewRequest(_StrictContractModel):
    """Frozen request envelope sent to the external AI component."""

    __contract_schema__: ClassVar[str] = "ai-review.schema.json"
    __contract_definition__: ClassVar[str] = "request"
    __contract_version__: ClassVar[str] = CONTRACT_VERSION

    contract_version: Literal["1.1.0"]
    run_id: UUID
    input_fingerprint: CanonicalDigest
    fingerprint_algorithm: Literal["jcs-sha256-v1"]
    organization_id: UUID
    course_run_id: UUID
    submission_version_id: UUID
    review_iteration_id: UUID
    credential_binding_id: UUID
    credential_binding_version: PositiveInteger
    artifact: AIArtifactEnvelope
    artifact_download: AIArtifactDownload
    homework: AIHomework
    criteria: AICriteria

    @model_validator(mode="after")
    def validate_immutable_provenance(self) -> AIReviewRequest:
        if self.artifact.organization_id != self.organization_id:
            raise ValueError("artifact organization_id must match request organization_id")
        immutable_input = ImmutableAIReviewInput(
            contract_version=self.contract_version,
            organization_id=self.organization_id,
            course_run_id=self.course_run_id,
            submission_version_id=self.submission_version_id,
            review_iteration_id=self.review_iteration_id,
            artifact_version_id=self.artifact.artifact_version_id,
            artifact_content_digest=self.artifact.content_digest,
            homework_version_id=self.homework.version_id,
            homework_digest=self.homework.digest,
            criterion_set_id=self.criteria.set_id,
            criterion_set_digest=self.criteria.digest,
        )
        if not verify_ai_fingerprint(
            immutable_input,
            self.input_fingerprint,
            algorithm=self.fingerprint_algorithm,
        ):
            raise ValueError("input_fingerprint does not match immutable request inputs")
        return self


class AIReviewEvent(_StrictContractModel):
    """One sequenced event returned by the external AI component."""

    __contract_schema__: ClassVar[str] = "ai-review.schema.json"
    __contract_definition__: ClassVar[str] = "event"
    __contract_version__: ClassVar[str] = CONTRACT_VERSION

    contract_version: Literal["1.1.0"]
    run_id: UUID
    attempt_id: UUID
    attempt_number: PositiveInteger
    event_id: UUID
    sequence: PositiveInteger
    input_fingerprint: CanonicalDigest
    status: Literal[
        "running", "partial", "succeeded", "retryable_failed", "action_required"
    ]
    is_final: StrictBool
    suggestions: list[AISuggestion]
    criterion_coverage: AICriterionCoverage
    ai_signal: AISignal | None
    error: AIReviewError | None

    @field_validator("suggestions")
    @classmethod
    def require_structurally_unique_suggestions(
        cls, value: list[AISuggestion]
    ) -> list[AISuggestion]:
        _require_unique(
            [suggestion.model_dump_json() for suggestion in value],
            "suggestions",
        )
        return value

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> AIReviewEvent:
        if self.status in {"running", "partial"}:
            if self.is_final:
                raise ValueError(f"{self.status} events must be nonfinal")
            if self.error is not None:
                raise ValueError(f"{self.status} events require error=null")
        elif self.status == "succeeded":
            if not self.is_final:
                raise ValueError("succeeded events must be final")
            if self.error is not None:
                raise ValueError("succeeded events require error=null")
            if self.ai_signal is None:
                raise ValueError("succeeded events require ai_signal")
            if not self.suggestions:
                raise ValueError("succeeded events require suggestions")
            if not self.criterion_coverage.complete:
                raise ValueError("succeeded events require complete criterion coverage")
        else:
            if not self.is_final:
                raise ValueError(f"{self.status} events must be final")
            if self.error is None:
                raise ValueError(f"{self.status} events require a typed error")
        return self


def validate_event_against_request(
    event: AIReviewEvent,
    request: AIReviewRequest,
) -> AIReviewEvent:
    """Validate request-relative event provenance, coverage, and score bounds."""

    if event.contract_version != request.contract_version:
        raise ValueError("event contract_version does not match request")
    if event.run_id != request.run_id:
        raise ValueError("event run_id does not match request")
    if event.input_fingerprint != request.input_fingerprint:
        raise ValueError("event input_fingerprint does not match request")

    expected_ids = [criterion.id for criterion in request.criteria.items]
    expected_id_set = set(expected_ids)
    coverage = event.criterion_coverage
    if set(coverage.expected_criterion_ids) != expected_id_set:
        raise ValueError("expected criterion coverage must exactly match request criteria")

    suggestion_ids = [suggestion.criterion_id for suggestion in event.suggestions]
    _require_unique(suggestion_ids, "suggestion criterion ids")
    if set(coverage.reported_criterion_ids) != set(suggestion_ids):
        raise ValueError("reported criterion ids must exactly match suggestion ids")

    criterion_by_id = {criterion.id: criterion for criterion in request.criteria.items}
    for suggestion in event.suggestions:
        criterion = criterion_by_id.get(suggestion.criterion_id)
        if criterion is None:
            raise ValueError(f"unknown suggestion criterion_id {suggestion.criterion_id}")
        if (
            suggestion.proposed_points is not None
            and suggestion.proposed_points > criterion.max_points
        ):
            raise ValueError(
                f"suggestion points for criterion {criterion.id} exceed max_points"
            )

    is_complete = set(suggestion_ids) == expected_id_set
    if coverage.complete != is_complete:
        raise ValueError("criterion coverage complete flag does not match reported criteria")
    if event.status == "succeeded" and not is_complete:
        raise ValueError("succeeded event requires exactly one suggestion per criterion")
    return event


def _require_unique[ItemT](values: list[ItemT], field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")


__all__ = [
    "AIArtifactDownload",
    "AIArtifactEnvelope",
    "AIArtifactObject",
    "AICriteria",
    "AICriterion",
    "AICriterionCoverage",
    "AIEvidence",
    "AIHomework",
    "AIReviewError",
    "AIReviewEvent",
    "AIReviewRequest",
    "AISignal",
    "AISuggestion",
    "validate_event_against_request",
]
