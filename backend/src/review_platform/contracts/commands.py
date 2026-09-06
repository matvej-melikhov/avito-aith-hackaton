"""Strict typed models for the frozen 1.1.0 command boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from review_platform.contracts.registry import CONTRACT_VERSION

ShortText = Annotated[str, StringConstraints(min_length=1)]
ReasonText = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
IdempotencyKey = Annotated[str, StringConstraints(min_length=16, max_length=128)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(ge=1, strict=True)]
NonNegativeNumber = Annotated[float, Field(ge=0, strict=True)]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class CommandName(StrEnum):
    ACTIVATE_BOOTSTRAP = "activate_bootstrap"
    CREATE_INVITATION = "create_invitation"
    REVOKE_INVITATION = "revoke_invitation"
    START_COURSE_IMPORT = "start_course_import"
    ARCHIVE_COURSE = "archive_course"
    RESTORE_COURSE = "restore_course"
    ARCHIVE_COURSE_RUN = "archive_course_run"
    RESTORE_COURSE_RUN = "restore_course_run"
    CHANGE_MEMBERSHIP_ROLES = "change_membership_roles"
    CREATE_HOMEWORK = "create_homework"
    CREATE_HOMEWORK_VERSION = "create_homework_version"
    PUBLISH_HOMEWORK_VERSION = "publish_homework_version"
    SET_REVIEWER_COURSE_SELECTION = "set_reviewer_course_selection"
    SET_REVIEWER_AVAILABILITY = "set_reviewer_availability"
    PREFLIGHT_SUBMISSION = "preflight_submission"
    SUBMIT_WORK = "submit_work"
    OPEN_REVIEW_ITERATION = "open_review_iteration"
    MIGRATE_REVIEW_REQUIREMENTS = "migrate_review_requirements"
    CREATE_REVIEW_CORRECTION = "create_review_correction"
    RECORD_REVIEW_RESPONSIBILITY = "record_review_responsibility"
    SAVE_REVIEW_REVISION = "save_review_revision"
    REQUEST_REVIEW_PUBLICATION = "request_review_publication"
    PUBLISH_REVIEW = "publish_review"
    START_AI_REVIEW = "start_ai_review"
    RETRY_DELIVERY = "retry_delivery"
    GRANT_AGENT_AUTHORIZATION = "grant_agent_authorization"
    REVOKE_AGENT_AUTHORIZATION = "revoke_agent_authorization"
    RECOVER_METHODOLOGIST = "recover_methodologist"


class RevisionTarget(StrEnum):
    ORGANIZATION = "organization"
    MEMBERSHIP = "membership"
    INVITATION = "invitation"
    COURSE = "course"
    COURSE_RUN = "course_run"
    COURSE_RUN_HOMEWORK = "course_run_homework"
    HOMEWORK = "homework"
    HOMEWORK_VERSION = "homework_version"
    SUBMISSION = "submission"
    REVIEW_CASE = "review_case"
    REVIEW_ITERATION = "review_iteration"
    PUBLICATION_REQUEST = "publication_request"
    EXTERNAL_DELIVERY = "external_delivery"
    AGENT_AUTHORIZATION = "agent_authorization"


class Transport(StrEnum):
    REST = "rest"
    MCP = "mcp"
    WORKER = "worker"
    OPERATOR = "operator"


class MembershipRole(StrEnum):
    METHODOLOGIST = "methodologist"
    REVIEWER = "reviewer"
    STUDENT = "student"


class AgentScope(StrEnum):
    COURSES_READ = "courses:read"
    REVIEW_PREFERENCES_WRITE = "review_preferences:write"
    REVIEW_QUEUE_READ = "review_queue:read"
    REVIEWS_READ = "reviews:read"
    REVIEWS_WRITE = "reviews:write"
    AI_REVIEWS_START = "ai_reviews:start"
    OPERATIONS_READ = "operations:read"
    PUBLICATION_REQUESTS_WRITE = "publication_requests:write"


class UserActor(StrictContractModel):
    type: Literal["user"]
    user_id: UUID
    membership_revision: NonNegativeInt
    auth_epoch: NonNegativeInt


class AgentActor(StrictContractModel):
    type: Literal["agent"]
    user_id: UUID
    membership_revision: NonNegativeInt
    auth_epoch: NonNegativeInt
    agent_id: UUID
    agent_authorization_id: UUID


class InstallationOperatorActor(StrictContractModel):
    type: Literal["installation_operator"]
    installation_operator_id: ShortText
    reason: ShortText


type RequestActor = Annotated[
    UserActor | AgentActor | InstallationOperatorActor,
    Field(discriminator="type"),
]
OperatorActor = InstallationOperatorActor


class EmptyPayload(StrictContractModel):
    pass


class ExternalIdentity(StrictContractModel):
    provider: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    issuer: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    subject: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ActivateBootstrapPayload(StrictContractModel):
    external_identity: ExternalIdentity


class CreateInvitationPayload(StrictContractModel):
    email: str
    role: Literal["methodologist", "reviewer"]
    expires_at: AwareDatetime

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        local, separator, domain = value.rpartition("@")
        if not separator or not local or "." not in domain or any(char.isspace() for char in value):
            raise ValueError("email must be a valid address")
        return value


class ReasonPayload(StrictContractModel):
    reason: ReasonText


class StartCourseImportPayload(StrictContractModel):
    provider: ShortText
    external_url: ShortText

    @field_validator("external_url")
    @classmethod
    def validate_external_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("external_url must be an absolute URI")
        return value


class ChangeMembershipRolesPayload(StrictContractModel):
    roles: list[MembershipRole]

    @field_validator("roles")
    @classmethod
    def unique_roles(cls, value: list[MembershipRole]) -> list[MembershipRole]:
        return _require_unique(value, "roles")


class CreateHomeworkPayload(StrictContractModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class CriterionInput(StrictContractModel):
    key: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    description: Annotated[str, StringConstraints(max_length=20_000)]
    max_points: NonNegativeNumber


class CreateHomeworkVersionPayload(StrictContractModel):
    student_text: Annotated[str, StringConstraints(max_length=100_000)]
    max_score: NonNegativeNumber
    artifact_kinds: Annotated[
        list[Literal["github", "google_docs"]], Field(min_length=1, max_length=2)
    ]
    estimated_review_minutes: Annotated[int, Field(ge=1, le=10_080, strict=True)]
    criteria: Annotated[list[CriterionInput], Field(min_length=1, max_length=500)]

    @field_validator("artifact_kinds")
    @classmethod
    def unique_artifact_kinds(
        cls, value: list[Literal["github", "google_docs"]]
    ) -> list[Literal["github", "google_docs"]]:
        return _require_unique(value, "artifact_kinds")


class PublishHomeworkVersionPayload(StrictContractModel):
    course_run_id: UUID
    submission_deadline: AwareDatetime
    review_deadline: AwareDatetime


class SetReviewerCourseSelectionPayload(StrictContractModel):
    course_run_ids: Annotated[list[UUID], Field(max_length=1000)]

    @field_validator("course_run_ids")
    @classmethod
    def unique_course_run_ids(cls, value: list[UUID]) -> list[UUID]:
        return _require_unique(value, "course_run_ids")


class SetReviewerAvailabilityPayload(StrictContractModel):
    planned_minutes: NonNegativeInt
    until_at: AwareDatetime


class PreflightSubmissionPayload(StrictContractModel):
    artifact_url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]

    @field_validator("artifact_url")
    @classmethod
    def validate_artifact_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("artifact_url must be an absolute URI")
        return value


class SubmitWorkPayload(StrictContractModel):
    artifact_reference_id: UUID


class OpenReviewIterationPayload(StrictContractModel):
    submission_version_id: UUID


class MigrateReviewRequirementsPayload(StrictContractModel):
    homework_version_id: UUID
    criterion_set_id: UUID


class CreateReviewCorrectionPayload(StrictContractModel):
    published_review_revision_id: UUID
    reason: ShortText


class RecordReviewResponsibilityPayload(StrictContractModel):
    action: Literal["started", "joined", "released", "completed"]


class ReviewDecision(StrictContractModel):
    criterion_id: UUID
    points: NonNegativeNumber
    decision: Literal["accepted", "changed", "manual"]
    # Черновик сохраняется и без обоснования: интерфейс дописывает его по ходу проверки.
    reason: Annotated[str, StringConstraints(max_length=10_000)]
    evidence_ids: Annotated[list[UUID], Field(max_length=100)] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        return _require_unique(value, "evidence_ids")


class ReviewNote(StrictContractModel):
    criterion_id: UUID | None = None
    text: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]


class SaveReviewRevisionPayload(StrictContractModel):
    feedback: Annotated[str, StringConstraints(max_length=50_000)]
    criterion_decisions: Annotated[list[ReviewDecision], Field(max_length=500)]
    review_notes: Annotated[list[ReviewNote], Field(max_length=500)]


class RequestReviewPublicationPayload(StrictContractModel):
    review_revision_id: UUID
    expires_at: AwareDatetime


class PublishReviewPayload(StrictContractModel):
    review_revision_id: UUID
    publication_request_id: UUID | None = None


class RetryDeliveryPayload(StrictContractModel):
    reconcile_first: Literal[True]


class GrantAgentAuthorizationPayload(StrictContractModel):
    agent_id: UUID
    scopes: Annotated[list[AgentScope], Field(min_length=1)]
    expires_at: AwareDatetime

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: list[AgentScope]) -> list[AgentScope]:
        return _require_unique(value, "scopes")


class RecoverMethodologistPayload(StrictContractModel):
    organization_id: UUID
    provider: ShortText
    issuer: ShortText
    subject: ShortText
    reason: ShortText


type AnyCommandPayload = (
    EmptyPayload
    | ActivateBootstrapPayload
    | CreateInvitationPayload
    | ReasonPayload
    | StartCourseImportPayload
    | ChangeMembershipRolesPayload
    | CreateHomeworkPayload
    | CreateHomeworkVersionPayload
    | PublishHomeworkVersionPayload
    | SetReviewerCourseSelectionPayload
    | SetReviewerAvailabilityPayload
    | PreflightSubmissionPayload
    | SubmitWorkPayload
    | OpenReviewIterationPayload
    | MigrateReviewRequirementsPayload
    | CreateReviewCorrectionPayload
    | RecordReviewResponsibilityPayload
    | SaveReviewRevisionPayload
    | RequestReviewPublicationPayload
    | PublishReviewPayload
    | RetryDeliveryPayload
    | GrantAgentAuthorizationPayload
    | RecoverMethodologistPayload
)


COMMAND_SPECS: Mapping[CommandName, tuple[RevisionTarget, type[StrictContractModel]]] = {
    CommandName.ACTIVATE_BOOTSTRAP: (RevisionTarget.ORGANIZATION, ActivateBootstrapPayload),
    CommandName.CREATE_INVITATION: (RevisionTarget.ORGANIZATION, CreateInvitationPayload),
    CommandName.REVOKE_INVITATION: (RevisionTarget.INVITATION, ReasonPayload),
    CommandName.START_COURSE_IMPORT: (RevisionTarget.ORGANIZATION, StartCourseImportPayload),
    CommandName.ARCHIVE_COURSE: (RevisionTarget.COURSE, ReasonPayload),
    CommandName.RESTORE_COURSE: (RevisionTarget.COURSE, EmptyPayload),
    CommandName.ARCHIVE_COURSE_RUN: (RevisionTarget.COURSE_RUN, ReasonPayload),
    CommandName.RESTORE_COURSE_RUN: (RevisionTarget.COURSE_RUN, EmptyPayload),
    CommandName.CHANGE_MEMBERSHIP_ROLES: (
        RevisionTarget.MEMBERSHIP,
        ChangeMembershipRolesPayload,
    ),
    CommandName.CREATE_HOMEWORK: (RevisionTarget.COURSE_RUN, CreateHomeworkPayload),
    CommandName.CREATE_HOMEWORK_VERSION: (RevisionTarget.HOMEWORK, CreateHomeworkVersionPayload),
    CommandName.PUBLISH_HOMEWORK_VERSION: (
        RevisionTarget.HOMEWORK_VERSION,
        PublishHomeworkVersionPayload,
    ),
    CommandName.SET_REVIEWER_COURSE_SELECTION: (
        RevisionTarget.MEMBERSHIP,
        SetReviewerCourseSelectionPayload,
    ),
    CommandName.SET_REVIEWER_AVAILABILITY: (
        RevisionTarget.MEMBERSHIP,
        SetReviewerAvailabilityPayload,
    ),
    CommandName.PREFLIGHT_SUBMISSION: (
        RevisionTarget.COURSE_RUN_HOMEWORK,
        PreflightSubmissionPayload,
    ),
    CommandName.SUBMIT_WORK: (RevisionTarget.SUBMISSION, SubmitWorkPayload),
    CommandName.OPEN_REVIEW_ITERATION: (RevisionTarget.REVIEW_CASE, OpenReviewIterationPayload),
    CommandName.MIGRATE_REVIEW_REQUIREMENTS: (
        RevisionTarget.REVIEW_ITERATION,
        MigrateReviewRequirementsPayload,
    ),
    CommandName.CREATE_REVIEW_CORRECTION: (
        RevisionTarget.REVIEW_ITERATION,
        CreateReviewCorrectionPayload,
    ),
    CommandName.RECORD_REVIEW_RESPONSIBILITY: (
        RevisionTarget.REVIEW_ITERATION,
        RecordReviewResponsibilityPayload,
    ),
    CommandName.SAVE_REVIEW_REVISION: (
        RevisionTarget.REVIEW_ITERATION,
        SaveReviewRevisionPayload,
    ),
    CommandName.REQUEST_REVIEW_PUBLICATION: (
        RevisionTarget.REVIEW_ITERATION,
        RequestReviewPublicationPayload,
    ),
    CommandName.PUBLISH_REVIEW: (RevisionTarget.REVIEW_ITERATION, PublishReviewPayload),
    CommandName.START_AI_REVIEW: (RevisionTarget.REVIEW_ITERATION, EmptyPayload),
    CommandName.RETRY_DELIVERY: (RevisionTarget.EXTERNAL_DELIVERY, RetryDeliveryPayload),
    CommandName.GRANT_AGENT_AUTHORIZATION: (
        RevisionTarget.MEMBERSHIP,
        GrantAgentAuthorizationPayload,
    ),
    CommandName.REVOKE_AGENT_AUTHORIZATION: (RevisionTarget.AGENT_AUTHORIZATION, ReasonPayload),
    CommandName.RECOVER_METHODOLOGIST: (
        RevisionTarget.ORGANIZATION,
        RecoverMethodologistPayload,
    ),
}

OPERATOR_COMMANDS = frozenset(
    {CommandName.ACTIVATE_BOOTSTRAP, CommandName.RECOVER_METHODOLOGIST}
)
HUMAN_ONLY_COMMANDS = frozenset(
    {
        CommandName.PUBLISH_REVIEW,
        CommandName.GRANT_AGENT_AUTHORIZATION,
        CommandName.REVOKE_AGENT_AUTHORIZATION,
    }
)


class WireCommand(StrictContractModel):
    """Client-supplied command. Actor and organization context are forbidden."""

    __contract_schema__: ClassVar[str] = "command.schema.json"
    __contract_definition__: ClassVar[str] = "wire"
    __contract_version__: ClassVar[str] = CONTRACT_VERSION

    request_id: UUID
    idempotency_key: IdempotencyKey
    command_name: CommandName
    revision_target: RevisionTarget
    target_id: UUID
    expected_revision: NonNegativeInt
    payload: AnyCommandPayload

    @model_validator(mode="before")
    @classmethod
    def parse_exact_payload(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        command_name = value.get("command_name")
        try:
            if not isinstance(command_name, str):
                return value
            name = CommandName(command_name)
            payload_model = COMMAND_SPECS[name][1]
        except (KeyError, TypeError, ValueError):
            return value
        if "payload" not in value:
            return value
        parsed = dict(value)
        parsed["payload"] = payload_model.model_validate(value["payload"])
        return parsed

    @model_validator(mode="after")
    def exact_variant(self) -> WireCommand:
        expected_target, payload_model = COMMAND_SPECS[self.command_name]
        if self.revision_target != expected_target:
            raise ValueError(
                f"{self.command_name} requires revision_target {expected_target}, "
                f"got {self.revision_target}"
            )
        if type(self.payload) is not payload_model:
            raise ValueError(
                f"{self.command_name} requires payload {payload_model.__name__}, "
                f"got {type(self.payload).__name__}"
            )
        return self


class ApplicationCommand(WireCommand):
    """Server-enriched command with authenticated actor and transport context."""

    __contract_definition__: ClassVar[str] = "application"

    organization_id: UUID
    actor: RequestActor
    transport: Transport
    trace_id: UUID

    @model_validator(mode="after")
    def enforce_actor_transport_policy(self) -> ApplicationCommand:
        if self.command_name in OPERATOR_COMMANDS:
            if (
                not isinstance(self.actor, InstallationOperatorActor)
                or self.transport != Transport.OPERATOR
            ):
                raise ValueError(
                    "bootstrap/recovery require installation_operator over operator transport"
                )
            return self

        if isinstance(self.actor, InstallationOperatorActor):
            raise ValueError("installation_operator is allowed only for bootstrap/recovery")
        if self.command_name in HUMAN_ONLY_COMMANDS and (
            not isinstance(self.actor, UserActor) or self.transport != Transport.REST
        ):
            raise ValueError("human-only command requires user actor over rest transport")
        return self


def parse_wire_command(value: Mapping[str, Any]) -> WireCommand:
    return WireCommand.model_validate(value)


def build_application_command(
    wire: WireCommand,
    *,
    organization_id: UUID,
    actor: UserActor | AgentActor | InstallationOperatorActor,
    transport: Transport,
    trace_id: UUID,
) -> ApplicationCommand:
    return ApplicationCommand.model_validate(
        {
            **wire.model_dump(mode="python"),
            "organization_id": organization_id,
            "actor": actor,
            "transport": transport,
            "trace_id": trace_id,
        }
    )


def _require_unique[T](value: list[T], field_name: str) -> list[T]:
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} items must be unique")
    return value


if len(COMMAND_SPECS) != 28 or set(COMMAND_SPECS) != set(CommandName):
    raise RuntimeError("the frozen command registry must contain exactly all 28 variants")
