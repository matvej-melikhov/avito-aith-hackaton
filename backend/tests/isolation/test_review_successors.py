"""US5 RED isolation contract for impacts and immutable review successors."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import anyio
import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Table, func, insert, select, text

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import WireCommand
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    Criterion,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import OrganizationMembership, User
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.operations import OutboxMessage
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.tasks.registry import REGISTRY, load_handler_modules
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
REVIEWER = UUID("00000000-0000-7000-8000-000000012001")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000012002")
COURSE = UUID("00000000-0000-7000-8000-000000012003")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000012004")
HOMEWORK = UUID("00000000-0000-7000-8000-000000012005")
OLD_HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000012006")
NEW_HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000012007")
OLD_CRITERION_SET = UUID("00000000-0000-7000-8000-000000012008")
NEW_CRITERION_SET = UUID("00000000-0000-7000-8000-000000012009")
OLD_MATCHING_CRITERION = UUID("00000000-0000-7000-8000-000000012010")
OLD_REMOVED_CRITERION = UUID("00000000-0000-7000-8000-000000012011")
NEW_MATCHING_CRITERION = UUID("00000000-0000-7000-8000-000000012012")
NEW_ADDED_CRITERION = UUID("00000000-0000-7000-8000-000000012013")
RELATION = UUID("00000000-0000-7000-8000-000000012014")
OLD_PUBLICATION = UUID("00000000-0000-7000-8000-000000012015")
NEW_PUBLICATION = UUID("00000000-0000-7000-8000-000000012016")
REVIEW_CASE_ID = UUID("00000000-0000-7000-8000-000000012017")
PREDECESSOR = UUID("00000000-0000-7000-8000-000000012018")
PREDECESSOR_REVISION = UUID("00000000-0000-7000-8000-000000012019")
IMPACT_MESSAGE = UUID("00000000-0000-7000-8000-000000012020")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

MIGRATION_ROUTE_TEMPLATE = (
    "/api/v1/review-iterations/{reviewIterationId}/requirements-migrations"
)
CORRECTION_ROUTE_TEMPLATE = "/api/v1/review-iterations/{reviewIterationId}/corrections"
MIGRATION_ROUTE = MIGRATION_ROUTE_TEMPLATE.replace(
    "{reviewIterationId}",
    str(PREDECESSOR),
)
CORRECTION_ROUTE = CORRECTION_ROUTE_TEMPLATE.replace(
    "{reviewIterationId}",
    str(PREDECESSOR),
)


@dataclass(slots=True)
class SuccessorHarness:
    app: FastAPI
    client: AsyncClient
    session_factory: AsyncSessionFactory


@pytest.fixture
async def successor_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[SuccessorHarness]:
    app = create_app(runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_reviewer(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield SuccessorHarness(app, client, foundation_session_factory)


async def _mysql_ready(factory: AsyncSessionFactory) -> None:
    async with factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1


def _require_route(app: FastAPI, path: str) -> None:
    routes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert (path, "POST") in routes, f"US5 successor route is not implemented: POST {path}"


def _successor_tables() -> dict[str, Table]:
    names = (
        "review_impact_event",
        "review_iteration_relation",
        "review_publication",
    )
    missing = [name for name in names if name not in Base.metadata.tables]
    assert not missing, f"US5 successor persistence tables are missing: {missing}"
    return {name: Base.metadata.tables[name] for name in names}


async def test_homework_requirements_event_is_idempotent_and_projects_affected_review(
    successor_harness: SuccessorHarness,
) -> None:
    await _mysql_ready(successor_harness.session_factory)
    await _seed_predecessor(successor_harness.session_factory, include_event=True)
    load_handler_modules()
    implemented_events = {spec.event_type for spec in REGISTRY}
    assert "HomeworkRequirementsChanged" in implemented_events, (
        "US5 HomeworkRequirementsChanged impact consumer is not implemented"
    )
    handler = REGISTRY.resolve_event("HomeworkRequirementsChanged").handler

    first = await _invoke_handler(handler, IMPACT_MESSAGE)
    replay = await _invoke_handler(handler, IMPACT_MESSAGE)
    assert first == replay

    tables = _successor_tables()
    impact = tables["review_impact_event"]
    async with successor_harness.session_factory() as session:
        rows = (
            await session.execute(
                select(impact).where(
                    impact.c.organization_id == ORG,
                    impact.c.review_iteration_id == PREDECESSOR,
                    impact.c.current_homework_version_id == NEW_HOMEWORK_VERSION,
                )
            )
        ).mappings().all()
        assert len(rows) == 1
        affected = rows[0]
        assert affected["previous_homework_version_id"] == OLD_HOMEWORK_VERSION
        assert affected["current_publication_id"] == NEW_PUBLICATION
        assert affected.get("resolved_at") is None
        predecessor = await session.get(ReviewIteration, PREDECESSOR)
        assert predecessor is not None
        assert predecessor.homework_version_id == OLD_HOMEWORK_VERSION
        assert predecessor.criterion_set_id == OLD_CRITERION_SET


async def test_requirements_migration_transfers_only_matching_keys_and_preserves_predecessor(
    successor_harness: SuccessorHarness,
) -> None:
    await _mysql_ready(successor_harness.session_factory)
    _require_route(successor_harness.app, MIGRATION_ROUTE_TEMPLATE)
    await _seed_predecessor(successor_harness.session_factory, include_event=True)
    before = await _predecessor_bytes(successor_harness.session_factory)
    command = _command(
        command_name="migrate_review_requirements",
        request_suffix=31,
        payload={
            "homework_version_id": str(NEW_HOMEWORK_VERSION),
            "criterion_set_id": str(NEW_CRITERION_SET),
        },
    )

    response = await successor_harness.client.post(MIGRATION_ROUTE, json=command)
    assert response.status_code == 201, response.text
    successor_id = UUID(response.json()["review_iteration_id"])
    tables = _successor_tables()
    relation = tables["review_iteration_relation"]
    async with successor_harness.session_factory() as session:
        successor = await session.get(ReviewIteration, successor_id)
        assert successor is not None
        assert successor.predecessor_iteration_id == PREDECESSOR
        assert successor.origin == "requirements_migration"
        assert successor.homework_version_id == NEW_HOMEWORK_VERSION
        assert successor.criterion_set_id == NEW_CRITERION_SET
        link = (
            await session.execute(
                select(relation).where(
                    relation.c.organization_id == ORG,
                    relation.c.predecessor_iteration_id == PREDECESSOR,
                    relation.c.successor_iteration_id == successor_id,
                )
            )
        ).mappings().one()
        assert link["kind"] == "requirements_migration"
        successor_revision = await session.scalar(
            select(ReviewRevision).where(
                ReviewRevision.organization_id == ORG,
                ReviewRevision.review_iteration_id == successor_id,
            )
        )
        assert successor_revision is not None
        decisions = (
            await session.scalars(
                select(ReviewCriterionDecision).where(
                    ReviewCriterionDecision.organization_id == ORG,
                    ReviewCriterionDecision.review_revision_id == successor_revision.id,
                )
            )
        ).all()
        assert [decision.criterion_id for decision in decisions] == [
            NEW_MATCHING_CRITERION
        ]
    assert await _predecessor_bytes(successor_harness.session_factory) == before


async def test_two_corrections_race_to_one_successor_without_mutating_published_bytes(
    successor_harness: SuccessorHarness,
) -> None:
    await _mysql_ready(successor_harness.session_factory)
    _require_route(successor_harness.app, CORRECTION_ROUTE_TEMPLATE)
    await _seed_predecessor(successor_harness.session_factory, include_event=False)
    before = await _predecessor_bytes(successor_harness.session_factory)
    responses: list[Response] = []

    async def correct(suffix: int) -> None:
        responses.append(
            await successor_harness.client.post(
                CORRECTION_ROUTE,
                json=_command(
                    command_name="create_review_correction",
                    request_suffix=suffix,
                    payload={
                        "published_review_revision_id": str(PREDECESSOR_REVISION),
                        "reason": f"Correction request {suffix}",
                    },
                ),
            )
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(correct, 41)
        tasks.start_soon(correct, 42)
    assert sorted(response.status_code for response in responses) == [201, 409]

    tables = _successor_tables()
    relation = tables["review_iteration_relation"]
    async with successor_harness.session_factory() as session:
        review_case = await session.get(ReviewCase, REVIEW_CASE_ID)
        assert review_case is not None
        assert review_case.current_iteration_id != PREDECESSOR
        successors = (
            await session.scalars(
                select(ReviewIteration).where(
                    ReviewIteration.organization_id == ORG,
                    ReviewIteration.review_case_id == REVIEW_CASE_ID,
                    ReviewIteration.predecessor_iteration_id == PREDECESSOR,
                    ReviewIteration.origin == "correction",
                )
            )
        ).all()
        assert len(successors) == 1
        relation_count = await session.scalar(
            select(func.count())
            .select_from(relation)
            .where(
                relation.c.organization_id == ORG,
                relation.c.predecessor_iteration_id == PREDECESSOR,
                relation.c.kind == "correction",
            )
        )
        assert relation_count == 1
    assert await _predecessor_bytes(successor_harness.session_factory) == before


async def _invoke_handler(handler: object, message_id: UUID) -> object:
    assert callable(handler), "HomeworkRequirementsChanged handler is not callable"
    result = handler(organization_id=str(ORG), message_id=str(message_id))
    if isinstance(result, Awaitable):
        return await result
    return result


def _command(
    *,
    command_name: str,
    request_suffix: int,
    payload: Mapping[str, object],
) -> dict[str, object]:
    body: dict[str, object] = {
        "request_id": f"00000000-0000-7000-8000-{12000 + request_suffix:012d}",
        "idempotency_key": f"review-successor-{request_suffix}",
        "command_name": command_name,
        "revision_target": "review_iteration",
        "target_id": str(PREDECESSOR),
        "expected_revision": 0,
        "payload": dict(payload),
    }
    WireCommand.model_validate(body)
    return body


async def _seed_predecessor(
    factory: AsyncSessionFactory,
    *,
    include_event: bool,
) -> None:
    async with session_scope(factory) as session:
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        session.add_all(
            [
                User(id=REVIEWER, display_name="Successor Reviewer", status="active"),
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=REVIEWER,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Successor Course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=COURSE_RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Successor Run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Successor Homework",
                    revision=0,
                ),
                HomeworkVersion(
                    id=OLD_HOMEWORK_VERSION,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Old requirements",
                    max_score=Decimal("10"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                HomeworkVersion(
                    id=NEW_HOMEWORK_VERSION,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=2,
                    student_text="New requirements",
                    max_score=Decimal("10"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                CriterionSet(
                    id=OLD_CRITERION_SET,
                    organization_id=ORG,
                    homework_version_id=OLD_HOMEWORK_VERSION,
                ),
                CriterionSet(
                    id=NEW_CRITERION_SET,
                    organization_id=ORG,
                    homework_version_id=NEW_HOMEWORK_VERSION,
                ),
                _criterion(
                    OLD_MATCHING_CRITERION,
                    OLD_CRITERION_SET,
                    "correctness",
                    0,
                ),
                _criterion(
                    OLD_REMOVED_CRITERION,
                    OLD_CRITERION_SET,
                    "removed",
                    1,
                ),
                _criterion(
                    NEW_MATCHING_CRITERION,
                    NEW_CRITERION_SET,
                    "correctness",
                    0,
                ),
                _criterion(
                    NEW_ADDED_CRITERION,
                    NEW_CRITERION_SET,
                    "added",
                    1,
                ),
                CourseRunHomework(
                    id=RELATION,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    current_publication_id=NEW_PUBLICATION,
                    status="active",
                    revision=2,
                ),
                CourseRunHomeworkPublication(
                    id=OLD_PUBLICATION,
                    organization_id=ORG,
                    course_run_homework_id=RELATION,
                    homework_id=HOMEWORK,
                    homework_version_id=OLD_HOMEWORK_VERSION,
                    publication_sequence=1,
                    submission_deadline=NOW + timedelta(days=1),
                    review_deadline=NOW + timedelta(days=2),
                    published_at=NOW - timedelta(days=1),
                ),
                CourseRunHomeworkPublication(
                    id=NEW_PUBLICATION,
                    organization_id=ORG,
                    course_run_homework_id=RELATION,
                    homework_id=HOMEWORK,
                    homework_version_id=NEW_HOMEWORK_VERSION,
                    publication_sequence=2,
                    submission_deadline=NOW + timedelta(days=2),
                    review_deadline=NOW + timedelta(days=3),
                    published_at=NOW,
                ),
                ReviewCase(
                    id=REVIEW_CASE_ID,
                    organization_id=ORG,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=REVIEWER,
                    current_iteration_id=PREDECESSOR,
                    revision=0,
                ),
                ReviewIteration(
                    id=PREDECESSOR,
                    organization_id=ORG,
                    review_case_id=REVIEW_CASE_ID,
                    course_run_id=COURSE_RUN,
                    homework_id=HOMEWORK,
                    student_id=REVIEWER,
                    iteration_number=1,
                    submission_version_id=UUID(
                        "00000000-0000-7000-8000-000000012101"
                    ),
                    artifact_version_id=UUID(
                        "00000000-0000-7000-8000-000000012102"
                    ),
                    homework_version_id=OLD_HOMEWORK_VERSION,
                    criterion_set_id=OLD_CRITERION_SET,
                    effective_deadline=NOW + timedelta(days=1),
                    responsible_reviewer_id=REVIEWER,
                    status="published",
                    current_revision_id=PREDECESSOR_REVISION,
                    predecessor_iteration_id=None,
                    origin="initial",
                    revision=0,
                ),
                ReviewRevision(
                    id=PREDECESSOR_REVISION,
                    organization_id=ORG,
                    review_iteration_id=PREDECESSOR,
                    revision_number=1,
                    author_user_id=REVIEWER,
                    base_revision_id=None,
                    feedback="Published predecessor feedback",
                    total_score=Decimal("8"),
                    created_at=NOW,
                ),
                ReviewCriterionDecision(
                    id=UUID("00000000-0000-7000-8000-000000012103"),
                    organization_id=ORG,
                    review_revision_id=PREDECESSOR_REVISION,
                    criterion_id=OLD_MATCHING_CRITERION,
                    ai_suggestion_id=None,
                    points=Decimal("4"),
                    decision="manual",
                    reason="Human matching decision",
                    evidence_ids=["evidence:matching"],
                ),
                ReviewCriterionDecision(
                    id=UUID("00000000-0000-7000-8000-000000012104"),
                    organization_id=ORG,
                    review_revision_id=PREDECESSOR_REVISION,
                    criterion_id=OLD_REMOVED_CRITERION,
                    ai_suggestion_id=None,
                    points=Decimal("4"),
                    decision="manual",
                    reason="Human removed decision",
                    evidence_ids=["evidence:removed"],
                ),
                ReviewNote(
                    id=UUID("00000000-0000-7000-8000-000000012105"),
                    organization_id=ORG,
                    review_revision_id=PREDECESSOR_REVISION,
                    criterion_id=None,
                    text="Immutable published note",
                    author_user_id=REVIEWER,
                    position=0,
                ),
            ]
        )
        await session.flush()
        tables = _successor_tables()
        await _insert_known(
            session,
            tables["review_publication"],
            {
                "id": UUID("00000000-0000-7000-8000-000000012106"),
                "organization_id": ORG,
                "review_iteration_id": PREDECESSOR,
                "review_revision_id": PREDECESSOR_REVISION,
                "publication_request_id": None,
                "publication_version": 1,
                "published_by": REVIEWER,
                "published_at": NOW,
                "status": "published",
                "revision": 0,
            },
        )
        if include_event:
            session.add(
                OutboxMessage(
                    message_id=IMPACT_MESSAGE,
                    organization_id=ORG,
                    aggregate_type="course_run_homework",
                    aggregate_id=RELATION,
                    event_type="HomeworkRequirementsChanged",
                    payload_version="1.1.0",
                    payload={
                        "contract_version": "1.1.0",
                        "organization_id": str(ORG),
                        "course_run_id": str(COURSE_RUN),
                        "course_run_homework_id": str(RELATION),
                        "homework_id": str(HOMEWORK),
                        "previous_homework_version_id": str(OLD_HOMEWORK_VERSION),
                        "current_homework_version_id": str(NEW_HOMEWORK_VERSION),
                        "previous_publication_id": str(OLD_PUBLICATION),
                        "current_publication_id": str(NEW_PUBLICATION),
                        "publication_sequence": 2,
                    },
                    available_at=NOW,
                    enqueue_state="enqueued",
                    attempts=1,
                    max_attempts=5,
                )
            )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _criterion(identity: UUID, criterion_set_id: UUID, key: str, position: int) -> Criterion:
    return Criterion(
        id=identity,
        organization_id=ORG,
        criterion_set_id=criterion_set_id,
        stable_key=key,
        position=position,
        title=key.title(),
        description="",
        max_points=Decimal("5"),
        active=True,
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
    assert not missing, f"T112 fixture lacks required {table.name} columns: {missing}"
    await session.execute(insert(table).values(**selected))


async def _predecessor_bytes(factory: AsyncSessionFactory) -> bytes:
    tables = _successor_tables()
    async with factory() as session:
        revision = await session.get(ReviewRevision, PREDECESSOR_REVISION)
        assert revision is not None
        decisions = (
            await session.scalars(
                select(ReviewCriterionDecision)
                .where(
                    ReviewCriterionDecision.organization_id == ORG,
                    ReviewCriterionDecision.review_revision_id == PREDECESSOR_REVISION,
                )
                .order_by(ReviewCriterionDecision.id)
            )
        ).all()
        notes = (
            await session.scalars(
                select(ReviewNote)
                .where(
                    ReviewNote.organization_id == ORG,
                    ReviewNote.review_revision_id == PREDECESSOR_REVISION,
                )
                .order_by(ReviewNote.position, ReviewNote.id)
            )
        ).all()
        publication = (
            await session.execute(
                select(tables["review_publication"]).where(
                    tables["review_publication"].c.organization_id == ORG,
                    tables["review_publication"].c.review_revision_id
                    == PREDECESSOR_REVISION,
                )
            )
        ).mappings().one()
        snapshot = {
            "revision": _json_mapping(
                {
                    "id": revision.id,
                    "review_iteration_id": revision.review_iteration_id,
                    "revision_number": revision.revision_number,
                    "author_user_id": revision.author_user_id,
                    "base_revision_id": revision.base_revision_id,
                    "feedback": revision.feedback,
                    "total_score": revision.total_score,
                    "created_at": revision.created_at,
                }
            ),
            "decisions": [
                _json_mapping(
                    {
                        "id": row.id,
                        "criterion_id": row.criterion_id,
                        "points": row.points,
                        "decision": row.decision,
                        "reason": row.reason,
                        "evidence_ids": row.evidence_ids,
                    }
                )
                for row in decisions
            ],
            "notes": [
                _json_mapping(
                    {
                        "id": row.id,
                        "criterion_id": row.criterion_id,
                        "text": row.text,
                        "author_user_id": row.author_user_id,
                        "position": row.position,
                    }
                )
                for row in notes
            ],
            "publication": _json_mapping(publication),
        }
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()


def _json_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {
        key: (
            str(value)
            if isinstance(value, UUID | datetime | Decimal)
            else cast(object, value)
        )
        for key, value in values.items()
    }
