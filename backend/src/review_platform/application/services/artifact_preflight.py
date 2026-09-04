"""Idempotent CourseRunHomework-addressed artifact preflight orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast
from urllib.parse import urlparse
from uuid import UUID

from jsonschema import ValidationError as JsonSchemaValidationError

from review_platform.application.authorization import AuthorizationPolicy, Authorizer
from review_platform.application.ports.providers import ArtifactProvider, ProviderPayload
from review_platform.application.request_context import RequestActor
from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry
from review_platform.domain.primitives import require_utc, sanitize_error, utc_now, uuid7

type ArtifactProviderName = Literal["github", "google_docs"]
type ReadCapability = Literal["available", "requires_action", "unavailable"]
type FeedbackCapability = Literal["available", "not_supported", "requires_action"]

_STUDENT_POLICY = AuthorizationPolicy(required_roles=frozenset({"student"}))


class ArtifactPreflightError(RuntimeError):
    """Base typed preflight failure."""


class PreflightTargetNotFound(ArtifactPreflightError):
    """The tenant-scoped CourseRunHomework or expected revision is absent."""


class StudentNotEnrolled(ArtifactPreflightError):
    """The represented user is not enrolled in this CourseRun."""


class HomeworkNotPublished(ArtifactPreflightError):
    """The CourseRunHomework has no current requirements publication."""


class ArtifactKindNotAllowed(ArtifactPreflightError):
    """The URL provider is unsupported or disallowed by current requirements."""


class ArchivedPreflightDenied(ArtifactPreflightError):
    """The Course or CourseRun no longer accepts new submissions."""


class InvalidArtifactCredentialBinding(ArtifactPreflightError):
    """The explicitly supplied credential generation is absent or mismatched."""


class ArtifactProviderContractViolation(ArtifactPreflightError):
    """The adapter request/result violates the frozen provider contract."""


@dataclass(frozen=True, slots=True)
class ArtifactCredentialBinding:
    credential_binding_id: UUID
    credential_binding_version: int
    provider: ArtifactProviderName

    def __post_init__(self) -> None:
        if self.credential_binding_version < 1:
            raise InvalidArtifactCredentialBinding(
                "credential binding version must be positive"
            )


@dataclass(frozen=True, slots=True)
class CourseRunHomeworkPreflightContext:
    organization_id: UUID
    course_run_homework_id: UUID
    course_run_id: UUID
    homework_id: UUID
    current_publication_id: UUID | None
    current_homework_version_id: UUID | None
    allowed_artifact_kinds: tuple[ArtifactProviderName, ...]
    revision: int
    status: str
    student_enrolled: bool


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    organization_id: UUID
    submission_id: UUID
    course_run_homework_id: UUID
    course_run_id: UUID
    homework_id: UUID
    student_id: UUID
    current_revision: int


@dataclass(frozen=True, slots=True)
class ArtifactReferenceRecord:
    organization_id: UUID
    artifact_reference_id: UUID
    provider: ArtifactProviderName
    original_url: str
    locator: Mapping[str, str]
    read_capability: ReadCapability
    feedback_capability: FeedbackCapability
    last_checked_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class ArtifactCapabilityResult:
    provider: ArtifactProviderName
    read_capability: ReadCapability
    feedback_capability: FeedbackCapability
    submission_id: UUID
    submission_revision: int
    artifact_reference_id: UUID | None
    error: Mapping[str, object] | None


class ArtifactPreflightRepository(Protocol):
    """Tenant-scoped SQL port implemented by T086."""

    async def lock_context(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        student_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> CourseRunHomeworkPreflightContext | None: ...

    async def get_or_create_submission(
        self,
        context: CourseRunHomeworkPreflightContext,
        *,
        student_id: UUID,
        new_submission_id: UUID,
        transaction: object,
    ) -> SubmissionRecord: ...

    async def upsert_available_reference(
        self,
        candidate: ArtifactReferenceRecord,
        *,
        transaction: object,
    ) -> ArtifactReferenceRecord: ...


class ArtifactCredentialBindingPort(Protocol):
    """Verify one explicit credential ID/version without selecting credentials."""

    async def require_exact_active(
        self,
        organization_id: UUID,
        binding: ArtifactCredentialBinding,
        *,
        transaction: object,
    ) -> None: ...


class ArtifactPreflightArchiveGuard(Protocol):
    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        transaction: object,
    ) -> None: ...


class ArtifactPreflightService:
    """Authorize, validate provider capability, and persist opaque identities."""

    def __init__(
        self,
        *,
        repository: ArtifactPreflightRepository,
        credential_bindings: ArtifactCredentialBindingPort,
        provider: ArtifactProvider,
        archived_guard: ArtifactPreflightArchiveGuard,
        authorizer: Authorizer,
        registry: ContractRegistry | None = None,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._credential_bindings = credential_bindings
        self._provider = provider
        self._archived_guard = archived_guard
        self._authorizer = authorizer
        self._registry = registry or ContractRegistry()
        self._id_factory = id_factory
        self._clock = clock

    async def preflight(
        self,
        *,
        transaction: object,
        organization_id: UUID,
        course_run_homework_id: UUID,
        expected_revision: int,
        artifact_url: str,
        credential_binding: ArtifactCredentialBinding,
        actor: RequestActor,
    ) -> ArtifactCapabilityResult:
        if actor.user_id is None:
            raise StudentNotEnrolled("preflight requires represented student identity")
        grant = await self._authorizer.authorize(
            actor=actor,
            organization_id=organization_id,
            policy=_STUDENT_POLICY,
        )
        provider_name = provider_for_artifact_url(artifact_url)
        if credential_binding.provider != provider_name:
            raise InvalidArtifactCredentialBinding(
                "credential provider does not match artifact URL provider"
            )
        context = await self._repository.lock_context(
            organization_id,
            course_run_homework_id,
            actor.user_id,
            expected_revision=expected_revision,
            transaction=transaction,
        )
        if context is None:
            raise PreflightTargetNotFound(
                "CourseRunHomework is missing or its revision is stale"
            )
        self._validate_context(
            context,
            organization_id=organization_id,
            course_run_homework_id=course_run_homework_id,
            provider=provider_name,
        )
        await self._archived_guard.require_active(
            organization_id=organization_id,
            course_run_id=context.course_run_id,
            transaction=transaction,
        )
        await self._credential_bindings.require_exact_active(
            organization_id,
            credential_binding,
            transaction=transaction,
        )
        submission = await self._repository.get_or_create_submission(
            context,
            student_id=actor.user_id,
            new_submission_id=self._id_factory(),
            transaction=transaction,
        )
        self._validate_submission(submission, context=context, student_id=actor.user_id)
        provider_result = await self._provider_preflight(
            organization_id=organization_id,
            artifact_url=artifact_url,
            provider=provider_name,
            binding=credential_binding,
        )
        read_capability = cast(ReadCapability, provider_result["read_capability"])
        feedback_capability = cast(
            FeedbackCapability,
            provider_result["feedback_capability"],
        )
        if read_capability == "available":
            locator = provider_result.get("locator")
            if not isinstance(locator, Mapping):
                raise ArtifactProviderContractViolation(
                    "available provider result omitted locator"
                )
            reference = await self._repository.upsert_available_reference(
                ArtifactReferenceRecord(
                    organization_id=organization_id,
                    artifact_reference_id=self._id_factory(),
                    provider=provider_name,
                    original_url=artifact_url,
                    locator=_string_mapping(locator),
                    read_capability=read_capability,
                    feedback_capability=feedback_capability,
                    last_checked_at=require_utc(self._clock()),
                    revision=0,
                ),
                transaction=transaction,
            )
            self._validate_reference(
                reference,
                organization_id=organization_id,
                provider=provider_name,
                artifact_url=artifact_url,
            )
            error: Mapping[str, object] | None = None
            reference_id: UUID | None = reference.artifact_reference_id
        else:
            provider_error = provider_result.get("error")
            if not isinstance(provider_error, Mapping):
                raise ArtifactProviderContractViolation(
                    "unavailable provider result omitted typed error"
                )
            error = cast(Mapping[str, object], sanitize_error(provider_error))
            reference_id = None

        await self._authorizer.revalidate_for_commit(grant, transaction=transaction)
        return ArtifactCapabilityResult(
            provider=provider_name,
            read_capability=read_capability,
            feedback_capability=feedback_capability,
            submission_id=submission.submission_id,
            submission_revision=submission.current_revision,
            artifact_reference_id=reference_id,
            error=error,
        )

    async def _provider_preflight(
        self,
        *,
        organization_id: UUID,
        artifact_url: str,
        provider: ArtifactProviderName,
        binding: ArtifactCredentialBinding,
    ) -> ProviderPayload:
        if (
            self._provider.contract_version != CONTRACT_VERSION
            or self._provider.schema_name != "artifact-provider.schema.json"
        ):
            raise ArtifactProviderContractViolation(
                "artifact provider uses unsupported contract metadata"
            )
        request: ProviderPayload = {
            "contract_version": CONTRACT_VERSION,
            "organization_id": str(organization_id),
            "provider": provider,
            "url": artifact_url,
            "credential_binding_id": str(binding.credential_binding_id),
            "credential_binding_version": binding.credential_binding_version,
        }
        try:
            self._registry.validate(
                request,
                "artifact-provider.schema.json",
                definition="preflight_request",
            )
            result = await self._provider.preflight(request)
            self._registry.validate(
                result,
                "artifact-provider.schema.json",
                definition="preflight_result",
            )
        except JsonSchemaValidationError as error:
            raise ArtifactProviderContractViolation(
                "artifact provider request or result violates frozen schema"
            ) from error
        if result.get("organization_id") != str(organization_id):
            raise ArtifactProviderContractViolation(
                "artifact provider returned a different organization"
            )
        if result.get("provider") != provider:
            raise ArtifactProviderContractViolation(
                "artifact provider returned a different provider kind"
            )
        return result

    @staticmethod
    def _validate_context(
        context: CourseRunHomeworkPreflightContext,
        *,
        organization_id: UUID,
        course_run_homework_id: UUID,
        provider: ArtifactProviderName,
    ) -> None:
        if (
            context.organization_id != organization_id
            or context.course_run_homework_id != course_run_homework_id
        ):
            raise PreflightTargetNotFound("repository returned a different tenant target")
        if not context.student_enrolled:
            raise StudentNotEnrolled("student is not enrolled in this CourseRun")
        if context.status != "active":
            raise ArchivedPreflightDenied("CourseRunHomework is not active")
        if (
            context.current_publication_id is None
            or context.current_homework_version_id is None
        ):
            raise HomeworkNotPublished("CourseRunHomework has no current publication")
        if provider not in context.allowed_artifact_kinds:
            raise ArtifactKindNotAllowed(
                "artifact provider is not allowed by current homework requirements"
            )

    @staticmethod
    def _validate_submission(
        submission: SubmissionRecord,
        *,
        context: CourseRunHomeworkPreflightContext,
        student_id: UUID,
    ) -> None:
        if (
            submission.organization_id != context.organization_id
            or submission.course_run_homework_id != context.course_run_homework_id
            or submission.course_run_id != context.course_run_id
            or submission.homework_id != context.homework_id
            or submission.student_id != student_id
            or submission.current_revision < 0
        ):
            raise ArtifactPreflightError(
                "repository returned a submission outside the exact tenant flow"
            )

    @staticmethod
    def _validate_reference(
        reference: ArtifactReferenceRecord,
        *,
        organization_id: UUID,
        provider: ArtifactProviderName,
        artifact_url: str,
    ) -> None:
        if (
            reference.organization_id != organization_id
            or reference.provider != provider
            or reference.original_url != artifact_url
            or reference.read_capability != "available"
        ):
            raise ArtifactProviderContractViolation(
                "repository returned an artifact reference with different provenance"
            )


def provider_for_artifact_url(artifact_url: str) -> ArtifactProviderName:
    parsed = urlparse(artifact_url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme in {"http", "https"} and host in {"github.com", "www.github.com"}:
        return "github"
    if (
        parsed.scheme in {"http", "https"}
        and host == "docs.google.com"
        and parsed.path.startswith("/document/")
    ):
        return "google_docs"
    raise ArtifactKindNotAllowed("artifact URL is not a supported GitHub or Google Docs URL")


def _string_mapping(value: Mapping[str, object]) -> Mapping[str, str]:
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ArtifactProviderContractViolation("artifact locator must contain string fields")
    return cast(Mapping[str, str], dict(value))


__all__ = [
    "ArchivedPreflightDenied",
    "ArtifactCapabilityResult",
    "ArtifactCredentialBinding",
    "ArtifactCredentialBindingPort",
    "ArtifactKindNotAllowed",
    "ArtifactPreflightArchiveGuard",
    "ArtifactPreflightError",
    "ArtifactPreflightRepository",
    "ArtifactPreflightService",
    "ArtifactProviderContractViolation",
    "ArtifactReferenceRecord",
    "CourseRunHomeworkPreflightContext",
    "FeedbackCapability",
    "HomeworkNotPublished",
    "InvalidArtifactCredentialBinding",
    "PreflightTargetNotFound",
    "ReadCapability",
    "StudentNotEnrolled",
    "SubmissionRecord",
    "provider_for_artifact_url",
]
