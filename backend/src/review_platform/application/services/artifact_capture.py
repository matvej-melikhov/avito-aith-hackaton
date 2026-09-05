"""Bounded immutable artifact capture with a durable promotion boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from jsonschema import ValidationError as JsonSchemaValidationError

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.ports.providers import (
    ArtifactProvider,
    JsonValue,
    ProviderContractError,
    ProviderPayload,
)
from review_platform.application.request_context import RequestActor
from review_platform.application.services.artifact_preflight import (
    ArtifactCredentialBinding,
    ArtifactProviderName,
)
from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry
from review_platform.domain.primitives import (
    canonical_json_sha256,
    require_utc,
    sanitize_error,
    utc_now,
    uuid7,
    validate_digest,
)


class ArtifactCaptureError(RuntimeError):
    """Base typed capture failure."""


class ArtifactCaptureBoundaryViolation(ArtifactCaptureError):
    """Tenant, reference, provider, credential, or result provenance mismatched."""


class ArtifactReferenceUnavailable(ArtifactCaptureError):
    """The exact tenant reference is absent or not currently readable."""


class ArtifactContentMismatch(ArtifactCaptureError):
    """Staged bytes violate the reported digest, media type, or byte budget."""


class ArtifactStagingError(ArtifactCaptureError):
    """An explicit staging adapter failed before DB promotion intent creation."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ArtifactCaptureLimits:
    max_files: int
    max_single_blob_bytes: int
    max_total_bytes: int
    max_archive_bytes: int
    max_unpacked_bytes: int

    def __post_init__(self) -> None:
        limits = {
            "max_files": (self.max_files, 50_000),
            "max_single_blob_bytes": (self.max_single_blob_bytes, 52_428_800),
            "max_total_bytes": (self.max_total_bytes, 524_288_000),
            "max_archive_bytes": (self.max_archive_bytes, 524_288_000),
            "max_unpacked_bytes": (self.max_unpacked_bytes, 1_073_741_824),
        }
        for name, (value, ceiling) in limits.items():
            if isinstance(value, bool) or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be between 1 and {ceiling}")

    @property
    def staged_byte_limit(self) -> int:
        return min(self.max_total_bytes, self.max_archive_bytes)

    def provider_payload(self) -> dict[str, int]:
        return {
            "max_files": self.max_files,
            "max_single_blob_bytes": self.max_single_blob_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_archive_bytes": self.max_archive_bytes,
            "max_unpacked_bytes": self.max_unpacked_bytes,
        }


@dataclass(frozen=True, slots=True)
class ArtifactCaptureCommand:
    organization_id: UUID
    artifact_reference_id: UUID
    operation_id: UUID
    provider: ArtifactProviderName
    credential_binding: ArtifactCredentialBinding
    limits: ArtifactCaptureLimits
    actor: RequestActor
    request_id: UUID | None = None
    trace_id: UUID | None = None
    worker_identity: str = "artifact-capture-worker"
    max_promotion_attempts: int = 5

    def __post_init__(self) -> None:
        if not self.worker_identity or len(self.worker_identity) > 255:
            raise ValueError("worker identity must contain 1..255 characters")
        if not 1 <= self.max_promotion_attempts <= 10:
            raise ValueError("promotion max attempts must be between 1 and 10")


@dataclass(frozen=True, slots=True)
class ArtifactReferenceCaptureRecord:
    organization_id: UUID
    artifact_reference_id: UUID
    provider: ArtifactProviderName
    locator: Mapping[str, str]
    read_capability: str
    revision: int


