"""Schema-backed Google Docs DOCX adapter over injected bounded clients."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import quote, unquote, urlparse
from uuid import UUID

from jsonschema import ValidationError as JsonSchemaValidationError

from review_platform.application.ports.providers import (
    ArtifactProvider,
    ProviderContractError,
    ProviderPayload,
)
from review_platform.contracts.registry import CONTRACT_VERSION, ContractRegistry

from .github_artifacts import (
    ArtifactContentHandoff,
    ContentLimitExceeded,
    CredentialBindingMismatch,
    CredentialMaterial,
    CredentialMaterialResolver,
    InvalidProviderResponse,
    ProviderClientError,
    UnsafeArtifactURL,
    _as_provider_error,
    _capture_failure,
    _capture_limits,
    _capture_success,
    _preflight_failure,
    _required_mapping,
    _required_positive_int,
    _required_string,
    _validate_handoff,
)

GOOGLE_DOCX_HARD_LIMIT_BYTES = 10_000_000
GOOGLE_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]{10,256}$")
_GOOGLE_DOCS_HOSTS = frozenset({"docs.google.com"})


@dataclass(frozen=True, slots=True)
class GoogleDocumentLocator:
    document_id: str

    @property
    def external_id(self) -> str:
        return self.document_id

    @property
    def canonical_url(self) -> str:
        return f"https://docs.google.com/document/d/{quote(self.document_id, safe='')}/edit"


@dataclass(frozen=True, slots=True)
class GoogleDocumentSnapshot:
    document_id: str
    revision_id: str
    source_url: str
    anonymous_access: bool


class GoogleDocsArtifactClient(Protocol):
    """Injected API client that must keep redirects disabled."""

    async def inspect_document(
        self,
        locator: GoogleDocumentLocator,
        *,
        credential: CredentialMaterial,
        follow_redirects: Literal[False] = False,
    ) -> GoogleDocumentSnapshot: ...

    def stream_docx(
        self,
        snapshot: GoogleDocumentSnapshot,
        *,
        credential: CredentialMaterial,
        max_bytes: int,
        follow_redirects: Literal[False] = False,
    ) -> AsyncIterator[bytes]: ...


class GoogleDocsArtifactProvider(ArtifactProvider):
    """Export only exact allowlisted Google document IDs as bounded DOCX."""

    def __init__(
        self,
        *,
        client: GoogleDocsArtifactClient,
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
        self._require_provider(request)
        organization_id, credential = await self._resolve_credential(request)
        locator = parse_google_docs_url(_required_string(request, "url"))
        try:
            snapshot = await self._client.inspect_document(
                locator,
                credential=credential,
                follow_redirects=False,
            )
            _validate_snapshot(locator, snapshot)
            result: ProviderPayload = {
                "contract_version": CONTRACT_VERSION,
                "organization_id": str(organization_id),
                "provider": "google_docs",
                "read_capability": "available",
                "feedback_capability": "not_supported",
                "locator": {
                    "canonical_url": locator.canonical_url,
                    "external_id": locator.external_id,
                },
                "error": None,
            }
        except ProviderClientError as error:
            result = _preflight_failure(
                organization_id=organization_id,
                provider="google_docs",
                error=error,
            )
        return self._validated_result(result, definition="preflight_result")

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        self._validate_request(request, definition="capture_request")
        self._require_provider(request)
        organization_id, credential = await self._resolve_credential(request)
        artifact_reference_id = UUID(_required_string(request, "artifact_reference_id"))
        locator_payload = _required_mapping(request, "locator")
        locator = parse_google_docs_url(_required_string(locator_payload, "canonical_url"))
        if _required_string(locator_payload, "external_id") != locator.document_id:
            raise InvalidProviderResponse("Google Docs locator identity mismatched")
        limits = _capture_limits(request)
        max_bytes = min(
            GOOGLE_DOCX_HARD_LIMIT_BYTES,
            limits["max_total_bytes"],
            limits["max_archive_bytes"],
        )
        provider_version = "revision:unresolved"
        try:
            snapshot = await self._client.inspect_document(
                locator,
                credential=credential,
                follow_redirects=False,
            )
            _validate_snapshot(locator, snapshot)
            if not snapshot.revision_id or len(snapshot.revision_id) > 247:
                raise InvalidProviderResponse("Google Docs revision identity is invalid")
            provider_version = f"revision:{snapshot.revision_id}"
            content = await self._handoff.ingest(
                self._client.stream_docx(
                    snapshot,
                    credential=credential,
                    max_bytes=max_bytes,
                    follow_redirects=False,
                ),
                max_bytes=max_bytes,
                expected_media_type=GOOGLE_DOCX_MEDIA_TYPE,
                provider_version=provider_version,
            )
            _validate_handoff(
                content,
                max_bytes=max_bytes,
                expected_media_type=GOOGLE_DOCX_MEDIA_TYPE,
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
    ) -> tuple[UUID, CredentialMaterial]:
        organization_id = UUID(_required_string(request, "organization_id"))
        binding_id = UUID(_required_string(request, "credential_binding_id"))
        version = _required_positive_int(request, "credential_binding_version")
        material = await self._credentials.resolve_exact(
            organization_id=organization_id,
            credential_binding_id=binding_id,
            credential_binding_version=version,
            provider="google_docs",
        )
        if material is None or (
            material.organization_id != organization_id
            or material.credential_binding_id != binding_id
            or material.credential_binding_version != version
            or material.provider != "google_docs"
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
    def _require_provider(request: ProviderPayload) -> None:
        if request.get("provider") != "google_docs":
            raise ProviderContractError("Google Docs adapter received another provider kind")


def parse_google_docs_url(value: str) -> GoogleDocumentLocator:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise UnsafeArtifactURL("Google Docs URL has an invalid port") from error
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() not in _GOOGLE_DOCS_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        raise UnsafeArtifactURL("Google Docs URL is outside the HTTPS allowlist")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0:2] != ["document", "d"]:
        raise UnsafeArtifactURL("Google Docs URL must identify a document")
    document_id = parts[2]
    if not _DOCUMENT_ID.fullmatch(document_id):
        raise UnsafeArtifactURL("Google document ID contains unsafe characters")
    if len(parts) > 4 or (len(parts) == 4 and parts[3] not in {"edit", "view", "preview"}):
        raise UnsafeArtifactURL("Google Docs URL path is not an allowed document view")
    return GoogleDocumentLocator(document_id=document_id)


def _validate_snapshot(
    requested: GoogleDocumentLocator,
    snapshot: GoogleDocumentSnapshot,
) -> None:
    if snapshot.document_id != requested.document_id:
        raise InvalidProviderResponse("Google Docs client returned another document")
    observed = parse_google_docs_url(snapshot.source_url)
    if observed.document_id != requested.document_id:
        raise UnsafeArtifactURL("Google Docs client followed a cross-document redirect")


__all__ = [
    "GOOGLE_DOCX_HARD_LIMIT_BYTES",
    "GOOGLE_DOCX_MEDIA_TYPE",
    "GoogleDocsArtifactClient",
    "GoogleDocsArtifactProvider",
    "GoogleDocumentLocator",
    "GoogleDocumentSnapshot",
    "parse_google_docs_url",
]
