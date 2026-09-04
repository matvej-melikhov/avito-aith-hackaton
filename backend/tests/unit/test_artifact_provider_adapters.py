"""Offline contract tests for GitHub and Google Docs artifact adapters."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

import pytest

from review_platform.application.ports.providers import ProviderPayload
from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.providers.github_artifacts import (
    ArtifactContentHandoff,
    ContentLimitExceeded,
    CredentialBindingMismatch,
    CredentialMaterial,
    GitHubArtifactProvider,
    GitHubCaptureLimits,
    GitHubLocator,
    GitHubRepositorySnapshot,
    HandoffContent,
    ProviderClientError,
    UnsafeArtifactURL,
    parse_github_url,
)
from review_platform.infrastructure.providers.google_docs_artifacts import (
    GOOGLE_DOCX_HARD_LIMIT_BYTES,
    GOOGLE_DOCX_MEDIA_TYPE,
    GoogleDocsArtifactProvider,
    GoogleDocumentLocator,
    GoogleDocumentSnapshot,
    parse_google_docs_url,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
REFERENCE = UUID("00000000-0000-7000-8000-000000000020")
GITHUB_CREDENTIAL = UUID("00000000-0000-7000-8000-000000000021")
GOOGLE_CREDENTIAL = UUID("00000000-0000-7000-8000-000000000031")
COMMIT = "a" * 40
DOCUMENT_ID = "offlineGoogleDocument1234567890"
REVISION = "google-revision-7"


class Resolver:
    def __init__(self, *, mismatch: bool = False, anonymous: bool = False) -> None:
        self.mismatch = mismatch
        self.anonymous = anonymous
        self.calls: list[tuple[UUID, UUID, int, str]] = []

    async def resolve_exact(
        self,
        *,
        organization_id: UUID,
        credential_binding_id: UUID,
        credential_binding_version: int,
        provider: Literal["github", "google_docs"],
    ) -> CredentialMaterial | None:
        self.calls.append(
            (
                organization_id,
                credential_binding_id,
                credential_binding_version,
                provider,
            )
        )
        return CredentialMaterial(
            organization_id=organization_id,
            credential_binding_id=(
                UUID("00000000-0000-7000-8000-000000000099")
                if self.mismatch
                else credential_binding_id
            ),
            credential_binding_version=credential_binding_version,
            provider=provider,
            secret=None if self.anonymous else "provider-secret-material",
        )


class Handoff(ArtifactContentHandoff):
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    async def ingest(
        self,
        stream: AsyncIterator[bytes],
        *,
        max_bytes: int,
        expected_media_type: str,
        provider_version: str,
    ) -> HandoffContent:
        self.calls.append((max_bytes, expected_media_type, provider_version))
        payload = bytearray()
        async for chunk in stream:
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise ContentLimitExceeded("bounded handoff rejected oversized stream")
        if not payload:
            raise ContentLimitExceeded("empty provider content")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        return HandoffContent(
            download_url=f"artifact-handoff://{digest.removeprefix('sha256:')}",
            byte_size=len(payload),
            content_digest=digest,
            media_type=expected_media_type,
        )


class GitHubClient:
    def __init__(
        self,
        *,
        content: bytes = b"github-archive",
        failure: ProviderClientError | None = None,
        source_url: str = "https://github.com/example/repository",
    ) -> None:
        self.content = content
        self.failure = failure
        self.source_url = source_url
        self.inspect_calls: list[tuple[GitHubLocator, CredentialMaterial, bool]] = []
        self.capture_limits: list[GitHubCaptureLimits] = []

    async def inspect_repository(
        self,
        locator: GitHubLocator,
        *,
        credential: CredentialMaterial,
        follow_redirects: Literal[False] = False,
    ) -> GitHubRepositorySnapshot:
        self.inspect_calls.append((locator, credential, follow_redirects))
        if self.failure is not None:
            raise self.failure
        return GitHubRepositorySnapshot(
            owner=locator.owner,
            repository=locator.repository,
            resolved_commit=COMMIT,
            source_url=self.source_url,
            feedback_supported=True,
        )

    def stream_archive(
        self,
        snapshot: GitHubRepositorySnapshot,
        *,
        credential: CredentialMaterial,
        limits: GitHubCaptureLimits,
        follow_redirects: Literal[False] = False,
    ) -> AsyncIterator[bytes]:
        assert snapshot.resolved_commit == COMMIT
        assert credential.credential_binding_id == GITHUB_CREDENTIAL
        assert follow_redirects is False
        self.capture_limits.append(limits)

        async def chunks() -> AsyncIterator[bytes]:
            yield self.content

        return chunks()


class GoogleClient:
    def __init__(
        self,
        *,
        content: bytes = b"docx-content",
        failure: ProviderClientError | None = None,
        source_url: str | None = None,
    ) -> None:
        self.content = content
        self.failure = failure
        self.source_url = source_url or (
            f"https://docs.google.com/document/d/{DOCUMENT_ID}/edit"
        )
        self.max_bytes: list[int] = []
        self.credentials: list[CredentialMaterial] = []

    async def inspect_document(
        self,
        locator: GoogleDocumentLocator,
        *,
        credential: CredentialMaterial,
        follow_redirects: Literal[False] = False,
    ) -> GoogleDocumentSnapshot:
        assert follow_redirects is False
        self.credentials.append(credential)
        if self.failure is not None:
            raise self.failure
        return GoogleDocumentSnapshot(
            document_id=locator.document_id,
            revision_id=REVISION,
            source_url=self.source_url,
            anonymous_access=credential.secret is None,
        )

    def stream_docx(
        self,
        snapshot: GoogleDocumentSnapshot,
        *,
        credential: CredentialMaterial,
        max_bytes: int,
        follow_redirects: Literal[False] = False,
    ) -> AsyncIterator[bytes]:
        assert snapshot.revision_id == REVISION
        assert follow_redirects is False
        self.credentials.append(credential)
        self.max_bytes.append(max_bytes)

        async def chunks() -> AsyncIterator[bytes]:
            yield self.content

        return chunks()


def _github_preflight_request(*, version: int = 1) -> ProviderPayload:
    return {
        "contract_version": "1.1.0",
        "organization_id": str(ORG),
        "provider": "github",
        "url": "https://github.com/example/repository",
        "credential_binding_id": str(GITHUB_CREDENTIAL),
        "credential_binding_version": version,
    }


def _capture_request(
    *,
    provider: Literal["github", "google_docs"],
    locator: MappingPayload,
    credential_id: UUID,
    max_archive_bytes: int = 100,
    max_total_bytes: int = 100,
) -> ProviderPayload:
    return {
        "contract_version": "1.1.0",
        "organization_id": str(ORG),
        "artifact_reference_id": str(REFERENCE),
        "provider": provider,
        "locator": locator,
        "credential_binding_id": str(credential_id),
        "credential_binding_version": 1,
        "limits": {
            "max_files": 10,
            "max_single_blob_bytes": 50,
            "max_total_bytes": max_total_bytes,
            "max_archive_bytes": max_archive_bytes,
            "max_unpacked_bytes": 200,
        },
    }


type MappingPayload = dict[str, str]


def _validate(payload: ProviderPayload, definition: str) -> None:
    ContractRegistry().validate(
        payload,
        "artifact-provider.schema.json",
        definition=definition,
    )


@pytest.mark.anyio
async def test_github_preflight_pins_commit_and_validates_frozen_contract() -> None:
    resolver = Resolver()
    client = GitHubClient()
    provider = GitHubArtifactProvider(
        client=client,
        credentials=resolver,
        handoff=Handoff(),
    )

    result = await provider.preflight(_github_preflight_request())

    _validate(result, "preflight_result")
    assert result["read_capability"] == "available"
    locator = result["locator"]
    assert isinstance(locator, dict)
    assert locator["external_id"] == f"example/repository@{COMMIT}"
    assert locator["canonical_url"].endswith(f"/tree/{COMMIT}")
    assert resolver.calls == [(ORG, GITHUB_CREDENTIAL, 1, "github")]
    assert client.inspect_calls[0][2] is False


@pytest.mark.anyio
async def test_github_private_preflight_is_actionable_and_redacts_provider_secret() -> None:
    secret = "live-provider-secret"
    client = GitHubClient(
        failure=ProviderClientError(
            "access_denied",
            f"Bearer {secret}",
            retryable=False,
            action="install_github_app",
        )
    )
    provider = GitHubArtifactProvider(
        client=client,
        credentials=Resolver(),
        handoff=Handoff(),
    )

    result = await provider.preflight(_github_preflight_request())

    _validate(result, "preflight_result")
    assert result["read_capability"] == "requires_action"
    assert result["locator"] is None
    assert secret not in repr(result)
    error = result["error"]
    assert isinstance(error, dict)
    assert error["action"] == "install_github_app"


@pytest.mark.anyio
async def test_github_capture_is_bounded_and_returns_local_digest_handoff() -> None:
    resolver = Resolver()
    client = GitHubClient(content=b"immutable-github-archive")
    handoff = Handoff()
    provider = GitHubArtifactProvider(
        client=client,
        credentials=resolver,
        handoff=handoff,
    )
    request = _capture_request(
        provider="github",
        locator={
            "canonical_url": f"https://github.com/example/repository/tree/{COMMIT}",
            "external_id": f"example/repository@{COMMIT}",
        },
        credential_id=GITHUB_CREDENTIAL,
    )

    first = await provider.capture(request)
    second = await provider.capture(request)

    for result in (first, second):
        _validate(result, "capture_result")
        assert result["organization_id"] == str(ORG)
        assert result["artifact_reference_id"] == str(REFERENCE)
        assert result["provider_version"] == f"commit:{COMMIT}"
        content = result["content"]
        assert isinstance(content, dict)
        assert str(content["download_url"]).startswith("artifact-handoff://")
        assert content["content_digest"] == (
            "sha256:" + hashlib.sha256(b"immutable-github-archive").hexdigest()
        )
    assert first["content"] == second["content"]
    assert client.capture_limits[0] == GitHubCaptureLimits(10, 50, 100, 100, 200)
    assert handoff.calls[0] == (100, "application/zip", f"commit:{COMMIT}")


@pytest.mark.anyio
async def test_exact_credential_version_mismatch_fails_before_github_client() -> None:
    resolver = Resolver(mismatch=True)
    client = GitHubClient()
    provider = GitHubArtifactProvider(
        client=client,
        credentials=resolver,
        handoff=Handoff(),
    )

    with pytest.raises(CredentialBindingMismatch):
        await provider.preflight(_github_preflight_request(version=7))
    assert client.inspect_calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/repository",
        "https://github.com.evil.test/example/repository",
        "https://127.0.0.1/example/repository",
        "https://token@github.com/example/repository",
        "https://github.com:444/example/repository",
        "https://github.com/example/repository?redirect=https://127.0.0.1",
        "https://github.com/example/repository/../../admin",
    ],
)
def test_github_url_allowlist_rejects_ssrf_and_ambiguous_paths(url: str) -> None:
    with pytest.raises(UnsafeArtifactURL):
        parse_github_url(url)


@pytest.mark.anyio
async def test_github_rejects_cross_host_redirect_observation() -> None:
    provider = GitHubArtifactProvider(
        client=GitHubClient(source_url="https://github.com.evil.test/example/repository"),
        credentials=Resolver(),
        handoff=Handoff(),
    )
    with pytest.raises(UnsafeArtifactURL):
        await provider.preflight(_github_preflight_request())


def _google_preflight_request() -> ProviderPayload:
    return {
        "contract_version": "1.1.0",
        "organization_id": str(ORG),
        "provider": "google_docs",
        "url": f"https://docs.google.com/document/d/{DOCUMENT_ID}/edit?usp=sharing",
        "credential_binding_id": str(GOOGLE_CREDENTIAL),
        "credential_binding_version": 1,
    }


@pytest.mark.anyio
async def test_google_preflight_supports_explicit_anonymous_binding() -> None:
    resolver = Resolver(anonymous=True)
    client = GoogleClient()
    provider = GoogleDocsArtifactProvider(
        client=client,
        credentials=resolver,
        handoff=Handoff(),
    )

    result = await provider.preflight(_google_preflight_request())

    _validate(result, "preflight_result")
    assert result["read_capability"] == "available"
    assert result["feedback_capability"] == "not_supported"
    assert client.credentials[0].secret is None
    assert resolver.calls == [(ORG, GOOGLE_CREDENTIAL, 1, "google_docs")]


@pytest.mark.anyio
async def test_google_private_error_is_typed_and_redacted() -> None:
    secret = "google-provider-secret"
    provider = GoogleDocsArtifactProvider(
        client=GoogleClient(
            failure=ProviderClientError(
                "access_denied",
                f"Bearer {secret}",
                retryable=False,
                action="share_document",
            )
        ),
        credentials=Resolver(anonymous=True),
        handoff=Handoff(),
    )

    result = await provider.preflight(_google_preflight_request())

    _validate(result, "preflight_result")
    assert result["read_capability"] == "requires_action"
    assert result["locator"] is None
    assert secret not in repr(result)
    error = result["error"]
    assert isinstance(error, dict)
    assert error["action"] == "share_document"


@pytest.mark.anyio
async def test_google_capture_pins_revision_and_enforces_provider_10mb_limit() -> None:
    client = GoogleClient(content=b"deterministic-docx")
    handoff = Handoff()
    provider = GoogleDocsArtifactProvider(
        client=client,
        credentials=Resolver(anonymous=True),
        handoff=handoff,
    )
    request = _capture_request(
        provider="google_docs",
        locator={
            "canonical_url": f"https://docs.google.com/document/d/{DOCUMENT_ID}/edit",
            "external_id": DOCUMENT_ID,
        },
        credential_id=GOOGLE_CREDENTIAL,
        max_archive_bytes=20_000_000,
        max_total_bytes=20_000_000,
    )

    result = await provider.capture(request)

    _validate(result, "capture_result")
    assert result["provider_version"] == f"revision:{REVISION}"
    content = result["content"]
    assert isinstance(content, dict)
    assert content["media_type"] == GOOGLE_DOCX_MEDIA_TYPE
    assert client.max_bytes == [GOOGLE_DOCX_HARD_LIMIT_BYTES]
    assert handoff.calls[0][0] == GOOGLE_DOCX_HARD_LIMIT_BYTES


@pytest.mark.anyio
async def test_google_oversize_stream_returns_typed_nonretryable_failure() -> None:
    client = GoogleClient(content=b"x" * 11)
    provider = GoogleDocsArtifactProvider(
        client=client,
        credentials=Resolver(anonymous=True),
        handoff=Handoff(),
    )
    request = _capture_request(
        provider="google_docs",
        locator={
            "canonical_url": f"https://docs.google.com/document/d/{DOCUMENT_ID}/edit",
            "external_id": DOCUMENT_ID,
        },
        credential_id=GOOGLE_CREDENTIAL,
        max_archive_bytes=10,
        max_total_bytes=10,
    )

    result = await provider.capture(request)

    _validate(result, "capture_result")
    assert result["outcome"] == "failed"
    assert result["content"] is None
    error = result["error"]
    assert isinstance(error, dict)
    assert error == {
        "code": "artifact_size_limit_exceeded",
        "message": "Artifact content exceeds the configured byte limit",
        "retryable": False,
        "action": "reduce_artifact_size",
    }


@pytest.mark.parametrize(
    "url",
    [
        f"http://docs.google.com/document/d/{DOCUMENT_ID}/edit",
        f"https://docs.google.com.evil.test/document/d/{DOCUMENT_ID}/edit",
        f"https://127.0.0.1/document/d/{DOCUMENT_ID}/edit",
        f"https://token@docs.google.com/document/d/{DOCUMENT_ID}/edit",
        f"https://docs.google.com:444/document/d/{DOCUMENT_ID}/edit",
        f"https://docs.google.com/spreadsheets/d/{DOCUMENT_ID}/edit",
        "https://docs.google.com/document/d/../../admin",
    ],
)
def test_google_docs_url_allowlist_rejects_ssrf_and_non_documents(url: str) -> None:
    with pytest.raises(UnsafeArtifactURL):
        parse_google_docs_url(url)


@pytest.mark.anyio
async def test_google_rejects_cross_host_redirect_observation() -> None:
    provider = GoogleDocsArtifactProvider(
        client=GoogleClient(
            source_url=f"https://docs.google.com.evil.test/document/d/{DOCUMENT_ID}/edit"
        ),
        credentials=Resolver(anonymous=True),
        handoff=Handoff(),
    )
    with pytest.raises(UnsafeArtifactURL):
        await provider.preflight(_google_preflight_request())
