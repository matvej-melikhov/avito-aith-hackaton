"""US4 RED integration contract for sequenced, immutable AI event ingestion."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import Table, func, insert, select, text, update

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.ports.providers import JsonValue
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import ExternalCredential, User
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.submission import (
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.providers.mocks import FrozenFixtureStore
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

EVENT_ROUTE = "/api/v1/internal/ai-review/events"
COMPONENT_TOKEN = "offline-ai-component-token"
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

ORG = UUID("00000000-0000-7000-8000-000000000001")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000000002")
SUBMISSION_VERSION_ID = UUID("00000000-0000-7000-8000-000000000003")
REVIEW_ITERATION_ID = UUID("00000000-0000-7000-8000-000000000004")
ARTIFACT_VERSION_ID = UUID("00000000-0000-7000-8000-000000000005")
HOMEWORK_VERSION_ID = UUID("00000000-0000-7000-8000-000000000006")
CRITERION_SET_ID = UUID("00000000-0000-7000-8000-000000000007")
AI_RUN_ID = UUID("00000000-0000-7000-8000-000000000060")
ATTEMPT_1 = UUID("00000000-0000-7000-8000-000000000061")
EVENT_SUCCEEDED = UUID("00000000-0000-7000-8000-000000000062")
CRITERION_ID = UUID("00000000-0000-7000-8000-000000000063")
ATTEMPT_2 = UUID("00000000-0000-7000-8000-000000000065")
FINGERPRINT = "sha256:8487b948022750a39653da4a76d721c9b16e0961903ad84ce80ca49be67fd14e"

USER = UUID("00000000-0000-7000-8000-000000009001")
COURSE = UUID("00000000-0000-7000-8000-000000009002")
HOMEWORK = UUID("00000000-0000-7000-8000-000000009003")
RELATION = UUID("00000000-0000-7000-8000-000000009004")
REFERENCE = UUID("00000000-0000-7000-8000-000000009005")
SUBMISSION = UUID("00000000-0000-7000-8000-000000009006")
REVIEW_CASE_ID = UUID("00000000-0000-7000-8000-000000009007")
CAPTURE_OPERATION = UUID("00000000-0000-7000-8000-000000009008")
ARTIFACT_CREDENTIAL = UUID("00000000-0000-7000-8000-000000009009")
AI_CREDENTIAL = UUID("00000000-0000-7000-8000-000000009010")
HUMAN_REVISION_ID = UUID("00000000-0000-7000-8000-000000009011")
HUMAN_DECISION_ID = UUID("00000000-0000-7000-8000-000000009012")
HUMAN_NOTE_ID = UUID("00000000-0000-7000-8000-000000009013")


@dataclass(slots=True)
class IngestionHarness:
    app: FastAPI
    client: AsyncClient
    session_factory: AsyncSessionFactory


@pytest.fixture
async def ingestion_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[IngestionHarness]:
    app = create_app(runtime=foundation_runtime)
    # T095 owns the component-token adapter. This server-owned fixture value
    # never enters an event body and never enables a live provider.
    app.state.ai_component_token = COMPONENT_TOKEN
    app.state.ai_component_organization_id = ORG
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield IngestionHarness(app, client, foundation_session_factory)


async def _ready(harness: IngestionHarness, *, current_attempt: int = 1) -> None:
    async with harness.session_factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1
    routes = {
        (route.path, method)
        for route in harness.app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert (EVENT_ROUTE, "POST") in routes, (
        "US4 AI event ingestion route is not implemented: "
        f"POST {EVENT_ROUTE}"
    )
    tables = _ai_tables()
    await _seed_run(harness.session_factory, tables=tables, current_attempt=current_attempt)


def _ai_tables() -> dict[str, Table]:
    names = (
        "ai_review_run",
        "ai_review_attempt",
        "ai_review_event_receipt",
        "ai_criterion_suggestion",
        "ai_signal",
        "review_revision",
        "review_criterion_decision",
        "review_note",
        "review_iteration",
    )
    missing = [name for name in names if name not in Base.metadata.tables]
    assert not missing, f"US4 ingestion persistence tables are missing: {missing}"
    return {name: Base.metadata.tables[name] for name in names}


def _event(name: str = "succeeded") -> dict[str, Any]:
    fixture = FrozenFixtureStore().load("ai-events-v1.1.0.json")
    return deepcopy(cast(dict[str, Any], fixture[name]))


async def _post(harness: IngestionHarness, event: Mapping[str, Any]) -> Response:
    return await harness.client.post(
        EVENT_ROUTE,
        headers={"Authorization": f"Bearer {COMPONENT_TOKEN}"},
        json=dict(event),
    )


async def test_identical_replay_is_noop_and_event_id_payload_collision_is_rejected(
    ingestion_harness: IngestionHarness,
) -> None:
    await _ready(ingestion_harness)
    event = _event()

    first = await _post(ingestion_harness, event)
    replay = await _post(ingestion_harness, event)
    assert first.status_code == replay.status_code == 202
    assert await _count(
        ingestion_harness.session_factory,
        "ai_review_event_receipt",
        event_id=EVENT_SUCCEEDED,
    ) == 1
    assert await _count(
        ingestion_harness.session_factory,
        "ai_criterion_suggestion",
        criterion_id=CRITERION_ID,
    ) == 1

    collision = deepcopy(event)
    collision["suggestions"][0]["reason"] = "different bytes under the same event ID"
    rejected = await _post(ingestion_harness, collision)
    assert rejected.status_code == 409
    assert await _count(
        ingestion_harness.session_factory,
        "ai_review_event_receipt",
        event_id=EVENT_SUCCEEDED,
    ) == 1


async def test_duplicate_and_out_of_order_sequence_are_rejected_atomically(
    ingestion_harness: IngestionHarness,
) -> None:
    await _ready(ingestion_harness)
    first = _partial_event(sequence=1, event_suffix=71)
    third = _partial_event(sequence=3, event_suffix=73)
    late_second = _partial_event(sequence=2, event_suffix=72)
    duplicate_third = _partial_event(sequence=3, event_suffix=74)

    first_response = await _post(ingestion_harness, first)
    assert first_response.status_code == 202, first_response.text
    assert (await _post(ingestion_harness, third)).status_code == 202
    assert (await _post(ingestion_harness, late_second)).status_code == 409
    assert (await _post(ingestion_harness, duplicate_third)).status_code == 409

    attempt = await _one(
        ingestion_harness.session_factory,
        "ai_review_attempt",
        id=ATTEMPT_1,
    )
    assert attempt["last_sequence"] == 3
    assert await _count(
        ingestion_harness.session_factory,
        "ai_review_event_receipt",
        ai_review_run_id=AI_RUN_ID,
    ) == 2


async def test_old_attempt_is_stored_without_regression_and_stale_input_is_rejected(
    ingestion_harness: IngestionHarness,
) -> None:
    await _ready(ingestion_harness, current_attempt=2)
    old = _partial_event(sequence=1, event_suffix=81)
    old_response = await _post(ingestion_harness, old)
    assert old_response.status_code == 202, old_response.text
    run = await _one(ingestion_harness.session_factory, "ai_review_run", id=AI_RUN_ID)
    assert run["current_attempt_no"] == 2
    assert run["status"] == "running"
    assert await _count(
        ingestion_harness.session_factory,
        "ai_review_event_receipt",
        event_id=UUID(str(old["event_id"])),
    ) == 1

    stale = _partial_event(sequence=1, event_suffix=82, attempt_number=2)
    stale["attempt_id"] = str(ATTEMPT_2)
    stale["input_fingerprint"] = "sha256:" + "f" * 64
    assert (await _post(ingestion_harness, stale)).status_code == 409

    unsupported = _partial_event(sequence=1, event_suffix=83, attempt_number=2)
    unsupported["attempt_id"] = str(ATTEMPT_2)
    unsupported["contract_version"] = "1.0.0"
    assert (await _post(ingestion_harness, unsupported)).status_code in {409, 422}
    current_attempt = await _one(
        ingestion_harness.session_factory,
        "ai_review_attempt",
        id=ATTEMPT_2,
    )
    assert current_attempt["last_sequence"] == 0


async def test_final_success_requires_exact_complete_criterion_coverage(
    ingestion_harness: IngestionHarness,
) -> None:
    await _ready(ingestion_harness)
    incomplete = _event()
    incomplete["event_id"] = "00000000-0000-7000-8000-000000000091"
    incomplete["criterion_coverage"]["reported_criterion_ids"] = []
    incomplete["criterion_coverage"]["complete"] = False
    assert (await _post(ingestion_harness, incomplete)).status_code in {409, 422}
    assert await _count(
        ingestion_harness.session_factory,
        "ai_review_event_receipt",
        event_id=UUID(str(incomplete["event_id"])),
    ) == 0

    duplicated = _event()
    duplicated["event_id"] = "00000000-0000-7000-8000-000000000092"
    second_suggestion = deepcopy(duplicated["suggestions"][0])
    second_suggestion["reason"] = "same criterion reported twice"
    duplicated["suggestions"].append(second_suggestion)
    assert (await _post(ingestion_harness, duplicated)).status_code in {409, 422}

    accepted = await _post(ingestion_harness, _event())
    assert accepted.status_code == 202
    run = await _one(ingestion_harness.session_factory, "ai_review_run", id=AI_RUN_ID)
    assert run["status"] == "succeeded"
    assert run["finished_at"] is not None
    assert await _count(
        ingestion_harness.session_factory,
        "ai_criterion_suggestion",
        ai_review_run_id=AI_RUN_ID,
    ) == 1


async def test_intervening_human_revision_is_byte_stable_and_ai_rows_remain_separate(
    ingestion_harness: IngestionHarness,
) -> None:
    await _ready(ingestion_harness)
    partial = _partial_event(sequence=1, event_suffix=101)
    partial_response = await _post(ingestion_harness, partial)
    assert partial_response.status_code == 202, partial_response.text
    tables = _ai_tables()
    await _insert_human_revision(ingestion_harness.session_factory, tables=tables)
    before = await _human_snapshot(ingestion_harness.session_factory, tables=tables)

    final = _event()
    final["sequence"] = 2
    final["event_id"] = "00000000-0000-7000-8000-000000000102"
    assert (await _post(ingestion_harness, final)).status_code == 202
    after = await _human_snapshot(ingestion_harness.session_factory, tables=tables)

    assert before == after
    assert await _count(
        ingestion_harness.session_factory,
        "ai_criterion_suggestion",
        ai_review_run_id=AI_RUN_ID,
    ) >= 1
    assert await _count(
        ingestion_harness.session_factory,
        "ai_signal",
        ai_review_run_id=AI_RUN_ID,
    ) == 1
    human_columns = set(tables["review_revision"].c.keys())
    assert "ai_suggestions" not in human_columns
    assert "ai_signal" not in human_columns


def _partial_event(
    *,
    sequence: int,
    event_suffix: int,
    attempt_number: int = 1,
) -> dict[str, Any]:
    event = _event()
    event["attempt_number"] = attempt_number
    event["event_id"] = f"00000000-0000-7000-8000-{event_suffix:012d}"
    event["sequence"] = sequence
    event["status"] = "partial"
    event["is_final"] = False
    event["suggestions"] = []
    event["criterion_coverage"]["reported_criterion_ids"] = []
    event["criterion_coverage"]["complete"] = False
    event["ai_signal"] = None
    event["error"] = None
    return event


async def _seed_run(
    factory: AsyncSessionFactory,
    *,
    tables: Mapping[str, Table],
    current_attempt: int,
) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=USER, display_name="AI ingestion reviewer", status="active"),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="AI ingestion course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                ExternalCredential(
                    id=ARTIFACT_CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-artifact",
                    key_id="fixture-key",
                    status="active",
                ),
                ExternalCredential(
                    id=AI_CREDENTIAL,
                    organization_id=ORG,
                    provider="ai_review",
                    binding_version=1,
                    ciphertext="encrypted-ai",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="AI ingestion run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="AI ingestion homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=ARTIFACT_CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/repository",
                    locator={"external_id": "example/repository"},
                    read_capability="available",
                    feedback_capability="available",
                    last_checked_at=NOW,
                    revision=0,
                ),
                Operation(
                    id=CAPTURE_OPERATION,
                    organization_id=ORG,
                    kind="artifact_capture",
                    input_version="ai-ingestion-fixture",
                    state="succeeded",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    finished_at=NOW,
                ),
                Operation(
                    id=AI_RUN_ID,
                    organization_id=ORG,
                    kind="ai_review",
                    input_version=f"ai-review:1.1.0:{FINGERPRINT}",
                    state="processing",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    finished_at=None,
                ),
            ]
        )
        await session.flush()
        session.add(
            HomeworkVersion(
                id=HOMEWORK_VERSION_ID,
                organization_id=ORG,
                homework_id=HOMEWORK,
                version_number=1,
                student_text="Review the immutable artifact",
                max_score=Decimal("5"),
                artifact_kinds=["github"],
                estimated_review_minutes=30,
                revision=0,
            )
        )
        session.add(
            ArtifactVersion(
                id=ARTIFACT_VERSION_ID,
                organization_id=ORG,
                artifact_reference_id=REFERENCE,
                provider_version="commit:fixture",
                content_digest="sha256:" + "0" * 64,
                object_key=f"{ORG}/{ARTIFACT_VERSION_ID}/artifact.bin",
                media_type="application/zip",
                byte_size=128,
                captured_at=NOW,
                artifact_metadata={},
            )
        )
        await session.flush()
        session.add(
            CriterionSet(
                id=CRITERION_SET_ID,
                organization_id=ORG,
                homework_version_id=HOMEWORK_VERSION_ID,
            )
        )
        session.add(
            CourseRunHomework(
                id=RELATION,
                organization_id=ORG,
                course_run_id=COURSE_RUN,
                homework_id=HOMEWORK,
                current_publication_id=None,
                status="active",
                revision=0,
            )
        )
        await session.flush()
        session.add(
            Criterion(
                id=CRITERION_ID,
                organization_id=ORG,
                criterion_set_id=CRITERION_SET_ID,
                stable_key="correctness",
                position=0,
                title="Correctness",
                description="",
                max_points=Decimal("5"),
                active=True,
            )
        )
        session.add(
            Submission(
                id=SUBMISSION,
                organization_id=ORG,
                course_run_homework_id=RELATION,
                course_run_id=COURSE_RUN,
                homework_id=HOMEWORK,
                student_id=USER,
                current_predeadline_version_id=None,
                revision=0,
            )
        )
        await session.flush()
        session.add(
            SubmissionVersion(
                id=SUBMISSION_VERSION_ID,
                organization_id=ORG,
                submission_id=SUBMISSION,
                course_run_id=COURSE_RUN,
                homework_id=HOMEWORK,
                sequence=1,
                homework_version_id=HOMEWORK_VERSION_ID,
                artifact_reference_id=REFERENCE,
                artifact_version_id=ARTIFACT_VERSION_ID,
                submitted_at=NOW,
                effective_deadline=NOW + timedelta(days=1),
                phase="before_deadline",
                status="ready",
                capture_operation_id=CAPTURE_OPERATION,
                revision=0,
            )
        )
        session.add(
            ReviewCase(
                id=REVIEW_CASE_ID,
                organization_id=ORG,
                course_run_id=COURSE_RUN,
                homework_id=HOMEWORK,
                student_id=USER,
                current_iteration_id=None,
                revision=0,
            )
        )
        await session.flush()
        session.add(
            ReviewIteration(
                id=REVIEW_ITERATION_ID,
                organization_id=ORG,
                review_case_id=REVIEW_CASE_ID,
                course_run_id=COURSE_RUN,
                homework_id=HOMEWORK,
                student_id=USER,
                iteration_number=1,
                submission_version_id=SUBMISSION_VERSION_ID,
                artifact_version_id=ARTIFACT_VERSION_ID,
                homework_version_id=HOMEWORK_VERSION_ID,
                criterion_set_id=CRITERION_SET_ID,
                effective_deadline=NOW + timedelta(days=2),
                responsible_reviewer_id=USER,
                status="in_review",
                current_revision_id=None,
                predecessor_iteration_id=None,
                origin="initial",
                revision=0,
            )
        )
        await session.flush()
        review_case = await session.get(ReviewCase, REVIEW_CASE_ID)
        assert review_case is not None
        review_case.current_iteration_id = REVIEW_ITERATION_ID
        await session.flush()
        await _insert_known(
            session,
            tables["ai_review_run"],
            {
                "id": AI_RUN_ID,
                "organization_id": ORG,
                "review_iteration_id": REVIEW_ITERATION_ID,
                "course_run_id": COURSE_RUN,
                "submission_version_id": SUBMISSION_VERSION_ID,
                "input_fingerprint": FINGERPRINT,
                "artifact_version_id": ARTIFACT_VERSION_ID,
                "content_digest": "sha256:" + "0" * 64,
                "homework_version_id": HOMEWORK_VERSION_ID,
                "homework_digest": "sha256:" + "1" * 64,
                "criterion_set_id": CRITERION_SET_ID,
                "criteria_digest": "sha256:" + "2" * 64,
                "contract_version": "1.1.0",
                "fingerprint_algorithm": "jcs-sha256-v1",
                "status": "running",
                "current_attempt_no": current_attempt,
                "created_at": NOW,
                "finished_at": None,
                "revision": 0,
            },
        )
        await _insert_attempt(session, tables["ai_review_attempt"], ATTEMPT_1, 1)
        if current_attempt == 2:
            await _insert_attempt(session, tables["ai_review_attempt"], ATTEMPT_2, 2)


async def _insert_attempt(
    session: Any,
    table: Table,
    attempt_id: UUID,
    attempt_number: int,
) -> None:
    await _insert_known(
        session,
        table,
        {
            "id": attempt_id,
            "organization_id": ORG,
            "ai_review_run_id": AI_RUN_ID,
            "run_id": AI_RUN_ID,
            "attempt_number": attempt_number,
            "credential_binding_id": AI_CREDENTIAL,
            "credential_binding_version": 1,
            "status": "running",
            "last_sequence": 0,
            "started_at": NOW,
            "finished_at": None,
            "error_code": None,
            "sanitized_error": None,
            "revision": 0,
        },
    )


async def _insert_human_revision(
    factory: AsyncSessionFactory,
    *,
    tables: Mapping[str, Table],
) -> None:
    async with session_scope(factory) as session:
        await _insert_known(
            session,
            tables["review_revision"],
            {
                "id": HUMAN_REVISION_ID,
                "organization_id": ORG,
                "review_iteration_id": REVIEW_ITERATION_ID,
                "revision_number": 1,
                "author_user_id": USER,
                "base_revision_id": None,
                "feedback": "Human feedback must remain immutable",
                "total_score": Decimal("4"),
                "created_at": NOW + timedelta(minutes=1),
            },
        )
        await _insert_known(
            session,
            tables["review_criterion_decision"],
            {
                "id": HUMAN_DECISION_ID,
                "organization_id": ORG,
                "review_revision_id": HUMAN_REVISION_ID,
                "criterion_id": CRITERION_ID,
                "ai_suggestion_id": None,
                "points": Decimal("4"),
                "decision": "manual",
                "reason": "Human judgment",
                "evidence_ids": [],
                "created_at": NOW + timedelta(minutes=1),
            },
        )
        await _insert_known(
            session,
            tables["review_note"],
            {
                "id": HUMAN_NOTE_ID,
                "organization_id": ORG,
                "review_revision_id": HUMAN_REVISION_ID,
                "criterion_id": None,
                "text": "Human-only note",
                "author_user_id": USER,
                "position": 0,
                "created_at": NOW + timedelta(minutes=1),
            },
        )
        iteration = tables["review_iteration"]
        iteration_columns = set(iteration.c.keys())
        if "current_revision_id" in iteration_columns:
            await session.execute(
                update(iteration)
                .where(
                    iteration.c.organization_id == ORG,
                    iteration.c.id == REVIEW_ITERATION_ID,
                )
                .values(current_revision_id=HUMAN_REVISION_ID)
            )


async def _insert_known(
    session: Any,
    table: Table,
    values: Mapping[str, object],
) -> None:
    columns = set(table.c.keys())
    selected = {name: value for name, value in values.items() if name in columns}
    missing = [
        column.name
        for column in table.c
        if not column.nullable
        and column.default is None
        and column.server_default is None
        and not column.autoincrement
        and column.name not in selected
    ]
    assert not missing, f"T094 fixture lacks required {table.name} columns: {missing}"
    await session.execute(insert(table).values(**selected))


async def _count(
    factory: AsyncSessionFactory,
    table_name: str,
    **filters: object,
) -> int:
    table = _ai_tables()[table_name]
    statement = select(func.count()).select_from(table)
    columns = set(table.c.keys())
    for name, value in filters.items():
        assert name in columns, f"{table_name} lacks required column {name}"
        statement = statement.where(table.c[name] == value)
    async with factory() as session:
        return int(await session.scalar(statement) or 0)


async def _one(
    factory: AsyncSessionFactory,
    table_name: str,
    **filters: object,
) -> Mapping[str, Any]:
    table = _ai_tables()[table_name]
    statement = select(table)
    columns = set(table.c.keys())
    for name, value in filters.items():
        assert name in columns, f"{table_name} lacks required column {name}"
        statement = statement.where(table.c[name] == value)
    async with factory() as session:
        return cast(Mapping[str, Any], (await session.execute(statement)).mappings().one())


async def _human_snapshot(
    factory: AsyncSessionFactory,
    *,
    tables: Mapping[str, Table],
) -> bytes:
    snapshot: dict[str, list[dict[str, JsonValue]]] = {}
    async with factory() as session:
        for name, identity_field, identity in (
            ("review_revision", "id", HUMAN_REVISION_ID),
            ("review_criterion_decision", "review_revision_id", HUMAN_REVISION_ID),
            ("review_note", "review_revision_id", HUMAN_REVISION_ID),
        ):
            table = tables[name]
            rows = (
                await session.execute(
                    select(table).where(table.c[identity_field] == identity)
                )
            ).mappings().all()
            snapshot[name] = [_json_row(row) for row in rows]
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()


def _json_row(row: Mapping[str, object]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in row.items():
        if isinstance(value, UUID | datetime | Decimal):
            result[key] = str(value)
        elif isinstance(value, str | int | float | bool) or value is None:
            result[key] = value
        elif isinstance(value, list | dict):
            result[key] = cast(JsonValue, value)
        else:
            result[key] = str(value)
    return result
