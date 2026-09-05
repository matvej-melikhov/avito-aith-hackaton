"""Prepared sources are immutable and shared by optional checks and submission."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from review_platform.application.workspace.ports import DefinitivePreparationFailure
from review_platform.application.workspace.sources import SourcePreparer
from review_platform.settings import Settings


def archive(name: str, content: bytes = b"work") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as value:
        value.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.anyio
async def test_github_resolves_commit_before_download_and_never_forwards_token_to_codeload():
    commit = "a" * 40
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": commit})
        if request.url.host == "api.github.com":
            assert request.url.path.endswith(commit)
            return httpx.Response(
                302,
                headers={
                    "location": f"https://codeload.github.com/example/work/legacy.zip/{commit}"
                },
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, content=archive("work/README.md"))

    settings = Settings(
        live_providers_enabled=True,
        workspace_github_token="test-token",
        workspace_github_repositories="example/work",
    )
    result = await SourcePreparer(settings, transport=httpx.MockTransport(handler)).prepare(
        "https://github.com/example/work"
    )
    assert result.source_version == commit and result.source_kind == "github"
    assert len(seen) == 3 and seen[0].headers["authorization"] == "Bearer test-token"


@pytest.mark.anyio
async def test_redirect_to_arbitrary_host_is_rejected_before_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://private.example/internal"})

    with pytest.raises(DefinitivePreparationFailure, match="outside"):
        await SourcePreparer(
            Settings(live_providers_enabled=True), transport=httpx.MockTransport(handler)
        ).prepare("https://docs.google.com/document/d/example_document_identifier_123456789/edit")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_document_export_is_validated_not_a_successful_html_login_page():
    with pytest.raises(DefinitivePreparationFailure, match="archive"):
        await SourcePreparer(
            Settings(live_providers_enabled=True),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"<html>Login</html>")
            ),
        ).prepare("https://docs.google.com/document/d/example_document_identifier_123456789/edit")


def test_zip_path_traversal_is_rejected():
    with pytest.raises(DefinitivePreparationFailure, match="Unsafe"):
        SourcePreparer(Settings())._check_archive(archive("../outside"), document=False)
