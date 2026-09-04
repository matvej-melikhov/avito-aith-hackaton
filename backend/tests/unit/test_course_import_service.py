"""MySQL-backed acceptance tests for resumable course import orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

import pytest
from sqlalchemy import func, select
from testcontainers.mysql import MySqlContainer

from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.services.course_import import (
    CourseImportBoundaryError,
    CourseImportCommand,
    CourseImportRepositories,
    CourseImportService,
)
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    Course,
    CourseMembership,
    CourseRun,
    ExternalCourseBinding,
    ExternalCredential,
    ExternalIdentity,
    Operation,
    Organization,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.providers.mocks import (
    FixtureCourseImportProvider,
    FrozenFixtureStore,
    JsonSchemaPayloadValidator,
)

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
OPERATION_ID = UUID("00000000-0000-7000-8000-000000000010")
CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000011")
OTHER_CREDENTIAL_ID = UUID("00000000-0000-7000-8000-000000000012")


@pytest.fixture
async def course_import_database(
    mysql_container: MySqlContainer,
) -> AsyncIterator[AsyncSessionFactory]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORG_A, slug="import-org-a", name="Import Org A"),
                Organization(id=ORG_B, slug="import-org-b", name="Import Org B"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ExternalCredential(
                    id=CREDENTIAL_ID,
                    organization_id=ORG_A,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-credential-a-v1",
                    key_id="key-a",
                ),
                ExternalCredential(
                    id=OTHER_CREDENTIAL_ID,
                    organization_id=ORG_A,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-credential-a-v2",
                    key_id="key-a",
                ),
            ]
        )
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _command(
    *,
    operation_id: UUID = OPERATION_ID,
    organization_id: UUID = ORG_A,
    credential_id: UUID = CREDENTIAL_ID,
    cursor: str | None = None,
) -> CourseImportCommand:
    return CourseImportCommand(
        organization_id=organization_id,
        operation_id=operation_id,
        credential_binding_id=credential_id,
        credential_binding_version=1,
        provider="stepik",
        external_url="https://stepik.org/course/1",
        cursor=cursor,
        page_size=100,
        course_binding_version=1,
        worker_identity="course-import-test-worker",
    )


def _service(
    provider: object,
    repositories: CourseImportRepositories,
    uuid7_factory: object,
    fixed_clock: object,
) -> CourseImportService:
    return CourseImportService(
        provider=cast(AnyCourseImportProvider, provider),
        validator=JsonSchemaPayloadValidator(),
        repositories=repositories,
        id_factory=cast(UUIDFactory, uuid7_factory),
        clock=cast(Clock, fixed_clock),
    )


type UUIDFactory = Callable[[], UUID]
type Clock = Callable[[], datetime]


class AnyCourseImportProvider(Protocol):
    @property
    def contract_version(self) -> str: ...

    @property
    def schema_name(self) -> str: ...

    async def import_course_page(self, request: ProviderPayload) -> ProviderPayload: ...


@dataclass(slots=True)
class SequenceCourseImportProvider:
    results: list[ProviderPayload]
    expected_cursors: list[str | None]
    requests: list[ProviderPayload] = field(default_factory=list)
    contract_version: str = "1.1.0"
    schema_name: str = "course-import.schema.json"

    async def import_course_page(self, request: ProviderPayload) -> ProviderPayload:
        call_index = len(self.requests)
        assert request["cursor"] == self.expected_cursors[call_index]
        self.requests.append(deepcopy(dict(request)))
        return deepcopy(dict(self.results[call_index]))


def _result(
    fixture: Mapping[str, ProviderPayload],
    key: str,
    *,
    outcome: str | None = None,
    complete: bool | None = None,
    next_cursor: str | None = None,
    organization_id: UUID = ORG_A,
    operation_id: UUID = OPERATION_ID,
) -> ProviderPayload:
    result = deepcopy(dict(fixture[key]))
    result["organization_id"] = str(organization_id)
    result["operation_id"] = str(operation_id)
    if outcome is not None:
        result["outcome"] = outcome
    if complete is not None:
        result["complete"] = complete
    result["next_cursor"] = next_cursor
    return cast(ProviderPayload, result)


async def test_success_is_idempotent_and_persists_exact_provenance(
    course_import_database: AsyncSessionFactory,
    uuid7_factory: UUIDFactory,
    fixed_clock: Clock,
) -> None:
    provider = FixtureCourseImportProvider()
    async with session_scope(course_import_database) as session:
        service = _service(
            provider,
            CourseImportRepositories.from_session(session),
            uuid7_factory,
            fixed_clock,
        )
        first = await service.execute(_command())
        replay = await service.execute(_command())

        assert first.state == replay.state == "succeeded"
        assert first.operation_id == replay.operation_id == OPERATION_ID
        assert first.input_version.startswith("course-import:1.1.0:sha256:")
        assert [attempt.state for attempt in first.attempts] == ["succeeded"]

    async with session_scope(course_import_database) as session:
        assert await session.scalar(select(func.count()).select_from(Course)) == 1
        assert await session.scalar(select(func.count()).select_from(CourseRun)) == 1
        assert await session.scalar(select(func.count()).select_from(CourseMembership)) == 1
        assert await session.scalar(select(func.count()).select_from(ExternalIdentity)) == 1
        operation = await session.scalar(select(Operation).where(Operation.id == OPERATION_ID))
        binding = await session.scalar(select(ExternalCourseBinding))
        assert operation is not None
        assert len(operation.attempts) == 1
        assert binding is not None
        assert (
            binding.organization_id,
            binding.credential_id,
            binding.credential_binding_version,
            binding.provider_version,
        ) == (ORG_A, CREDENTIAL_ID, 1, "fixture-1")


async def test_retry_partial_resume_and_repeated_roster_do_not_duplicate_rows(
    course_import_database: AsyncSessionFactory,
    uuid7_factory: UUIDFactory,
    fixed_clock: Clock,
) -> None:
    fixture = FrozenFixtureStore().load("course-import-v1.1.0.json")
    partial = _result(
        fixture,
        "success_result",
        outcome="partial",
        complete=False,
        next_cursor="page-2",
    )
    provider = SequenceCourseImportProvider(
        results=[
            _result(fixture, "failure_result"),
            partial,
            _result(fixture, "success_result"),
        ],
        expected_cursors=[None, None, "page-2"],
    )

    async with session_scope(course_import_database) as session:
        service = _service(
            provider,
            CourseImportRepositories.from_session(session),
            uuid7_factory,
            fixed_clock,
        )
        failed = await service.execute(_command())
        partial_view = await service.execute(_command())
        completed = await service.execute(_command(cursor=partial_view.next_cursor))
        replay = await service.execute(_command(cursor=partial_view.next_cursor))

        assert failed.state == "retryable_failed"
        assert failed.error == {
            "code": "provider_unavailable",
            "message": "Provider unavailable",
            "retryable": True,
            "action": None,
        }
        assert (partial_view.state, partial_view.complete, partial_view.next_cursor) == (
            "partial",
            False,
            "page-2",
        )
        assert completed.state == replay.state == "succeeded"
        assert [attempt.attempt_number for attempt in completed.attempts] == [1, 2, 3]
        assert [attempt.state for attempt in completed.attempts] == [
            "retryable_failed",
            "succeeded",
            "succeeded",
        ]
        assert len(provider.requests) == 3

    async with session_scope(course_import_database) as session:
        assert await session.scalar(select(func.count()).select_from(Course)) == 1
        assert await session.scalar(select(func.count()).select_from(CourseRun)) == 1
        assert await session.scalar(select(func.count()).select_from(CourseMembership)) == 1
        assert await session.scalar(select(func.count()).select_from(ExternalIdentity)) == 1


async def test_cross_tenant_and_exact_binding_mismatches_fail_closed(
    course_import_database: AsyncSessionFactory,
    uuid7_factory: UUIDFactory,
    fixed_clock: Clock,
) -> None:
    async with session_scope(course_import_database) as session:
        service = _service(
            FixtureCourseImportProvider(),
            CourseImportRepositories.from_session(session),
            uuid7_factory,
            fixed_clock,
        )
        with pytest.raises(CourseImportBoundaryError, match="exact active credential"):
            await service.execute(_command(organization_id=ORG_B))
        assert await session.scalar(select(func.count()).select_from(Operation)) == 0

        await service.execute(_command())

    other_operation = UUID("00000000-0000-7000-8000-000000000020")
    fixture = FrozenFixtureStore().load("course-import-v1.1.0.json")
    provider = SequenceCourseImportProvider(
        results=[_result(fixture, "success_result", operation_id=other_operation)],
        expected_cursors=[None],
    )
    async with session_scope(course_import_database) as session:
        service = _service(
            provider,
            CourseImportRepositories.from_session(session),
            uuid7_factory,
            fixed_clock,
        )
        with pytest.raises(CourseImportBoundaryError, match="different exact credential"):
            await service.execute(
                _command(operation_id=other_operation, credential_id=OTHER_CREDENTIAL_ID)
            )


async def test_provider_result_from_another_tenant_becomes_action_required(
    course_import_database: AsyncSessionFactory,
    uuid7_factory: UUIDFactory,
    fixed_clock: Clock,
) -> None:
    fixture = FrozenFixtureStore().load("course-import-v1.1.0.json")
    provider = SequenceCourseImportProvider(
        results=[_result(fixture, "success_result", organization_id=ORG_B)],
        expected_cursors=[None],
    )
    async with session_scope(course_import_database) as session:
        view = await _service(
            provider,
            CourseImportRepositories.from_session(session),
            uuid7_factory,
            fixed_clock,
        ).execute(_command())

        assert view.state == "action_required"
        assert view.error is not None
        assert view.error["code"] == "course_import_contract_error"
        assert view.attempts[0].state == "action_required"
        assert await session.scalar(select(func.count()).select_from(Course)) == 0


async def test_external_run_identity_survives_duplicate_display_and_rename(
    course_import_database: AsyncSessionFactory,
    uuid7_factory: UUIDFactory,
    fixed_clock: Clock,
) -> None:
    fixture = FrozenFixtureStore().load("course-import-v1.1.0.json")
    first_page = deepcopy(dict(_result(fixture, "success_result")))
    first_page["outcome"] = "partial"
    first_page["complete"] = False
    first_page["next_cursor"] = "rename-page"
    first_course = cast(dict[str, object], first_page["course"])
    first_run = cast(dict[str, object], first_course["runs"][0])
    first_run["title"] = "Shared Display Name"
    second_run = deepcopy(first_run)
    second_run["external_run_id"] = "run-2"
    first_course["runs"] = [first_run, second_run]
    first_membership = cast(dict[str, object], first_page["memberships"][0])
    second_membership = deepcopy(first_membership)
    second_membership["external_run_id"] = "run-2"
    second_membership["external_user_id"] = "student-2"
    second_membership["display_name"] = "Student Two"
    first_page["memberships"] = [first_membership, second_membership]

    final_page = deepcopy(first_page)
    final_page["outcome"] = "succeeded"
    final_page["complete"] = True
    final_page["next_cursor"] = None
    final_course = cast(dict[str, object], final_page["course"])
    renamed_run = cast(dict[str, object], final_course["runs"][0])
    renamed_run["title"] = "Renamed First Run"

    provider = SequenceCourseImportProvider(
        results=[cast(ProviderPayload, first_page), cast(ProviderPayload, final_page)],
        expected_cursors=[None, "rename-page"],
    )
    async with session_scope(course_import_database) as session:
        service = _service(
            provider,
            CourseImportRepositories.from_session(session),
            uuid7_factory,
            fixed_clock,
        )
        partial = await service.execute(_command())
        initial_runs = {
            run.external_run_id: run.id
            for run in (await session.execute(select(CourseRun))).scalars()
        }
        assert len(initial_runs) == 2

        completed = await service.execute(_command(cursor=partial.next_cursor))
        persisted_runs = list((await session.execute(select(CourseRun))).scalars())
        final_runs = {run.external_run_id: run.id for run in persisted_runs}

        assert completed.state == "succeeded"
        assert final_runs == initial_runs
        assert {run.title for run in persisted_runs} == {
            "Renamed First Run",
            "Shared Display Name",
        }
        assert await session.scalar(select(func.count()).select_from(CourseMembership)) == 2
        assert await session.scalar(select(func.count()).select_from(ExternalIdentity)) == 2
