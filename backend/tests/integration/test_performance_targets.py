"""Measured local-backend latency gates for the documented SC thresholds."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from uuid import UUID

import anyio
import httpx
import pytest
from fastapi import FastAPI, Request, Response
from tests.unit.test_review_work_repositories import (
    MEMBERSHIP_A,
    NOW,
    ORG_A,
    REVIEWER_A,
    RUN_ACTIVE,
    _seed_scope,
)

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.models import (
    Operation,
    ReviewerCourseSelection,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

MUTATION_LIMIT_SECONDS = 1.0
RECOMMENDATION_LIMIT_SECONDS = 2.0
OPERATION_VISIBILITY_LIMIT_SECONDS = 5.0
MEASURED_REPETITIONS = 5


@dataclass(slots=True)
class PerformanceHarness:
    app: FastAPI
    client: httpx.AsyncClient
    factory: AsyncSessionFactory


@pytest.fixture
async def performance_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[PerformanceHarness]:
    # Reuse the already executable tenant-valid recommendation graph. The
    # performance sample still runs through production routes and repositories.
    await _seed_scope(foundation_session_factory, full_candidate=True)
    async with session_scope(foundation_session_factory) as session:
        session.add(
            ReviewerCourseSelection(
                id=UUID("00000000-0000-7000-8000-000000165001"),
                organization_id=ORG_A,
                course_run_id=RUN_ACTIVE,
                reviewer_id=REVIEWER_A,
                active=True,
            )
        )
        session.add(
            Operation(
                id=UUID("00000000-0000-7000-8000-000000165002"),
                organization_id=ORG_A,
                kind="course_import",
                input_version="performance-warmup",
                state="succeeded",
                revision=0,
                created_at=NOW,
                updated_at=NOW,
                finished_at=NOW,
            )
        )
    actor = RequestActor.user(
        organization_id=ORG_A,
        user_id=REVIEWER_A,
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=0,
    )
    app = create_app(runtime=foundation_runtime)

    @app.middleware("http")
    async def inject_reviewer(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://performance.test",
    ) as client:
        yield PerformanceHarness(app, client, foundation_session_factory)


async def test_representative_mutations_complete_under_one_second(
    performance_harness: PerformanceHarness,
) -> None:
    warmup = await _availability_mutation(performance_harness.client, suffix=0)
    assert warmup.status_code == 200, warmup.text
    durations: list[float] = []
    for repetition in range(1, MEASURED_REPETITIONS + 1):
        started = perf_counter()
        response = await _availability_mutation(
            performance_harness.client,
            suffix=repetition,
        )
        durations.append(perf_counter() - started)
        assert response.status_code == 200, response.text

    _assert_latency(
        "representative mutation",
        durations,
        MUTATION_LIMIT_SECONDS,
    )


async def test_recommendation_completes_under_two_seconds(
    performance_harness: PerformanceHarness,
) -> None:
    path = f"/api/v1/review-queue/next?course_run_id={RUN_ACTIVE}"
    warmup = await performance_harness.client.get(path)
    assert warmup.status_code == 200, warmup.text
    assert warmup.json() is not None

    durations: list[float] = []
    for _ in range(MEASURED_REPETITIONS):
        started = perf_counter()
        response = await performance_harness.client.get(path)
        durations.append(perf_counter() - started)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result is not None
        assert UUID(result["review_case_id"])
        assert UUID(result["submission_version_id"])

    _assert_latency(
        "recommendation",
        durations,
        RECOMMENDATION_LIMIT_SECONDS,
    )


async def test_operation_changes_are_visible_under_five_seconds(
    performance_harness: PerformanceHarness,
) -> None:
    warmup_id = UUID("00000000-0000-7000-8000-000000165002")
    warmup = await performance_harness.client.get(f"/api/v1/operations/{warmup_id}")
    assert warmup.status_code == 200, warmup.text

    durations: list[float] = []
    for repetition in range(1, 4):
        operation_id = UUID(f"00000000-0000-7000-8000-{165010 + repetition:012d}")
        started = perf_counter()
        async with session_scope(performance_harness.factory) as session:
            session.add(
                Operation(
                    id=operation_id,
                    organization_id=ORG_A,
                    kind="course_import",
                    input_version=f"performance-visible-{repetition}",
                    state="pending",
                    revision=0,
                    created_at=NOW + timedelta(seconds=repetition),
                    updated_at=NOW + timedelta(seconds=repetition),
                )
            )
        response = await _poll_operation(
            performance_harness.client,
            operation_id,
            timeout_seconds=OPERATION_VISIBILITY_LIMIT_SECONDS,
        )
        durations.append(perf_counter() - started)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "pending"

    _assert_latency(
        "operation visibility",
        durations,
        OPERATION_VISIBILITY_LIMIT_SECONDS,
    )


async def _availability_mutation(
    client: httpx.AsyncClient,
    *,
    suffix: int,
) -> httpx.Response:
    return await client.put(
        "/api/v1/reviewer/availability",
        json={
            "request_id": f"00000000-0000-7000-8000-{165100 + suffix:012d}",
            "idempotency_key": f"performance-availability-{suffix:04d}",
            "command_name": "set_reviewer_availability",
            "revision_target": "membership",
            "target_id": str(MEMBERSHIP_A),
            "expected_revision": 3,
            "payload": {
                "planned_minutes": 120 + suffix,
                "until_at": (NOW + timedelta(days=30)).isoformat(),
            },
        },
    )


async def _poll_operation(
    client: httpx.AsyncClient,
    operation_id: UUID,
    *,
    timeout_seconds: float,
) -> httpx.Response:
    deadline = perf_counter() + timeout_seconds
    response = await client.get(f"/api/v1/operations/{operation_id}")
    while response.status_code == 404 and perf_counter() < deadline:
        await anyio.sleep(0.01)
        response = await client.get(f"/api/v1/operations/{operation_id}")
    return response


def _assert_latency(label: str, durations: list[float], limit: float) -> None:
    rendered = ", ".join(f"{duration:.6f}s" for duration in durations)
    maximum = max(durations)
    assert maximum < limit, (
        f"{label} exceeded {limit:.3f}s; max={maximum:.6f}s; durations=[{rendered}]"
    )
