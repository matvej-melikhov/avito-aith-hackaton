"""Bounded immutable GitHub/Google snapshots; never fetch an arbitrary user host."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit
from zipfile import BadZipFile, ZipFile

import httpx

from review_platform.application.workspace.ports import DefinitivePreparationFailure, PreparedBytes
from review_platform.infrastructure.providers.github_artifacts import (
    UnsafeArtifactURL,
    parse_github_url,
)
from review_platform.infrastructure.providers.google_docs_artifacts import parse_google_docs_url
from review_platform.settings import Settings


class SourcePreparer:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    async def _fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allowed_hosts: set[str],
        max_bytes: int,
    ) -> bytes:
        async with httpx.AsyncClient(
            timeout=self.settings.provider_timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            for _ in range(4):
                host = urlsplit(url).hostname or ""
                if urlsplit(url).scheme != "https" or (
                    host not in allowed_hosts
                    and not (
                        "googleusercontent.com" in allowed_hosts
                        and host.endswith(".googleusercontent.com")
                    )
                ):
                    raise DefinitivePreparationFailure(
                        "Redirect is outside the artifact provider", "source_redirect_rejected"
                    )
                # Authentication headers go only to the GitHub API, never to a redirected host.
                request_headers = headers if host == "api.github.com" else None
                async with client.stream("GET", url, headers=request_headers) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise DefinitivePreparationFailure("Missing redirect location")
                        url = str(response.url.join(location))
                        continue
                    if response.status_code in {401, 403, 404}:
                        raise DefinitivePreparationFailure(
                            "Artifact is not accessible", "artifact_access_required"
                        )
                    if response.status_code == 429 or response.status_code >= 500:
                        raise OSError("Artifact source is temporarily unavailable")
                    if response.status_code != 200:
                        raise DefinitivePreparationFailure("Artifact source rejected the request")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise DefinitivePreparationFailure(
                                "Artifact exceeds size limit", "artifact_too_large"
                            )
                    return bytes(content)
        raise DefinitivePreparationFailure("Too many artifact redirects")

    async def prepare(self, artifact_url: str) -> PreparedBytes:
        if not self.settings.live_providers_enabled:
            raise DefinitivePreparationFailure("Live sources are disabled", "source_not_configured")
        try:
            if urlsplit(artifact_url).hostname == "github.com":
                parse_github_url(artifact_url)
            else:
                parse_google_docs_url(artifact_url)
        except UnsafeArtifactURL as error:
            raise DefinitivePreparationFailure(str(error), "invalid_artifact") from error
        if urlsplit(artifact_url).hostname == "github.com":
            locator = parse_github_url(artifact_url)
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.settings.workspace_github_token:
                allowed = {
                    x.strip().lower()
                    for x in self.settings.workspace_github_repositories.split(",")
                    if x.strip()
                }
                if f"{locator.owner}/{locator.repository}".lower() not in allowed:
                    raise DefinitivePreparationFailure(
                        "Repository is not in the configured allowlist", "repository_not_allowed"
                    )
                headers["Authorization"] = (
                    "Bearer " + self.settings.workspace_github_token.get_secret_value()
                )
            prefix = (
                f"https://api.github.com/repos/{quote(locator.owner, safe='')}/"
                f"{quote(locator.repository, safe='')}"
            )
            raw = await self._fetch(
                f"{prefix}/commits/{quote(locator.ref, safe='')}",
                headers=headers,
                allowed_hosts={"api.github.com"},
                max_bytes=1_000_000,
            )
            try:
                commit = json.loads(raw)["sha"]
            except (ValueError, KeyError, TypeError) as exc:
                raise DefinitivePreparationFailure("Invalid GitHub revision") from exc
            if (
                not isinstance(commit, str)
                or len(commit) not in {40, 64}
                or any(c not in "0123456789abcdef" for c in commit)
            ):
                raise DefinitivePreparationFailure("Invalid GitHub revision")
            data = await self._fetch(
                f"{prefix}/zipball/{commit}",
                headers=headers,
                allowed_hosts={"api.github.com", "codeload.github.com"},
                max_bytes=self.settings.workspace_snapshot_max_bytes,
            )
            self._check_archive(data, document=False)
            return PreparedBytes(
                data, "application/zip", f"{locator.repository}-{commit[:12]}.zip", "github", commit
            )
        document_locator = parse_google_docs_url(artifact_url)
        data = await self._fetch(
            f"https://docs.google.com/document/d/{document_locator.document_id}/export?format=docx",
            allowed_hosts={"docs.google.com", "googleusercontent.com"},
            max_bytes=self.settings.workspace_snapshot_max_bytes,
        )
        self._check_archive(data, document=True)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        return PreparedBytes(
            data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "document.docx",
            "google_docs",
            digest,
        )

    def _check_archive(self, data: bytes, *, document: bool) -> None:
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > self.settings.github_max_files
                    or sum(e.file_size for e in entries) > self.settings.unpacked_max_bytes
                ):
                    raise DefinitivePreparationFailure(
                        "Unpacked artifact exceeds limits", "artifact_too_large"
                    )
                if any(
                    e.flag_bits & 1
                    or ".." in PurePosixPath(e.filename).parts
                    or e.filename.startswith(("/", "\\"))
                    or ((e.external_attr >> 16) & 0o170000) == 0o120000
                    for e in entries
                ):
                    raise DefinitivePreparationFailure("Unsafe archive entries")
                if document and "word/document.xml" not in archive.namelist():
                    raise DefinitivePreparationFailure("The source did not return a DOCX")
        except BadZipFile as exc:
            raise DefinitivePreparationFailure("Invalid source archive") from exc
