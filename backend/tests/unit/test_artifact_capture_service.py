from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.request_context import RequestActor
from review_platform.application.services.artifact_capture import (
    ArtifactCaptureBoundaryViolation,
    ArtifactCaptureBundle,
    ArtifactCaptureCommand,
    ArtifactCaptureFailureRecord,
    ArtifactCaptureLimits,
    ArtifactCaptureService,
    ArtifactContentMismatch,
    ArtifactPromotionCaptureRecord,
    ArtifactPromotionRequested,
    ArtifactReferenceCaptureRecord,
    ArtifactReferenceUnavailable,
    ArtifactStageRequest,
    ArtifactStagingError,
    ArtifactVersionCaptureRecord,
    StagedArtifact,
)
from review_platform.application.services.artifact_preflight import (
    ArtifactCredentialBinding,
    ArtifactProviderName,
)
from review_platform.domain.primitives import sha256_digest

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000003001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000003002")
USER = UUID("00000000-0000-7000-8000-000000003003")
REFERENCE = UUID("00000000-0000-7000-8000-000000003004")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000003005")
OPERATION = UUID("00000000-0000-7000-8000-000000003006")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000003007")
PROMOTION = UUID("00000000-0000-7000-8000-000000003008")
MESSAGE = UUID("00000000-0000-7000-8000-000000003009")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
CONTENT = b"artifact"
DIGEST = sha256_digest(CONTENT)
OTHER_DIGEST = "sha256:" + "b" * 64
TRANSACTION = object()


class Provider:
    contract_version = "1.1.0"
    schema_name = "artifact-provider.schema.json"

    def __init__(self, result: ProviderPayload) -> None:
        self.result = result
        self.requests: list[ProviderPayload] = []

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        raise AssertionError("capture service must not repeat preflight")

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        self.requests.append(request)
        return self.result


class Repository:
    def __init__(self) -> None:
        self.reference = ArtifactReferenceCaptureRecord(
            organization_id=ORG,
            artifact_reference_id=REFERENCE,
            provider="github",
            locator={
                "canonical_url": "https://github.com/example/repository",
                "external_id": "example/repository",
            },
            read_capability="available",
            revision=2,
        )
        self.replay: ArtifactCaptureBundle | None = None
        self.successes: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []

    async def lock_reference(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        *,
        transaction: object,
    ) -> ArtifactReferenceCaptureRecord | None:
        assert transaction is TRANSACTION
        if (
            organization_id != self.reference.organization_id
            or artifact_reference_id != self.reference.artifact_reference_id
        ):
            return None
        return self.reference

    async def find_by_reference_digest(
        self,
        organization_id: UUID,
        artifact_reference_id: UUID,
        content_digest: str,
        *,
        transaction: object,
    ) -> ArtifactCaptureBundle | None:
        assert transaction is TRANSACTION
        if (
            organization_id == ORG
            and artifact_reference_id == REFERENCE
            and content_digest == DIGEST
        ):
            return self.replay
        return None

    async def record_success(self, **values: object) -> ArtifactCaptureBundle:
        self.successes.append(values)
        version = values["artifact_version"]
        promotion = values["promotion"]
        command = values["command"]
        assert isinstance(version, ArtifactVersionCaptureRecord)
        assert isinstance(promotion, ArtifactPromotionCaptureRecord)
        assert isinstance(command, ArtifactCaptureCommand)
        assert values["transaction"] is TRANSACTION
        bundle = ArtifactCaptureBundle(
            artifact_version=version,
            promotion=promotion,
            operation_id=command.operation_id,
            operation_state="processing",
            attempt_number=1,
            replayed=False,
        )
        self.replay = bundle
        return bundle

    async def record_failure(self, **values: object) -> ArtifactCaptureFailureRecord:
        self.failures.append(values)
        command = values["command"]
        state = values["state"]
        error = values["error"]
        assert isinstance(command, ArtifactCaptureCommand)
        assert state in {"retryable_failed", "action_required"}
        assert isinstance(error, Mapping)
        assert values["transaction"] is TRANSACTION
        return ArtifactCaptureFailureRecord(
            organization_id=command.organization_id,
            operation_id=command.operation_id,
            operation_state=state,
            attempt_number=1,
            error=error,
        )


