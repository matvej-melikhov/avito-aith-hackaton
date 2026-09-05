from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import httpx
import pytest
from fastapi.routing import APIRoute
from tests.support.contracts import load_openapi, validator_for

from review_platform.application.projections.review_detail import project_ai_review
from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewHistory
from review_platform.main import create_app
from review_platform.settings import Settings

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_ai_routes_are_exact_and_component_boundaries_require_bearer() -> None:
    app = create_app(Settings())
    operations = {
        (route.path, method): route.operation_id
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert (
        operations[("/api/v1/review-iterations/{reviewIterationId}/ai-review", "POST")]
        == "startAIReview"
    )
    assert operations[("/api/v1/internal/ai-review/events", "POST")] == "acceptAIReviewEvent"
    assert (
        operations[("/api/v1/internal/artifacts/{artifactVersionId}/content", "GET")]
        == "downloadArtifactVersion"
    )


@pytest.mark.anyio
async def test_component_routes_reject_missing_bearer_before_composition() -> None:
    app = create_app(Settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        event = await client.post("/api/v1/internal/ai-review/events", json={})
        artifact = await client.get(
            "/api/v1/internal/artifacts/00000000-0000-7000-8000-000000001501/content"
        )

    assert event.status_code == artifact.status_code == 401
    assert set(event.json()) == {"code", "message", "action"}
    assert set(artifact.json()) == {"code", "message", "action"}


def test_canonical_ai_projection_validates_against_frozen_review_detail_member() -> None:
    run = SimpleNamespace(
        id=UUID("00000000-0000-7000-8000-000000001502"),
        input_fingerprint="sha256:" + "1" * 64,
        contract_version="1.1.0",
        status="succeeded",
        current_attempt_no=1,
    )
    attempt = SimpleNamespace(
        attempt_number=1,
        status="succeeded",
        started_at=NOW,
        finished_at=NOW,
        sanitized_error=None,
    )
    suggestion = SimpleNamespace(
        id=UUID("00000000-0000-7000-8000-000000001503"),
        criterion_id=UUID("00000000-0000-7000-8000-000000001504"),
        status="suggested",
        proposed_points=Decimal("4.5"),
        reason="Evidence supports the suggestion",
        evidence=[{"locator": "line:1", "quote": "evidence", "verified": True}],
        confidence="high",
        reviewer_note=None,
        student_feedback="Good work",
        flags=[],
    )
    signal = SimpleNamespace(
        level="low",
        evidence=[],
        limitations=["One file unavailable"],
        questions=[],
    )
    history = cast(
        AIReviewHistory,
        SimpleNamespace(
            run=run,
            attempts=(attempt,),
            events=(),
            suggestions=(suggestion,),
            signals=(signal,),
        ),
    )
    projected = project_ai_review(history)
    assert projected is not None
    openapi = load_openapi()
    validator_for(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/components/schemas/AIReviewSummary",
            "components": openapi["components"],
        }
    ).validate(projected)

    assert projected["suggestions"][0]["criterion_id"] == str(suggestion.criterion_id)
    assert projected["signal"]["level"] == "low"
