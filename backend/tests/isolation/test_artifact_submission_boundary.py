"""US3 tenant, artifact, submission, and ReviewCase isolation specifications."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKeyConstraint, Table, UniqueConstraint, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.foundation_runtime import BoundaryViolation, FoundationRuntime
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course, CourseRun
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
STUDENT_A = UUID("00000000-0000-7000-8000-000000002001")
STUDENT_B = UUID("00000000-0000-7000-8000-000000002002")
COURSE_A = UUID("00000000-0000-7000-8000-000000002011")
COURSE_B = UUID("00000000-0000-7000-8000-000000002012")
RUN_A1 = UUID("00000000-0000-7000-8000-000000002021")
RUN_A2 = UUID("00000000-0000-7000-8000-000000002022")
RUN_B = UUID("00000000-0000-7000-8000-000000002023")
HOMEWORK_A = UUID("00000000-0000-7000-8000-000000002031")
HOMEWORK_B = UUID("00000000-0000-7000-8000-000000002032")
HOMEWORK_VERSION_A = UUID("00000000-0000-7000-8000-000000002041")
HOMEWORK_VERSION_B = UUID("00000000-0000-7000-8000-000000002042")
RELATION_A1 = UUID("00000000-0000-7000-8000-000000002051")
RELATION_A2 = UUID("00000000-0000-7000-8000-000000002052")
RELATION_B = UUID("00000000-0000-7000-8000-000000002053")
CREDENTIAL_A = UUID("00000000-0000-7000-8000-000000002054")
CREDENTIAL_B = UUID("00000000-0000-7000-8000-000000002055")
REFERENCE_A = UUID("00000000-0000-7000-8000-000000002061")
REFERENCE_B = UUID("00000000-0000-7000-8000-000000002062")
ARTIFACT_VERSION_A = UUID("00000000-0000-7000-8000-000000002071")
SUBMISSION_A1 = UUID("00000000-0000-7000-8000-000000002081")
SUBMISSION_A2 = UUID("00000000-0000-7000-8000-000000002082")
SUBMISSION_B = UUID("00000000-0000-7000-8000-000000002083")
SUBMISSION_VERSION_A = UUID("00000000-0000-7000-8000-000000002091")
REVIEW_CASE_A1 = UUID("00000000-0000-7000-8000-000000002101")
REVIEW_CASE_A2 = UUID("00000000-0000-7000-8000-000000002102")
OPERATION_A = UUID("00000000-0000-7000-8000-000000002111")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64

SUBMISSION_ROUTE = "/api/v1/submissions/{submissionId}"
SUBMIT_ROUTE = "/api/v1/submissions/{submissionId}/versions"


def _required_tables() -> dict[str, Table]:
    names = (
        "artifact_reference",
        "artifact_version",
        "submission",
        "submission_version",
        "review_case",
    )
    missing = [name for name in names if name not in Base.metadata.tables]
    assert not missing, f"missing required US3 table(s): {', '.join(missing)}"
    return {name: Base.metadata.tables[name] for name in names}


def _unique_keys(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_keys(table: Table) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    return tuple(
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )


def _has_tenant_fk(table: Table, target_table: str) -> bool:
    return any(
        "organization_id" in local
        and any(target.startswith(f"{target_table}.") for target in remote)
        and f"{target_table}.organization_id" in remote
        for local, remote in _foreign_keys(table)
    )


def _require_route(app: FastAPI, *, path: str, method: str) -> None:
    routes = {
        (route.path, candidate)
        for route in app.routes
        for candidate in (getattr(route, "methods", None) or set())
    }
    assert (path, method.upper()) in routes, (
        f"missing required US3 route: {method.upper()} {path}"
    )


async def _insert_row(
    session: AsyncSession,
    table: Table,
    values: Mapping[str, object],
) -> None:
    required_identity = {"id", "organization_id"}
    column_names = set(table.c.keys())
    assert required_identity <= column_names, (
        f"{table.name} lacks tenant candidate identity {sorted(required_identity)}"
    )
    missing = [
        column.name
        for column in table.c
        if not column.nullable
        and column.default is None
        and column.server_default is None
        and not column.autoincrement
        and column.name not in values
    ]
    assert not missing, f"test fixture lacks required {table.name} columns: {missing}"
    await session.execute(
        insert(table).values(
            **{
                key: value
                for key, value in values.items()
                if key in column_names
            }
        )
    )


async def _seed_parent_graph(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=STUDENT_A, display_name="Student A", status="active"),
                User(id=STUDENT_B, display_name="Student B", status="active"),
            ]
        )
        await session.flush()
        session.add(
            OrganizationMembership(
                id=UUID("00000000-0000-7000-8000-000000002121"),
                organization_id=ORG_B,
                user_id=STUDENT_B,
                roles=["student"],
                status="active",
                revision=0,
                auth_epoch=0,
            )
        )
        session.add_all(
            [
                ExternalCredential(
                    id=CREDENTIAL_A,
                    organization_id=ORG_A,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-org-a",
                    key_id="fixture-key",
                    status="active",
                ),
                ExternalCredential(
                    id=CREDENTIAL_B,
                    organization_id=ORG_B,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-org-b",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        session.add_all(
            [
                _course(ORG_A, COURSE_A),
                _course(ORG_B, COURSE_B),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _run(ORG_A, COURSE_A, RUN_A1),
                _run(ORG_A, COURSE_A, RUN_A2),
                _run(ORG_B, COURSE_B, RUN_B),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _homework(ORG_A, COURSE_A, HOMEWORK_A),
                _homework(ORG_B, COURSE_B, HOMEWORK_B),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _homework_version(ORG_A, HOMEWORK_A, HOMEWORK_VERSION_A),
                _homework_version(ORG_B, HOMEWORK_B, HOMEWORK_VERSION_B),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _relation(ORG_A, RUN_A1, HOMEWORK_A, RELATION_A1),
                _relation(ORG_A, RUN_A2, HOMEWORK_A, RELATION_A2),
                _relation(ORG_B, RUN_B, HOMEWORK_B, RELATION_B),
            ]
        )
        session.add(
            Operation(
                id=OPERATION_A,
                organization_id=ORG_A,
                kind="artifact_capture",
                input_version="artifact-reference:1",
                state="pending",
                revision=0,
                created_at=NOW,
                updated_at=NOW,
                finished_at=None,
                error_code=None,
                sanitized_error=None,
            )
        )


def _course(organization_id: UUID, course_id: UUID) -> Course:
    return Course(
        id=course_id,
        organization_id=organization_id,
        title=f"Course {course_id}",
        description="",
        source_kind="standalone",
        status="active",
        revision=0,
    )


def _run(organization_id: UUID, course_id: UUID, run_id: UUID) -> CourseRun:
    return CourseRun(
        id=run_id,
        organization_id=organization_id,
        course_id=course_id,
        external_run_id=None,
        title=f"Run {run_id}",
        starts_at=None,
        ends_at=None,
        timezone="Europe/Moscow",
        status="active",
        revision=0,
    )


def _homework(organization_id: UUID, course_id: UUID, homework_id: UUID) -> Homework:
    return Homework(
        id=homework_id,
        organization_id=organization_id,
        course_id=course_id,
        title=f"Homework {homework_id}",
        revision=0,
    )


def _homework_version(
    organization_id: UUID,
    homework_id: UUID,
    version_id: UUID,
) -> HomeworkVersion:
    return HomeworkVersion(
        id=version_id,
        organization_id=organization_id,
        homework_id=homework_id,
        version_number=1,
        student_text="Requirements",
        max_score=Decimal("10"),
        artifact_kinds=["github"],
        estimated_review_minutes=30,
        revision=0,
    )


def _relation(
    organization_id: UUID,
    run_id: UUID,
    homework_id: UUID,
    relation_id: UUID,
) -> CourseRunHomework:
    return CourseRunHomework(
        id=relation_id,
        organization_id=organization_id,
        course_run_id=run_id,
        homework_id=homework_id,
        current_publication_id=None,
        status="active",
        revision=0,
    )


def _artifact_reference_values(
    *,
    organization_id: UUID,
    reference_id: UUID,
    credential_id: UUID,
) -> dict[str, object]:
    return {
        "id": reference_id,
        "organization_id": organization_id,
        "provider": "github",
        "credential_binding_id": credential_id,
        "credential_binding_version": 1,
        "original_url": "https://github.com/example/repository",
        "url": "https://github.com/example/repository",
        "locator": {
            "canonical_url": "https://github.com/example/repository",
            "external_id": "example/repository",
        },
        "read_capability": "available",
        "feedback_capability": "available",
        "capability_status": "available",
        "last_checked_at": NOW,
        "revision": 0,
    }


def _artifact_version_values(
    *,
    organization_id: UUID,
    version_id: UUID,
    reference_id: UUID,
) -> dict[str, object]:
    return {
        "id": version_id,
        "organization_id": organization_id,
        "artifact_reference_id": reference_id,
        "reference_id": reference_id,
        "provider_version": "commit:abc",
        "content_digest": DIGEST,
        "object_key": f"{organization_id}/{version_id}/artifact.zip",
        "media_type": "application/zip",
        "byte_size": 128,
        "captured_at": NOW,
        "metadata": {"source": "offline-fixture"},
        "provider_metadata": {"source": "offline-fixture"},
        "revision": 0,
    }


def _submission_values(
    *,
    organization_id: UUID,
    submission_id: UUID,
    run_id: UUID,
    homework_id: UUID,
    relation_id: UUID,
    student_id: UUID,
) -> dict[str, object]:
    return {
        "id": submission_id,
        "organization_id": organization_id,
        "course_run_id": run_id,
        "homework_id": homework_id,
        "course_run_homework_id": relation_id,
        "student_id": student_id,
        "current_predeadline_version_id": None,
        "revision": 0,
    }


def _submission_version_values(
    *,
    organization_id: UUID,
    submission_id: UUID,
    reference_id: UUID,
    artifact_version_id: UUID,
    homework_version_id: UUID,
    run_id: UUID,
    homework_id: UUID,
) -> dict[str, object]:
    return {
        "id": SUBMISSION_VERSION_A,
        "organization_id": organization_id,
        "submission_id": submission_id,
        "course_run_id": run_id,
        "homework_id": homework_id,
        "sequence": 1,
        "homework_version_id": homework_version_id,
        "artifact_reference_id": reference_id,
        "artifact_version_id": artifact_version_id,
        "submitted_at": NOW,
        "effective_deadline": NOW + timedelta(days=1),
        "phase": "before_deadline",
        "status": "ready",
        "revision": 0,
        "capture_operation_id": OPERATION_A,
    }


def _review_case_values(
    *,
    organization_id: UUID,
    review_case_id: UUID,
    submission_id: UUID,
    run_id: UUID,
    homework_id: UUID,
    student_id: UUID,
) -> dict[str, object]:
    return {
        "id": review_case_id,
        "organization_id": organization_id,
        "submission_id": submission_id,
        "course_run_id": run_id,
        "homework_id": homework_id,
        "student_id": student_id,
        "current_iteration_id": None,
        "revision": 0,
    }


async def _seed_us3_rows(factory: AsyncSessionFactory) -> dict[str, Table]:
    tables = _required_tables()
    await _seed_parent_graph(factory)
    async with session_scope(factory) as session:
        await _insert_row(
            session,
            tables["artifact_reference"],
            _artifact_reference_values(
                organization_id=ORG_A,
                reference_id=REFERENCE_A,
                credential_id=CREDENTIAL_A,
            ),
        )
        await _insert_row(
            session,
            tables["artifact_reference"],
            _artifact_reference_values(
                organization_id=ORG_B,
                reference_id=REFERENCE_B,
                credential_id=CREDENTIAL_B,
            ),
        )
        await _insert_row(
            session,
            tables["artifact_version"],
            _artifact_version_values(
                organization_id=ORG_A,
                version_id=ARTIFACT_VERSION_A,
                reference_id=REFERENCE_A,
            ),
        )
        await _insert_row(
            session,
            tables["submission"],
            _submission_values(
                organization_id=ORG_A,
                submission_id=SUBMISSION_A1,
                run_id=RUN_A1,
                homework_id=HOMEWORK_A,
                relation_id=RELATION_A1,
                student_id=STUDENT_A,
            ),
        )
        await _insert_row(
            session,
            tables["submission"],
            _submission_values(
                organization_id=ORG_B,
                submission_id=SUBMISSION_B,
                run_id=RUN_B,
                homework_id=HOMEWORK_B,
                relation_id=RELATION_B,
                student_id=STUDENT_B,
            ),
        )
    return tables


def test_us3_schema_declares_tenant_candidate_keys_and_composite_links() -> None:
    tables = _required_tables()
    for name in tables:
        assert ("organization_id", "id") in _unique_keys(tables[name]), name
    assert _has_tenant_fk(tables["artifact_version"], "artifact_reference")
    assert _has_tenant_fk(tables["submission_version"], "submission")
    assert _has_tenant_fk(tables["submission_version"], "artifact_reference")
    assert _has_tenant_fk(tables["review_case"], "course_run")
    assert _has_tenant_fk(tables["review_case"], "homework")
    exact_identity = ("organization_id", "course_run_id", "homework_id", "student_id")
    assert exact_identity in _unique_keys(tables["submission"])
    assert exact_identity in _unique_keys(tables["review_case"])


async def test_mysql_rejects_cross_tenant_artifact_submission_and_review_links(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    tables = await _seed_us3_rows(foundation_session_factory)
    async with foundation_session_factory() as session:
        invalid_rows = (
            (
                tables["artifact_reference"],
                _artifact_reference_values(
                    organization_id=ORG_B,
                    reference_id=UUID("00000000-0000-7000-8000-000000002063"),
                    credential_id=CREDENTIAL_A,
                ),
            ),
            (
                tables["artifact_version"],
                _artifact_version_values(
                    organization_id=ORG_B,
                    version_id=UUID("00000000-0000-7000-8000-000000002072"),
                    reference_id=REFERENCE_A,
                ),
            ),
            (
                tables["submission_version"],
                _submission_version_values(
                    organization_id=ORG_B,
                    submission_id=SUBMISSION_A1,
                    reference_id=REFERENCE_A,
                    artifact_version_id=ARTIFACT_VERSION_A,
                    homework_version_id=HOMEWORK_VERSION_A,
                    run_id=RUN_A1,
                    homework_id=HOMEWORK_A,
                ),
            ),
            (
                tables["review_case"],
                _review_case_values(
                    organization_id=ORG_B,
                    review_case_id=UUID("00000000-0000-7000-8000-000000002103"),
                    submission_id=SUBMISSION_A1,
                    run_id=RUN_A1,
                    homework_id=HOMEWORK_A,
                    student_id=STUDENT_A,
                ),
            ),
        )
        for table, values in invalid_rows:
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await _insert_row(session, table, values)
        await session.rollback()


@asynccontextmanager
async def _tenant_client(
    app: FastAPI,
    actor: RequestActor,
) -> AsyncIterator[AsyncClient]:
    @app.middleware("http")
    async def inject_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


async def test_opaque_artifact_reference_cannot_submit_or_read_across_tenant(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    app = create_app(runtime=foundation_runtime)
    _require_route(app, path=SUBMISSION_ROUTE, method="GET")
    _require_route(app, path=SUBMIT_ROUTE, method="POST")
    await _seed_us3_rows(foundation_session_factory)
    actor = RequestActor.user(
        organization_id=ORG_B,
        user_id=STUDENT_B,
        roles={"student"},
        membership_revision=0,
        auth_epoch=0,
    )
    command = {
        "request_id": "00000000-0000-7000-8000-000000002131",
        "idempotency_key": "cross-tenant-submit-0001",
        "command_name": "submit_work",
        "revision_target": "submission",
        "target_id": str(SUBMISSION_B),
        "expected_revision": 0,
        "payload": {"artifact_reference_id": str(REFERENCE_A)},
    }
    async with _tenant_client(app, actor) as client:
        foreign_read = await client.get(
            SUBMISSION_ROUTE.replace("{submissionId}", str(SUBMISSION_A1))
        )
        foreign_submit = await client.post(
            SUBMIT_ROUTE.replace("{submissionId}", str(SUBMISSION_B)),
            json=command,
        )
    assert foreign_read.status_code == 409
    assert foreign_read.json()["code"] == "command_conflict"
    assert foreign_submit.status_code in {403, 404, 409, 422}
    assert not 200 <= foreign_submit.status_code < 300


async def test_s3_key_and_signed_read_are_tenant_and_artifact_version_scoped(
    foundation_runtime: FoundationRuntime,
) -> None:
    key = foundation_runtime.s3_key(
        organization_id=str(ORG_A),
        artifact_version_id=str(ARTIFACT_VERSION_A),
    )
    assert key.startswith(f"{ORG_A}/{ARTIFACT_VERSION_A}/")
    assert str(ORG_B) not in key
    signed = foundation_runtime.sign_artifact_read(
        organization_id=str(ORG_A),
        artifact_version_id=str(ARTIFACT_VERSION_A),
        requested_by_organization_id=str(ORG_A),
    )
    assert signed
    with pytest.raises(BoundaryViolation, match="organization"):
        foundation_runtime.sign_artifact_read(
            organization_id=str(ORG_A),
            artifact_version_id=str(ARTIFACT_VERSION_A),
            requested_by_organization_id=str(ORG_B),
        )


async def test_same_student_homework_in_two_runs_has_independent_submission_and_review_case(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    tables = _required_tables()
    await _seed_parent_graph(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        submissions = (
            _submission_values(
                organization_id=ORG_A,
                submission_id=SUBMISSION_A1,
                run_id=RUN_A1,
                homework_id=HOMEWORK_A,
                relation_id=RELATION_A1,
                student_id=STUDENT_A,
            ),
            _submission_values(
                organization_id=ORG_A,
                submission_id=SUBMISSION_A2,
                run_id=RUN_A2,
                homework_id=HOMEWORK_A,
                relation_id=RELATION_A2,
                student_id=STUDENT_A,
            ),
        )
        review_cases = (
            _review_case_values(
                organization_id=ORG_A,
                review_case_id=REVIEW_CASE_A1,
                submission_id=SUBMISSION_A1,
                run_id=RUN_A1,
                homework_id=HOMEWORK_A,
                student_id=STUDENT_A,
            ),
            _review_case_values(
                organization_id=ORG_A,
                review_case_id=REVIEW_CASE_A2,
                submission_id=SUBMISSION_A2,
                run_id=RUN_A2,
                homework_id=HOMEWORK_A,
                student_id=STUDENT_A,
            ),
        )
        for values in submissions:
            await _insert_row(session, tables["submission"], values)
        for values in review_cases:
            await _insert_row(session, tables["review_case"], values)

        duplicate_submission = dict(submissions[0])
        duplicate_submission["id"] = UUID(
            "00000000-0000-7000-8000-000000002084"
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _insert_row(session, tables["submission"], duplicate_submission)
        duplicate_case = dict(review_cases[0])
        duplicate_case["id"] = UUID("00000000-0000-7000-8000-000000002104")
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _insert_row(session, tables["review_case"], duplicate_case)

        submission_count = await session.scalar(
            select(func.count()).select_from(tables["submission"])
        )
        review_case_count = await session.scalar(
            select(func.count()).select_from(tables["review_case"])
        )
        assert submission_count == review_case_count == 2
