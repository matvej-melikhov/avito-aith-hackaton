"""Schema-backed GitHub artifact adapter over injected bounded API clients."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import ParseResult, quote, unquote, urlparse
from uuid import UUID

from jsonschema import ValidationError as JsonSchemaValidationError

from review_platform.application.ports.providers import (
    ArtifactProvider,
    JsonValue,
    ProviderContractError,
    ProviderPayload,
)
from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry
from review_platform.domain.primitives import sanitize_error, validate_digest

_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


class ArtifactAdapterError(RuntimeError):
    """Base typed failure before a provider result can be trusted."""


class UnsafeArtifactURL(ArtifactAdapterError):
    """The user or client supplied a non-allowlisted provider URL."""


class CredentialBindingMismatch(ArtifactAdapterError):
    """The resolver did not return the exact requested credential generation."""


class ContentLimitExceeded(ArtifactAdapterError):
    """The provider stream exceeded the adapter's explicit byte ceiling."""


class InvalidProviderResponse(ArtifactAdapterError):
    """Injected provider client or handoff violated its typed boundary."""


class ProviderClientError(RuntimeError):
    """Sanitizable provider failure with retry and recovery semantics."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        action: str | None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.action = action


@dataclass(frozen=True, slots=True)
class CredentialMaterial:
    organization_id: UUID
    credential_binding_id: UUID
    credential_binding_version: int
    provider: Literal["github", "google_docs"]
    secret: str | None = field(default=None, repr=False)


class CredentialMaterialResolver(Protocol):
    async def resolve_exact(
        self,
        *,
        organization_id: UUID,
        credential_binding_id: UUID,
        credential_binding_version: int,
        provider: Literal["github", "google_docs"],
    ) -> CredentialMaterial | None: ...


@dataclass(frozen=True, slots=True)
class HandoffContent:
    download_url: str
    byte_size: int
    content_digest: str
    media_type: str


class ArtifactContentHandoff(Protocol):
    """Bound and spool a provider stream behind a local opaque URL."""

    async def ingest(
        self,
        stream: AsyncIterator[bytes],
        *,
        max_bytes: int,
        expected_media_type: str,
        provider_version: str,
    ) -> HandoffContent: ...


@dataclass(frozen=True, slots=True)
class GitHubLocator:
    owner: str
    repository: str
    ref: str

    @property
    def external_id(self) -> str:
        return f"{self.owner}/{self.repository}@{self.ref}"

    @property
    def canonical_url(self) -> str:
        return (
            f"https://github.com/{quote(self.owner, safe='')}/"
            f"{quote(self.repository, safe='')}/tree/{quote(self.ref, safe='')}"
        )


@dataclass(frozen=True, slots=True)
class GitHubRepositorySnapshot:
    owner: str
    repository: str
    resolved_commit: str
    source_url: str
    feedback_supported: bool = True


@dataclass(frozen=True, slots=True)
class GitHubCaptureLimits:
    max_files: int
    max_single_blob_bytes: int
    max_total_bytes: int
    max_archive_bytes: int
    max_unpacked_bytes: int


class GitHubArtifactClient(Protocol):
    async def inspect_repository(
        self,
        locator: GitHubLocator,
        *,
        credential: CredentialMaterial,
        follow_redirects: Literal[False] = False,
    ) -> GitHubRepositorySnapshot: ...

    def stream_archive(
        self,
        snapshot: GitHubRepositorySnapshot,
        *,
        credential: CredentialMaterial,
        limits: GitHubCaptureLimits,
        follow_redirects: Literal[False] = False,
    ) -> AsyncIterator[bytes]: ...


class GitHubArtifactProvider(ArtifactProvider):
    """Resolve and capture only ``https://github.com/<owner>/<repo>`` resources."""

    def __init__(
        self,
        *,
        client: GitHubArtifactClient,
        credentials: CredentialMaterialResolver,
        handoff: ArtifactContentHandoff,
        registry: ContractRegistry | None = None,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._handoff = handoff
        self._registry = registry or ContractRegistry()

    @property
    def contract_version(self) -> str:
        return CONTRACT_VERSION

    @property
    def schema_name(self) -> str:
        return "artifact-provider.schema.json"

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        self._validate_request(request, definition="preflight_request")
        self._require_provider(request, "github")
        organization_id, binding = await self._resolve_credential(request, provider="github")
        locator = parse_github_url(_required_string(request, "url"))
        try:
            snapshot = await self._client.inspect_repository(
                locator,
                credential=binding,
                follow_redirects=False,
            )
            pinned = _validate_snapshot(locator, snapshot)
            result: ProviderPayload = {
                "contract_version": CONTRACT_VERSION,
                "organization_id": str(organization_id),
                "provider": "github",
                "read_capability": "available",
                "feedback_capability": (
                    "available" if snapshot.feedback_supported else "not_supported"
                ),
                "locator": {
                    "canonical_url": pinned.canonical_url,
                    "external_id": pinned.external_id,
                },
                "error": None,
            }
        except ProviderClientError as error:
            result = _preflight_failure(
                organization_id=organization_id,
                provider="github",
                error=error,
            )
        return self._validated_result(result, definition="preflight_result")

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        self._validate_request(request, definition="capture_request")
        self._require_provider(request, "github")
        organization_id, binding = await self._resolve_credential(request, provider="github")
        artifact_reference_id = UUID(_required_string(request, "artifact_reference_id"))
        locator_payload = _required_mapping(request, "locator")
        locator = parse_github_url(_required_string(locator_payload, "canonical_url"))
        external_id = _required_string(locator_payload, "external_id")
        # Frozen fixtures predate pinned-ref locators and use owner/repository.
        if external_id != locator.external_id and external_id != (
            f"{locator.owner}/{locator.repository}"
        ):
            raise InvalidProviderResponse("GitHub locator external identity mismatched")
        limits = _capture_limits(request)
        capture_limits = GitHubCaptureLimits(**limits)
        provider_version = f"commit:{locator.ref}"
        try:
            if not _COMMIT.fullmatch(locator.ref):
                snapshot = await self._client.inspect_repository(
                    locator,
                    credential=binding,
                    follow_redirects=False,
                )
                locator = _validate_snapshot(locator, snapshot)
                provider_version = f"commit:{locator.ref}"
                source = snapshot
            else:
                source = GitHubRepositorySnapshot(
                    owner=locator.owner,
                    repository=locator.repository,
                    resolved_commit=locator.ref.lower(),
                    source_url=locator.canonical_url,
                )
            content = await self._handoff.ingest(
                self._client.stream_archive(
                    source,
                    credential=binding,
                    limits=capture_limits,
                    follow_redirects=False,
                ),
                max_bytes=min(limits["max_archive_bytes"], limits["max_total_bytes"]),
                expected_media_type="application/zip",
                provider_version=provider_version,
            )
            _validate_handoff(
                content,
                max_bytes=min(limits["max_archive_bytes"], limits["max_total_bytes"]),
                expected_media_type="application/zip",
            )
            result = _capture_success(
                organization_id=organization_id,
                artifact_reference_id=artifact_reference_id,
                provider_version=provider_version,
                content=content,
            )
        except (ProviderClientError, ContentLimitExceeded) as error:
            result = _capture_failure(
                organization_id=organization_id,
                artifact_reference_id=artifact_reference_id,
                provider_version=provider_version,
                error=_as_provider_error(error),
            )
        return self._validated_result(result, definition="capture_result")

    async def _resolve_credential(
        self,
        request: ProviderPayload,
        *,
        provider: Literal["github", "google_docs"],
    ) -> tuple[UUID, CredentialMaterial]:
        organization_id = UUID(_required_string(request, "organization_id"))
        binding_id = UUID(_required_string(request, "credential_binding_id"))
        version = _required_positive_int(request, "credential_binding_version")
        material = await self._credentials.resolve_exact(
            organization_id=organization_id,
            credential_binding_id=binding_id,
            credential_binding_version=version,
            provider=provider,
        )
        if material is None or (
            material.organization_id != organization_id
            or material.credential_binding_id != binding_id
            or material.credential_binding_version != version
            or material.provider != provider
        ):
            raise CredentialBindingMismatch("credential resolver returned a different binding")
        return organization_id, material

    def _validate_request(self, request: ProviderPayload, *, definition: str) -> None:
        try:
            self._registry.validate(request, self.schema_name, definition=definition)
        except JsonSchemaValidationError as error:
            raise ProviderContractError(
                f"payload violates {self.schema_name}#/$defs/{definition}"
            ) from error

    def _validated_result(self, result: ProviderPayload, *, definition: str) -> ProviderPayload:
        try:
            self._registry.validate(result, self.schema_name, definition=definition)
        except JsonSchemaValidationError as error:
            raise InvalidProviderResponse(
                f"adapter produced invalid {self.schema_name}#/$defs/{definition}"
            ) from error
        return result

    @staticmethod
    def _require_provider(request: ProviderPayload, expected: str) -> None:
        if request.get("provider") != expected:
            raise ProviderContractError(f"{expected} adapter received another provider kind")


def parse_github_url(value: str) -> GitHubLocator:
    parsed = urlparse(value)
    _require_safe_provider_url(parsed, allowed_hosts=_GITHUB_HOSTS)
    if parsed.query or parsed.fragment:
        raise UnsafeArtifactURL("GitHub URL must not contain query or fragment")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 2 or any(part in {".", ".."} for part in parts):
        raise UnsafeArtifactURL("GitHub URL must identify owner and repository")
    owner, repository = parts[0], parts[1].removesuffix(".git")
    if not _SEGMENT.fullmatch(owner) or not _SEGMENT.fullmatch(repository):
        raise UnsafeArtifactURL("GitHub owner or repository contains unsafe characters")
    if len(parts) == 2:
        ref = "HEAD"
    elif len(parts) >= 4 and parts[2] in {"tree", "commit"}:
        ref = "/".join(parts[3:])
    else:
        raise UnsafeArtifactURL("GitHub URL path is not a repository, tree, or commit")
    if not ref or len(ref) > 256 or any(part in {"", ".", ".."} for part in ref.split("/")):
        raise UnsafeArtifactURL("GitHub ref is unsafe")
    return GitHubLocator(owner=owner, repository=repository, ref=ref)


def _validate_snapshot(
    requested: GitHubLocator,
    snapshot: GitHubRepositorySnapshot,
) -> GitHubLocator:
    if snapshot.owner != requested.owner or snapshot.repository != requested.repository:
        raise InvalidProviderResponse("GitHub client returned another repository")
    observed = parse_github_url(snapshot.source_url)
    if observed.owner != requested.owner or observed.repository != requested.repository:
        raise UnsafeArtifactURL("GitHub client followed a cross-repository redirect")
    if not _COMMIT.fullmatch(snapshot.resolved_commit):
        raise InvalidProviderResponse("GitHub client returned an invalid commit identity")
    return GitHubLocator(
        owner=requested.owner,
        repository=requested.repository,
        ref=snapshot.resolved_commit.lower(),
    )


def _require_safe_provider_url(
    parsed: ParseResult,
    *,
    allowed_hosts: frozenset[str],
) -> None:
    scheme = parsed.scheme
    hostname = parsed.hostname
    username = parsed.username
    password = parsed.password
    try:
        port = parsed.port
    except ValueError as error:
        raise UnsafeArtifactURL("provider URL has an invalid port") from error
    if (
        scheme != "https"
        or hostname is None
        or hostname.casefold() not in allowed_hosts
        or username is not None
        or password is not None
        or port not in {None, 443}
    ):
        raise UnsafeArtifactURL("provider URL is outside the HTTPS allowlist")


def _capture_limits(request: ProviderPayload) -> dict[str, int]:
    raw = _required_mapping(request, "limits")
    names = (
        "max_files",
        "max_single_blob_bytes",
        "max_total_bytes",
        "max_archive_bytes",
        "max_unpacked_bytes",
    )
    return {name: _required_positive_int(raw, name) for name in names}


def _required_mapping(value: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ProviderContractError(f"{key} must be an object")
    return item


def _required_string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ProviderContractError(f"{key} must be a non-empty string")
    return item


def _required_positive_int(value: Mapping[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise ProviderContractError(f"{key} must be a positive integer")
    return item


def _validate_handoff(
    content: HandoffContent,
    *,
    max_bytes: int,
    expected_media_type: str,
) -> None:
    parsed = urlparse(content.download_url)
    if parsed.scheme != "artifact-handoff" or parsed.username or parsed.password:
        raise InvalidProviderResponse("handoff URL must be local, opaque, and credential-free")
    if not 1 <= content.byte_size <= max_bytes:
        raise ContentLimitExceeded("handoff content exceeded the bounded byte limit")
    validate_digest(content.content_digest)
    if content.media_type != expected_media_type:
        raise InvalidProviderResponse("handoff media type mismatched")


def _preflight_failure(
    *,
    organization_id: UUID,
    provider: Literal["github", "google_docs"],
    error: ProviderClientError,
) -> ProviderPayload:
    return {
        "contract_version": CONTRACT_VERSION,
        "organization_id": str(organization_id),
        "provider": provider,
        "read_capability": "requires_action" if error.action else "unavailable",
        "feedback_capability": "requires_action" if error.action else "not_supported",
        "locator": None,
        "error": _safe_error(error),
    }


def _capture_success(
    *,
    organization_id: UUID,
    artifact_reference_id: UUID,
    provider_version: str,
    content: HandoffContent,
) -> ProviderPayload:
    return {
        "contract_version": CONTRACT_VERSION,
        "organization_id": str(organization_id),
        "artifact_reference_id": str(artifact_reference_id),
        "outcome": "succeeded",
        "provider_version": provider_version,
        "content": {
            "download_url": content.download_url,
            "media_type": content.media_type,
            "byte_size": content.byte_size,
            "content_digest": content.content_digest,
        },
        "error": None,
    }


def _capture_failure(
    *,
    organization_id: UUID,
    artifact_reference_id: UUID,
    provider_version: str,
    error: ProviderClientError,
) -> ProviderPayload:
    return {
        "contract_version": CONTRACT_VERSION,
        "organization_id": str(organization_id),
        "artifact_reference_id": str(artifact_reference_id),
        "outcome": "failed",
        "provider_version": provider_version,
        "content": None,
        "error": _safe_error(error),
    }


def _as_provider_error(error: ProviderClientError | ContentLimitExceeded) -> ProviderClientError:
    if isinstance(error, ProviderClientError):
        return error
    return ProviderClientError(
        "artifact_size_limit_exceeded",
        "Artifact content exceeds the configured byte limit",
        retryable=False,
        action="reduce_artifact_size",
    )


def _safe_error(error: ProviderClientError) -> dict[str, JsonValue]:
    bounded = sanitize_error(
        {
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
            "action": error.action,
        },
        max_bytes=2048,
    )
    bounded["retryable"] = error.retryable
    if error.action is None:
        bounded["action"] = None
    return bounded


__all__ = [
    "ArtifactAdapterError",
    "ArtifactContentHandoff",
    "ContentLimitExceeded",
    "CredentialBindingMismatch",
    "CredentialMaterial",
    "CredentialMaterialResolver",
    "GitHubArtifactClient",
    "GitHubArtifactProvider",
    "GitHubCaptureLimits",
    "GitHubLocator",
    "GitHubRepositorySnapshot",
    "HandoffContent",
    "InvalidProviderResponse",
    "ProviderClientError",
    "UnsafeArtifactURL",
    "parse_github_url",
]