class Staging:
    def __init__(self) -> None:
        self.requests: list[ArtifactStageRequest] = []
        self.digest = DIGEST
        self.size = len(CONTENT)
        self.media_type = "application/zip"
        self.error: ArtifactStagingError | None = None

    async def stage(self, request: ArtifactStageRequest) -> StagedArtifact:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return StagedArtifact(
            organization_id=request.organization_id,
            artifact_version_id=request.artifact_version_id,
            staged_key=request.staged_key,
            final_key=request.final_key,
            byte_size=self.size,
            content_digest=self.digest,
            media_type=self.media_type,
            metadata={"source": "bounded-test-staging"},
        )


class Credentials:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, ArtifactCredentialBinding, object]] = []
        self.reject = False

    async def require_exact_active(
        self,
        organization_id: UUID,
        binding: ArtifactCredentialBinding,
        *,
        transaction: object,
    ) -> None:
        self.calls.append((organization_id, binding, transaction))
        if self.reject:
            raise ArtifactCaptureBoundaryViolation("credential binding rejected")


class Authorization:
    def __init__(self) -> None:
        self.authorized: list[tuple[RequestActor, UUID, UUID, object]] = []
        self.final: list[tuple[object, object]] = []

    async def authorize(
        self,
        *,
        actor: RequestActor,
        organization_id: UUID,
        artifact_reference_id: UUID,
        transaction: object,
    ) -> object:
        if actor.organization_id != organization_id or "student" not in actor.roles:
            raise ArtifactCaptureBoundaryViolation("capture authority rejected")
        self.authorized.append((actor, organization_id, artifact_reference_id, transaction))
        return (actor, artifact_reference_id)

    async def revalidate_for_commit(
        self,
        grant: object,
        *,
        transaction: object,
    ) -> None:
        self.final.append((grant, transaction))


class Outbox:
    def __init__(self) -> None:
        self.events: list[tuple[ArtifactPromotionRequested, object]] = []

    async def append(
        self,
        event: ArtifactPromotionRequested,
        *,
        transaction: object,
    ) -> None:
        self.events.append((event, transaction))


def _success_result(*, size: int = len(CONTENT), digest: str = DIGEST) -> ProviderPayload:
    return {
        "contract_version": "1.1.0",
        "organization_id": str(ORG),
        "artifact_reference_id": str(REFERENCE),
        "outcome": "succeeded",
        "provider_version": "commit:abc",
        "content": {
            "download_url": "https://provider.example.test/artifacts/fixture",
            "media_type": "application/zip",
            "byte_size": size,
            "content_digest": digest,
        },
        "error": None,
    }


def _failure_result() -> ProviderPayload:
    return {
        "contract_version": "1.1.0",
        "organization_id": str(ORG),
        "artifact_reference_id": str(REFERENCE),
        "outcome": "failed",
        "provider_version": "fixture-1",
        "content": None,
        "error": {
            "code": "provider_unavailable",
            "message": "Bearer live-secret-token-value could not download",
            "retryable": True,
            "action": "retry",
        },
    }


def _actor(*, organization_id: UUID = ORG) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=USER,
        roles={"student"},
        membership_revision=1,
        auth_epoch=2,
    )


def _command(
    *,
    actor: RequestActor | None = None,
    binding_provider: ArtifactProviderName = "github",
    byte_limit: int = 1024,
) -> ArtifactCaptureCommand:
    return ArtifactCaptureCommand(
        organization_id=ORG,
        artifact_reference_id=REFERENCE,
        operation_id=OPERATION,
        provider="github",
        credential_binding=ArtifactCredentialBinding(
            CREDENTIAL,
            3,
            binding_provider,
        ),
        limits=ArtifactCaptureLimits(
            max_files=100,
            max_single_blob_bytes=byte_limit,
            max_total_bytes=byte_limit,
            max_archive_bytes=byte_limit,
            max_unpacked_bytes=byte_limit * 2,
        ),
        actor=actor or _actor(),
        worker_identity="test-artifact-worker",
    )