@dataclass(frozen=True, slots=True)
class ArtifactStageRequest:
    organization_id: UUID
    artifact_version_id: UUID
    operation_id: UUID
    download_url: str
    expected_byte_size: int
    expected_content_digest: str
    expected_media_type: str
    max_bytes: int
    staged_key: str
    final_key: str


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    organization_id: UUID
    artifact_version_id: UUID
    staged_key: str
    final_key: str
    byte_size: int
    content_digest: str
    media_type: str
    metadata: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ArtifactVersionCaptureRecord:
    organization_id: UUID
    artifact_version_id: UUID
    artifact_reference_id: UUID
    provider: ArtifactProviderName
    provider_version: str
    content_digest: str
    object_key: str
    media_type: str
    byte_size: int
    captured_at: datetime
    metadata: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ArtifactPromotionCaptureRecord:
    organization_id: UUID
    promotion_id: UUID
    artifact_version_id: UUID
    operation_id: UUID
    staged_key: str
    final_key: str
    state: Literal["db_committed"]
    attempts: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class ArtifactCaptureBundle:
    artifact_version: ArtifactVersionCaptureRecord
    promotion: ArtifactPromotionCaptureRecord
    operation_id: UUID
    operation_state: str
    attempt_number: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class ArtifactCaptureFailureRecord:
    organization_id: UUID
    operation_id: UUID
    operation_state: Literal["retryable_failed", "action_required"]
    attempt_number: int
    error: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ArtifactPromotionRequested:
    organization_id: UUID
    message_id: UUID
    artifact_reference_id: UUID
    artifact_version_id: UUID
    promotion_id: UUID
    operation_id: UUID
    staged_key: str
    final_key: str
    content_digest: str
    byte_size: int
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class ArtifactCaptureResult:
    organization_id: UUID
    artifact_reference_id: UUID
    artifact_version_id: UUID | None
    promotion_id: UUID | None
    operation_id: UUID
    state: str
    attempt_number: int
    replayed: bool
    error: Mapping[str, JsonValue] | None


class ArtifactCaptureRepository(Protocol):
    """Tenant SQL persistence port owned by T086."""

    async def lock_reference(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        *,
        transaction: object,
    ) -> ArtifactReferenceCaptureRecord | None: ...

    async def find_by_reference_digest(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        content_digest: str,
        *,
        transaction: object,
    ) -> ArtifactCaptureBundle | None: ...

    async def record_success(
        self,
        *,
        command: ArtifactCaptureCommand,
        input_version: str,
        artifact_version: ArtifactVersionCaptureRecord,
        promotion: ArtifactPromotionCaptureRecord,
        attempt_metadata: Mapping[str, JsonValue],
        now: datetime,
        transaction: object,
    ) -> ArtifactCaptureBundle: ...

    async def record_failure(
        self,
        *,
        command: ArtifactCaptureCommand,
        input_version: str,
        state: Literal["retryable_failed", "action_required"],
        error: Mapping[str, JsonValue],
        attempt_metadata: Mapping[str, JsonValue],
        now: datetime,
        transaction: object,
    ) -> ArtifactCaptureFailureRecord: ...


class ArtifactStagingPort(Protocol):
    """Fetch and stage a bounded provider URL outside application core."""

    async def stage(self, request: ArtifactStageRequest) -> StagedArtifact: ...


class ArtifactCaptureCredentialPort(Protocol):
    async def require_exact_active(
        self,
        organization_id: UUID,
        binding: ArtifactCredentialBinding,
        *,
        transaction: object,
    ) -> None: ...


class ArtifactCaptureAuthorizationPort(Protocol):
    async def authorize(
        self,
        *,
        actor: RequestActor,
        organization_id: UUID,
        artifact_reference_id: UUID,
        transaction: object,
    ) -> object: ...

    async def revalidate_for_commit(
        self,
        grant: object,
        *,
        transaction: object,
    ) -> None: ...


class ArtifactPromotionOutbox(Protocol):
    async def append(
        self,
        event: ArtifactPromotionRequested,
        *,
        transaction: object,
    ) -> None: ...


