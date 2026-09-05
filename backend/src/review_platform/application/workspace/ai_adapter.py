"""HTTP transport for the colleague-owned AI implementation; contains no prompts."""

from __future__ import annotations

from typing import overload
from urllib.parse import urlsplit
from uuid import uuid5

import httpx
from pydantic import ValidationError

from review_platform.application.workspace.ports import DefinitivePreparationFailure, PreparedBytes
from review_platform.contracts.workspace import (
    ReviewAssistEvent,
    ReviewAssistRequest,
    ReviewAssistResult,
    ReviewerSuggestion,
    SelfReviewEvent,
    SelfReviewFinding,
    SelfReviewRequest,
    SelfReviewResult,
)
from review_platform.settings import Settings


class HTTPWorkspaceAI:
    """POST is idempotent by run/attempt; GET never silently resubmits unknown work."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.workspace_ai_url:
            raise ValueError("workspace_ai_url is required")
        parsed = urlsplit(settings.workspace_ai_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("AI endpoint must be a plain base URL")
        if parsed.scheme != "https" and not (
            settings.environment in {"local", "test"} and parsed.scheme == "http"
        ):
            raise ValueError("AI endpoint requires HTTPS outside local/test")
        if settings.environment not in {"local", "test"} and not settings.live_providers_enabled:
            raise ValueError("live AI provider is disabled")
        self.settings, self.transport = settings, transport
        self.base_url = settings.workspace_ai_url.rstrip("/")

    @overload
    async def _call(
        self, request: SelfReviewRequest, *, lookup: bool
    ) -> SelfReviewEvent | None: ...

    @overload
    async def _call(
        self, request: ReviewAssistRequest, *, lookup: bool
    ) -> ReviewAssistEvent | None: ...

    async def _call(
        self, request: SelfReviewRequest | ReviewAssistRequest, *, lookup: bool
    ) -> SelfReviewEvent | ReviewAssistEvent | None:
        kind = "self-reviews" if isinstance(request, SelfReviewRequest) else "review-assists"
        model = SelfReviewEvent if isinstance(request, SelfReviewRequest) else ReviewAssistEvent
        headers = {"Idempotency-Key": f"{request.run_id}:{request.attempt}"}
        if self.settings.workspace_ai_token:
            headers["Authorization"] = (
                "Bearer " + self.settings.workspace_ai_token.get_secret_value()
            )
        url = f"{self.base_url}/v2/{kind}/{request.run_id}"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.provider_timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    "GET" if lookup else "POST",
                    url,
                    headers=headers,
                    params={"attempt": request.attempt} if lookup else None,
                    json=None if lookup else request.model_dump(mode="json"),
                )
            if response.status_code in {202, 204} or (lookup and response.status_code == 404):
                return None
            if response.status_code != 200:
                raise OSError("AI service returned an unsuccessful response")
            if len(response.content) > 2_000_000:
                raise OSError("AI response exceeds the contract size limit")
            event = model.model_validate(response.json())
            if (event.run_id, event.attempt, event.input_fingerprint) != (
                request.run_id,
                request.attempt,
                request.input_fingerprint,
            ):
                raise OSError("AI response does not match the requested input")
            return event
        except (httpx.HTTPError, ValidationError, ValueError) as error:
            raise OSError("AI response could not be verified") from error

    async def submit(self, request: SelfReviewRequest) -> SelfReviewEvent | None:
        return await self._call(request, lookup=False)

    async def lookup(self, request: SelfReviewRequest) -> SelfReviewEvent | None:
        return await self._call(request, lookup=True)

    async def submit_assist(self, request: ReviewAssistRequest) -> ReviewAssistEvent | None:
        return await self._call(request, lookup=False)

    async def lookup_assist(self, request: ReviewAssistRequest) -> ReviewAssistEvent | None:
        return await self._call(request, lookup=True)


class FixtureWorkspaceAI:
    """Deterministic, visibly simulated outputs for explicitly enabled local fixtures."""

    async def submit(self, request: SelfReviewRequest) -> SelfReviewEvent:
        return SelfReviewEvent(
            contract_version="2.0.0",
            event_id=uuid5(request.run_id, f"fixture-{request.attempt}"),
            run_id=request.run_id,
            attempt=request.attempt,
            sequence=1,
            input_fingerprint=request.input_fingerprint,
            status="succeeded",
            result=SelfReviewResult(
                findings=[
                    SelfReviewFinding(
                        criterion_id=c.id,
                        status="needs_attention",
                        feedback=f"Демо: проверьте «{c.title}». Модель не вызывалась.",
                    )
                    for c in request.criteria
                ]
            ),
        )

    async def lookup(self, request: SelfReviewRequest) -> SelfReviewEvent:
        return await self.submit(request)

    async def submit_assist(self, request: ReviewAssistRequest) -> ReviewAssistEvent:
        return ReviewAssistEvent(
            contract_version="2.0.0",
            event_id=uuid5(request.run_id, f"fixture-{request.attempt}"),
            run_id=request.run_id,
            attempt=request.attempt,
            sequence=1,
            input_fingerprint=request.input_fingerprint,
            status="succeeded",
            result=ReviewAssistResult(
                feedback_draft=("Демо-отзыв: проверьте реализацию и тесты "
                                "перед окончательным решением."),
                suggestions=[
                    ReviewerSuggestion(
                        criterion_id=c.id,
                        status="suggested",
                        proposed_points=c.max_points / 2,
                        requirement_met=True if c.evaluate_quality else None,
                        reason="Демонстрационная рекомендация. Реальная модель не вызывалась.",
                        confidence="low",
                        evidence=["Локальный fixture"],
                        reviewer_note="Проверьте работу самостоятельно.",
                        student_feedback=None,
                    )
                    for c in request.criteria
                ],
            ),
        )

    async def lookup_assist(self, request: ReviewAssistRequest) -> ReviewAssistEvent:
        return await self.submit_assist(request)


class FixtureArtifactPreparer:
    async def prepare(self, artifact_url: str) -> PreparedBytes:
        if not artifact_url.startswith(("https://github.com/", "https://docs.google.com/")):
            raise DefinitivePreparationFailure("Fixture accepts supported source URLs only")
        return PreparedBytes(
            b"# Local fixture\nSimulated artifact, not fetched from the source.\n",
            "text/markdown",
            "fixture.md",
            source_version="fixture-v1",
        )