def _ids() -> Callable[[], UUID]:
    values = iter((ARTIFACT_VERSION, PROMOTION, MESSAGE))
    return lambda: next(values)


def _service(
    *,
    result: ProviderPayload | None = None,
) -> tuple[
    ArtifactCaptureService,
    Repository,
    Staging,
    Credentials,
    Authorization,
    Outbox,
    Provider,
]:
    repository = Repository()
    staging = Staging()
    credentials = Credentials()
    authorization = Authorization()
    outbox = Outbox()
    provider = Provider(result or _success_result())
    return (
        ArtifactCaptureService(
            repository=repository,
            staging=staging,
            credential_bindings=credentials,
            authorization=authorization,
            outbox=outbox,
            provider=provider,
            id_factory=_ids(),
            clock=lambda: NOW,
        ),
        repository,
        staging,
        credentials,
        authorization,
        outbox,
        provider,
    )


async def test_success_stages_bounded_bytes_then_records_one_atomic_db_bundle_and_outbox() -> None:
    service, repository, staging, credentials, authorization, outbox, provider = _service()

    result = await service.capture(_command(), transaction=TRANSACTION)

    assert result.artifact_version_id == ARTIFACT_VERSION
    assert result.promotion_id == PROMOTION
    assert result.operation_id == OPERATION
    assert result.state == "db_committed"
    assert result.replayed is False
    assert len(provider.requests) == len(staging.requests) == len(repository.successes) == 1
    request = provider.requests[0]
    assert request["credential_binding_id"] == str(CREDENTIAL)
    assert request["credential_binding_version"] == 3
    stage = staging.requests[0]
    assert stage.max_bytes == 1024
    assert stage.expected_content_digest == DIGEST
    assert stage.final_key == f"{ORG}/{ARTIFACT_VERSION}/artifact.bin"
    persisted = repository.successes[0]
    assert persisted["transaction"] is TRANSACTION
    assert str(persisted["input_version"]).startswith("artifact-capture:1.1.0:sha256:")
    version = persisted["artifact_version"]
    promotion = persisted["promotion"]
    assert isinstance(version, ArtifactVersionCaptureRecord)
    assert isinstance(promotion, ArtifactPromotionCaptureRecord)
    assert version.object_key == promotion.final_key == stage.final_key
    assert version.metadata["provider"] == "github"
    assert version.metadata["credential_binding_id"] == str(CREDENTIAL)
    assert "download_url" not in version.metadata
    attempt_metadata = persisted["attempt_metadata"]
    assert isinstance(attempt_metadata, Mapping)
    assert attempt_metadata["credential_binding_version"] == 3
    assert promotion.state == "db_committed"
    assert len(outbox.events) == 1
    event, transaction = outbox.events[0]
    assert transaction is TRANSACTION
    assert event.message_id == MESSAGE
    assert event.artifact_version_id == ARTIFACT_VERSION
    assert event.staged_key == stage.staged_key
    assert authorization.final and credentials.calls


@pytest.mark.parametrize("failure_kind", ["size", "digest", "media"])
async def test_staged_content_mismatch_creates_no_db_bundle_or_outbox(
    failure_kind: str,
) -> None:
    service, repository, staging, _credentials, authorization, outbox, _provider = _service()
    if failure_kind == "size":
        staging.size += 1
    elif failure_kind == "digest":
        staging.digest = OTHER_DIGEST
    else:
        staging.media_type = "application/octet-stream"

    with pytest.raises(ArtifactContentMismatch):
        await service.capture(_command(), transaction=TRANSACTION)

    assert repository.successes == repository.failures == []
    assert outbox.events == []
    assert authorization.final == []


async def test_provider_reported_size_over_limit_rolls_back_before_staging() -> None:
    service, repository, staging, _credentials, authorization, outbox, _provider = _service(
        result=_success_result(size=2048)
    )

    with pytest.raises(ArtifactContentMismatch, match="exceeds"):
        await service.capture(_command(byte_limit=1024), transaction=TRANSACTION)

    assert staging.requests == []
    assert repository.successes == repository.failures == []
    assert outbox.events == []
    assert authorization.final == []