class ArtifactCaptureService:
    """Capture verified bytes and persist only a durable promotion intent."""

    def __init__(
        self,
        *,
        repository: ArtifactCaptureRepository,
        staging: ArtifactStagingPort,
        credential_bindings: ArtifactCaptureCredentialPort,
        authorization: ArtifactCaptureAuthorizationPort,
        outbox: ArtifactPromotionOutbox,
        provider: ArtifactProvider,
        audit: AuditRecorder,
        registry: ContractRegistry | None = None,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._staging = staging
        self._credential_bindings = credential_bindings
        self._authorization = authorization
        self._outbox = outbox
        self._provider = provider
        self._audit = audit
        self._registry = registry or ContractRegistry()
        self._id_factory = id_factory
        self._clock = clock

    async def capture(
        self,
        command: ArtifactCaptureCommand,
        *,
        transaction: object,
    ) -> ArtifactCaptureResult:
        self._validate_command(command)
        grant = await self._authorization.authorize(
            actor=command.actor,
            organization_id=command.organization_id,
            artifact_reference_id=command.artifact_reference_id,
            transaction=transaction,
        )
        reference = await self._repository.lock_reference(
            command.organization_id,
            command.artifact_reference_id,
            transaction=transaction,
        )
        self._validate_reference(command, reference)
        reference_record = cast(ArtifactReferenceCaptureRecord, reference)
        await self._credential_bindings.require_exact_active(
            command.organization_id,
            command.credential_binding,
            transaction=transaction,
        )
        input_version = self._input_version(
            command,
            reference_record,
        )
        provider_result = await self._provider_capture(
            command,
            reference_record,
        )
        if provider_result["outcome"] == "failed":
            failure = await self._record_provider_failure(
                command,
                provider_result,
                input_version=input_version,
                transaction=transaction,
            )
            return await self._complete(
                command,
                _failure_result(command, failure),
                grant=grant,
                reference_revision=reference_record.revision,
                transaction=transaction,
            )

        provider_version, download_url, media_type, reported_size, reported_digest = (
            self._success_content(provider_result)
        )
        if reported_size > command.limits.staged_byte_limit:
            raise ArtifactContentMismatch("provider reported content exceeds capture byte limit")
        replay = await self._repository.find_by_reference_digest(
            command.organization_id,
            command.artifact_reference_id,
            reported_digest,
            transaction=transaction,
        )
        if replay is not None:
            self._validate_bundle(command, replay, expected_digest=reported_digest)
            return await self._complete(
                command,
                _bundle_result(replay, replayed=True),
                grant=grant,
                reference_revision=reference_record.revision,
                transaction=transaction,
            )

        now = require_utc(self._clock())
        artifact_version_id = self._id_factory()
        promotion_id = self._id_factory()
        prefix = f"{command.organization_id}/{artifact_version_id}"
        staged_key = f"{prefix}/staged/{command.operation_id}"
        final_key = f"{prefix}/artifact.bin"
        try:
            staged = await self._staging.stage(
                ArtifactStageRequest(
                    organization_id=command.organization_id,
                    artifact_version_id=artifact_version_id,
                    operation_id=command.operation_id,
                    download_url=download_url,
                    expected_byte_size=reported_size,
                    expected_content_digest=reported_digest,
                    expected_media_type=media_type,
                    max_bytes=command.limits.staged_byte_limit,
                    staged_key=staged_key,
                    final_key=final_key,
                )
            )
        except ArtifactStagingError as error:
            failure = await self._record_staging_failure(
                command,
                error,
                input_version=input_version,
                transaction=transaction,
            )
            return await self._complete(
                command,
                _failure_result(command, failure),
                grant=grant,
                reference_revision=reference_record.revision,
                transaction=transaction,
            )
        self._validate_staged(
            command,
            staged,
            artifact_version_id=artifact_version_id,
            expected_size=reported_size,
            expected_digest=reported_digest,
            expected_media_type=media_type,
            expected_staged_key=staged_key,
            expected_final_key=final_key,
        )
        safe_metadata: dict[str, JsonValue] = {
            "provider": command.provider,
            "provider_version": provider_version,
            "credential_binding_id": str(command.credential_binding.credential_binding_id),
            "credential_binding_version": command.credential_binding.credential_binding_version,
            "staging": dict(staged.metadata),
        }
        version = ArtifactVersionCaptureRecord(
            organization_id=command.organization_id,
            artifact_version_id=artifact_version_id,
            artifact_reference_id=command.artifact_reference_id,
            provider=command.provider,
            provider_version=provider_version,
            content_digest=reported_digest,
            object_key=final_key,
            media_type=media_type,
            byte_size=reported_size,
            captured_at=now,
            metadata=safe_metadata,
        )
        promotion = ArtifactPromotionCaptureRecord(
            organization_id=command.organization_id,
            promotion_id=promotion_id,
            artifact_version_id=artifact_version_id,
            operation_id=command.operation_id,
            staged_key=staged_key,
            final_key=final_key,
            state="db_committed",
            attempts=0,
            max_attempts=command.max_promotion_attempts,
        )
        bundle = await self._repository.record_success(
            command=command,
            input_version=input_version,
            artifact_version=version,
            promotion=promotion,
            attempt_metadata=safe_metadata,
            now=now,
            transaction=transaction,
        )
        self._validate_bundle(command, bundle, expected_digest=reported_digest)
        if not bundle.replayed:
            await self._outbox.append(
                ArtifactPromotionRequested(
                    organization_id=command.organization_id,
                    message_id=self._id_factory(),
                    artifact_reference_id=command.artifact_reference_id,
                    artifact_version_id=bundle.artifact_version.artifact_version_id,
                    promotion_id=bundle.promotion.promotion_id,
                    operation_id=bundle.operation_id,
                    staged_key=bundle.promotion.staged_key,
                    final_key=bundle.promotion.final_key,
                    content_digest=bundle.artifact_version.content_digest,
                    byte_size=bundle.artifact_version.byte_size,
                ),
                transaction=transaction,
            )
        return await self._complete(
            command,
            _bundle_result(bundle, replayed=bundle.replayed),
            grant=grant,
            reference_revision=reference_record.revision,
            transaction=transaction,
        )

    async def record_audit(
        self,
        command: ArtifactCaptureCommand,
        result: ArtifactCaptureResult,
        *,
        reference_revision: int | None,
        transaction: object,
    ) -> None:
        await self._audit.record(
            AuditEventDraft(
                organization_id=command.organization_id,
                actor=command.actor,
                action="capture_artifact",
                entity_type="artifact_reference",
                entity_id=command.artifact_reference_id,
                before_revision=reference_revision,
                after_revision=reference_revision,
                request_id=command.request_id or command.operation_id,
                trace_id=command.trace_id or command.operation_id,
                outcome=result.state,
                details={
                    "operation_id": str(result.operation_id),
                    "artifact_version_id": (
                        str(result.artifact_version_id)
                        if result.artifact_version_id is not None
                        else None
                    ),
                    "attempt_number": result.attempt_number,
                    "replayed": result.replayed,
                },
            ),
            transaction=transaction,
        )

    async def _complete(
        self,
        command: ArtifactCaptureCommand,
        result: ArtifactCaptureResult,
        *,
        grant: object,
        reference_revision: int,
        transaction: object,
    ) -> ArtifactCaptureResult:
        await self.record_audit(
            command,
            result,
            reference_revision=reference_revision,
            transaction=transaction,
        )
        await self._authorization.revalidate_for_commit(grant, transaction=transaction)
        return result

    def _validate_command(self, command: ArtifactCaptureCommand) -> None:
        if command.actor.organization_id != command.organization_id:
            raise ArtifactCaptureBoundaryViolation("capture actor tenant mismatched")
        if command.credential_binding.provider != command.provider:
            raise ArtifactCaptureBoundaryViolation("capture credential provider mismatched")
        if (
            self._provider.contract_version != CONTRACT_VERSION
            or self._provider.schema_name != "artifact-provider.schema.json"
        ):
            raise ArtifactCaptureBoundaryViolation("artifact provider contract metadata mismatched")

    @staticmethod
    def _validate_reference(
        command: ArtifactCaptureCommand,
        reference: ArtifactReferenceCaptureRecord | None,
    ) -> None:
        if reference is None:
            raise ArtifactReferenceUnavailable("tenant-scoped ArtifactReference was not found")
        if (
            reference.organization_id != command.organization_id
            or reference.artifact_reference_id != command.artifact_reference_id
            or reference.provider != command.provider
        ):
            raise ArtifactCaptureBoundaryViolation("ArtifactReference provenance mismatched")
        if reference.read_capability != "available" or not reference.locator:
            raise ArtifactReferenceUnavailable("ArtifactReference is not currently usable")

    async def _provider_capture(
        self,
        command: ArtifactCaptureCommand,
        reference: ArtifactReferenceCaptureRecord,
    ) -> ProviderPayload:
        request: ProviderPayload = {
            "contract_version": CONTRACT_VERSION,
            "organization_id": str(command.organization_id),
            "artifact_reference_id": str(command.artifact_reference_id),
            "provider": command.provider,
            "locator": dict(reference.locator),
            "credential_binding_id": str(command.credential_binding.credential_binding_id),
            "credential_binding_version": command.credential_binding.credential_binding_version,
            "limits": cast(JsonValue, command.limits.provider_payload()),
        }
        try:
            self._registry.validate(
                request,
                "artifact-provider.schema.json",
                definition="capture_request",
            )
            result = await self._provider.capture(request)
            self._registry.validate(
                result,
                "artifact-provider.schema.json",
                definition="capture_result",
            )
        except (JsonSchemaValidationError, ProviderContractError) as error:
            raise ArtifactCaptureBoundaryViolation(
                "artifact capture request/result violates frozen schema"
            ) from error
        if result.get("organization_id") != str(command.organization_id) or result.get(
            "artifact_reference_id"
        ) != str(command.artifact_reference_id):
            raise ArtifactCaptureBoundaryViolation("artifact provider result provenance mismatched")
        return result

    @staticmethod
    def _success_content(
        result: ProviderPayload,
    ) -> tuple[str, str, str, int, str]:
        content = result.get("content")
        if not isinstance(content, Mapping):
            raise ArtifactCaptureBoundaryViolation("successful capture omitted content")
        provider_version = _string(result, "provider_version")
        download_url = _string(content, "download_url")
        media_type = _string(content, "media_type")
        size = content.get("byte_size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ArtifactCaptureBoundaryViolation("provider byte_size is invalid")
        digest = validate_digest(_string(content, "content_digest"))
        return provider_version, download_url, media_type, size, digest

    async def _record_provider_failure(
        self,
        command: ArtifactCaptureCommand,
        result: ProviderPayload,
        *,
        input_version: str,
        transaction: object,
    ) -> ArtifactCaptureFailureRecord:
        error = result.get("error")
        if not isinstance(error, Mapping):
            raise ArtifactCaptureBoundaryViolation("failed capture omitted typed error")
        retryable = error.get("retryable") is True
        bounded_values = sanitize_error(error, max_bytes=1900)
        bounded_values["retryable"] = retryable
        bounded = cast(Mapping[str, JsonValue], bounded_values)
        return await self._repository.record_failure(
            command=command,
            input_version=input_version,
            state="retryable_failed" if retryable else "action_required",
            error=bounded,
            attempt_metadata=self._attempt_metadata(command, result),
            now=require_utc(self._clock()),
            transaction=transaction,
        )

    async def _record_staging_failure(
        self,
        command: ArtifactCaptureCommand,
        error: ArtifactStagingError,
        *,
        input_version: str,
        transaction: object,
    ) -> ArtifactCaptureFailureRecord:
        bounded_values = sanitize_error(
            {
                "code": "artifact_staging_failed",
                "message": str(error),
                "retryable": error.retryable,
                "action": "retry" if error.retryable else "inspect_artifact",
            },
            max_bytes=1900,
        )
        bounded_values["retryable"] = error.retryable
        bounded = cast(Mapping[str, JsonValue], bounded_values)
        return await self._repository.record_failure(
            command=command,
            input_version=input_version,
            state="retryable_failed" if error.retryable else "action_required",
            error=bounded,
            attempt_metadata=self._attempt_metadata(command, None),
            now=require_utc(self._clock()),
            transaction=transaction,
        )

    @staticmethod
    def _validate_staged(
        command: ArtifactCaptureCommand,
        staged: StagedArtifact,
        *,
        artifact_version_id: UUID,
        expected_size: int,
        expected_digest: str,
        expected_media_type: str,
        expected_staged_key: str,
        expected_final_key: str,
    ) -> None:
        if (
            staged.organization_id != command.organization_id
            or staged.artifact_version_id != artifact_version_id
            or staged.staged_key != expected_staged_key
            or staged.final_key != expected_final_key
        ):
            raise ArtifactCaptureBoundaryViolation("staging adapter returned different identity")
        if staged.byte_size != expected_size or staged.byte_size > command.limits.staged_byte_limit:
            raise ArtifactContentMismatch("staged byte size differs from provider result")
        if validate_digest(staged.content_digest) != expected_digest:
            raise ArtifactContentMismatch("staged digest differs from provider result")
        if staged.media_type != expected_media_type:
            raise ArtifactContentMismatch("staged media type differs from provider result")

    @staticmethod
    def _validate_bundle(
        command: ArtifactCaptureCommand,
        bundle: ArtifactCaptureBundle,
        *,
        expected_digest: str,
    ) -> None:
        version = bundle.artifact_version
        promotion = bundle.promotion
        prefix = f"{command.organization_id}/{version.artifact_version_id}/"
        if (
            version.organization_id != command.organization_id
            or version.artifact_reference_id != command.artifact_reference_id
            or version.provider != command.provider
            or version.content_digest != expected_digest
            or promotion.organization_id != command.organization_id
            or promotion.artifact_version_id != version.artifact_version_id
            or bundle.operation_id != promotion.operation_id
            or not version.object_key.startswith(prefix)
            or not promotion.staged_key.startswith(prefix)
            or promotion.final_key != version.object_key
        ):
            raise ArtifactCaptureBoundaryViolation("repository returned a different capture bundle")

    @staticmethod
    def _attempt_metadata(
        command: ArtifactCaptureCommand,
        result: ProviderPayload | None,
    ) -> dict[str, JsonValue]:
        return {
            "provider": command.provider,
            "credential_binding_id": str(command.credential_binding.credential_binding_id),
            "credential_binding_version": command.credential_binding.credential_binding_version,
            "provider_version": (result.get("provider_version") if result is not None else None),
        }

    @staticmethod
    def _input_version(
        command: ArtifactCaptureCommand,
        reference: ArtifactReferenceCaptureRecord,
    ) -> str:
        digest = canonical_json_sha256(
            {
                "contract_version": CONTRACT_VERSION,
                "organization_id": str(command.organization_id),
                "artifact_reference_id": str(command.artifact_reference_id),
                "reference_revision": reference.revision,
                "provider": command.provider,
                "locator": dict(reference.locator),
                "credential_binding_id": str(command.credential_binding.credential_binding_id),
                "credential_binding_version": (
                    command.credential_binding.credential_binding_version
                ),
                "limits": command.limits.provider_payload(),
            }
        )
        return f"artifact-capture:{CONTRACT_VERSION}:{digest}"


def _string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ArtifactCaptureBoundaryViolation(f"artifact capture {field} is invalid")
    return value


def _bundle_result(bundle: ArtifactCaptureBundle, *, replayed: bool) -> ArtifactCaptureResult:
    return ArtifactCaptureResult(
        organization_id=bundle.artifact_version.organization_id,
        artifact_reference_id=bundle.artifact_version.artifact_reference_id,
        artifact_version_id=bundle.artifact_version.artifact_version_id,
        promotion_id=bundle.promotion.promotion_id,
        operation_id=bundle.operation_id,
        state=bundle.promotion.state,
        attempt_number=bundle.attempt_number,
        replayed=replayed,
        error=None,
    )


def _failure_result(
    command: ArtifactCaptureCommand,
    failure: ArtifactCaptureFailureRecord,
) -> ArtifactCaptureResult:
    if (
        failure.organization_id != command.organization_id
        or failure.operation_id != command.operation_id
    ):
        raise ArtifactCaptureBoundaryViolation("failure record provenance mismatched")
    return ArtifactCaptureResult(
        organization_id=command.organization_id,
        artifact_reference_id=command.artifact_reference_id,
        artifact_version_id=None,
        promotion_id=None,
        operation_id=command.operation_id,
        state=failure.operation_state,
        attempt_number=failure.attempt_number,
        replayed=False,
        error=failure.error,
    )


__all__ = [
    "ArtifactCaptureAuthorizationPort",
    "ArtifactCaptureBoundaryViolation",
    "ArtifactCaptureBundle",
    "ArtifactCaptureCommand",
    "ArtifactCaptureCredentialPort",
    "ArtifactCaptureError",
    "ArtifactCaptureFailureRecord",
    "ArtifactCaptureLimits",
    "ArtifactCaptureRepository",
    "ArtifactCaptureResult",
    "ArtifactCaptureService",
    "ArtifactContentMismatch",
    "ArtifactPromotionCaptureRecord",
    "ArtifactPromotionOutbox",
    "ArtifactPromotionRequested",
    "ArtifactReferenceCaptureRecord",
    "ArtifactReferenceUnavailable",
    "ArtifactStageRequest",
    "ArtifactStagingError",
    "ArtifactStagingPort",
    "ArtifactVersionCaptureRecord",
    "StagedArtifact",
]