async def test_provider_failure_records_sanitized_operation_attempt_without_promotion() -> None:
    service, repository, staging, _credentials, authorization, outbox, _provider = _service(
        result=_failure_result()
    )

    result = await service.capture(_command(), transaction=TRANSACTION)

    assert result.state == "retryable_failed"
    assert result.artifact_version_id is None
    assert result.promotion_id is None
    assert len(repository.failures) == 1
    failure = repository.failures[0]
    rendered = str(failure["error"])
    assert "live-secret-token-value" not in rendered
    assert "[REDACTED]" in rendered
    assert failure["state"] == "retryable_failed"
    assert staging.requests == []
    assert repository.successes == []
    assert outbox.events == []
    assert authorization.final


async def test_staging_failure_is_typed_and_never_creates_promotion() -> None:
    service, repository, staging, _credentials, _authorization, outbox, _provider = _service()
    staging.error = ArtifactStagingError(
        "Bearer staging-secret failed",
        retryable=False,
    )

    result = await service.capture(_command(), transaction=TRANSACTION)

    assert result.state == "action_required"
    assert result.artifact_version_id is None
    assert len(repository.failures) == 1
    assert "staging-secret" not in str(repository.failures[0]["error"])
    assert repository.successes == []
    assert outbox.events == []


async def test_same_reference_digest_replay_reuses_bundle_without_stage_or_outbox() -> None:
    service, repository, staging, _credentials, authorization, outbox, _provider = _service()
    existing_version = ArtifactVersionCaptureRecord(
        organization_id=ORG,
        artifact_version_id=ARTIFACT_VERSION,
        artifact_reference_id=REFERENCE,
        provider="github",
        provider_version="commit:abc",
        content_digest=DIGEST,
        object_key=f"{ORG}/{ARTIFACT_VERSION}/artifact.bin",
        media_type="application/zip",
        byte_size=len(CONTENT),
        captured_at=NOW,
        metadata={"provider": "github"},
    )
    existing_promotion = ArtifactPromotionCaptureRecord(
        organization_id=ORG,
        promotion_id=PROMOTION,
        artifact_version_id=ARTIFACT_VERSION,
        operation_id=OPERATION,
        staged_key=f"{ORG}/{ARTIFACT_VERSION}/staged/{OPERATION}",
        final_key=existing_version.object_key,
        state="db_committed",
        attempts=0,
        max_attempts=5,
    )
    repository.replay = ArtifactCaptureBundle(
        artifact_version=existing_version,
        promotion=existing_promotion,
        operation_id=OPERATION,
        operation_state="processing",
        attempt_number=1,
        replayed=True,
    )

    result = await service.capture(_command(), transaction=TRANSACTION)

    assert result.replayed is True
    assert result.artifact_version_id == ARTIFACT_VERSION
    assert result.promotion_id == PROMOTION
    assert staging.requests == []
    assert repository.successes == repository.failures == []
    assert outbox.events == []
    assert authorization.final


async def test_tenant_reference_and_exact_binding_mismatches_fail_closed() -> None:
    service, repository, _staging, credentials, authorization, outbox, _provider = _service()
    with pytest.raises(ArtifactCaptureBoundaryViolation, match="actor tenant"):
        await service.capture(
            _command(actor=_actor(organization_id=OTHER_ORG)),
            transaction=TRANSACTION,
        )
    with pytest.raises(ArtifactCaptureBoundaryViolation, match="credential provider"):
        await service.capture(_command(binding_provider="google_docs"), transaction=TRANSACTION)

    repository.reference = replace(repository.reference, organization_id=OTHER_ORG)
    with pytest.raises(ArtifactReferenceUnavailable):
        await service.capture(_command(), transaction=TRANSACTION)
    repository.reference = replace(repository.reference, organization_id=ORG)
    credentials.reject = True
    with pytest.raises(ArtifactCaptureBoundaryViolation, match="credential binding"):
        await service.capture(_command(), transaction=TRANSACTION)

    assert repository.successes == repository.failures == []
    assert outbox.events == []
    assert authorization.final == []
