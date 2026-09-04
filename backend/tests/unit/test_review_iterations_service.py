"""Explicit-open behavior for immutable-input ReviewIterations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import anyio
import pytest

from review_platform.application.request_context import RequestActor
from review_platform.application.services.review_iterations import (
    OpenReviewIterationCommand,
    ReviewIterationConflict,
    ReviewIterationInvalidInput,
    ReviewIterationPermissionDenied,
    ReviewIterationService,
)
from review_platform.infrastructure.db.models import (
    ArtifactVersion,
    CourseRun,
    CriterionSet,
    HomeworkVersion,
    ReviewCase,
    ReviewIteration,
    Submission,
    SubmissionVersion,
)

ORG = UUID("00000000-0000-7000-8000-000000000901")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000902")
USER = UUID("00000000-0000-7000-8000-000000000903")
RUN = UUID("00000000-0000-7000-8000-000000000904")
HOMEWORK = UUID("00000000-0000-7000-8000-000000000905")
SUBMISSION = UUID("00000000-0000-7000-8000-000000000906")
VERSION = UUID("00000000-0000-7000-8000-000000000907")
ARTIFACT_REFERENCE = UUID("00000000-0000-7000-8000-000000000908")
ARTIFACT = UUID("00000000-0000-7000-8000-000000000909")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000000910")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000000911")
CASE = UUID("00000000-0000-7000-8000-000000000912")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self, *, status: str = "ready", artifact: bool = True) -> None:
        self.lock = anyio.Lock()
        self.course_run = CourseRun(
            id=RUN,
            organization_id=ORG,
            course_id=UUID("00000000-0000-7000-8000-000000000920"),
            title="Run",
            timezone="UTC",
            status="active",
        )
        self.submission = Submission(
            id=SUBMISSION,
            organization_id=ORG,
            course_run_homework_id=UUID("00000000-0000-7000-8000-000000000921"),
            course_run_id=RUN,
            homework_id=HOMEWORK,
            student_id=USER,
            revision=0,
        )
        self.artifact = ArtifactVersion(
            id=ARTIFACT,
            organization_id=ORG,
            artifact_reference_id=ARTIFACT_REFERENCE,
            provider_version="v1",
            content_digest="sha256:" + "5" * 64,
            object_key=f"{ORG}/{ARTIFACT}/content",
            media_type="application/zip",
            byte_size=128,
            captured_at=NOW,
            artifact_metadata={},
        )
        self.homework_version = HomeworkVersion(
            id=HOMEWORK_VERSION,
            organization_id=ORG,
            homework_id=HOMEWORK,
            version_number=1,
            student_text="Requirements",
            max_score=Decimal("10.00"),
            artifact_kinds=["github"],
            estimated_review_minutes=30,
        )
        self.criterion_set = CriterionSet(
            id=CRITERION_SET,
            organization_id=ORG,
            homework_version_id=HOMEWORK_VERSION,
        )
        self.versions = {
            VERSION: SubmissionVersion(
                id=VERSION,
                organization_id=ORG,
                submission_id=SUBMISSION,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                sequence=1,
                homework_version_id=HOMEWORK_VERSION,
                artifact_reference_id=ARTIFACT_REFERENCE,
                artifact_version_id=ARTIFACT if artifact else None,
                submitted_at=NOW,
                effective_deadline=NOW + timedelta(days=1),
                phase="before_deadline",
                status=status,
                revision=0,
            )
        }
        self.review_case: ReviewCase | None = None
        self.iterations: list[ReviewIteration] = []
        self.force_cas_failure = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[object]:
        async with self.lock:
            yield self

    async def get_submission_version(
        self, organization_id: UUID, submission_version_id: UUID, *, for_update: bool
    ) -> SubmissionVersion | None:
        del for_update
        row = self.versions.get(submission_version_id)
        return row if row is not None and row.organization_id == organization_id else None

    async def get_submission(
        self, organization_id: UUID, submission_id: UUID, *, for_update: bool
    ) -> Submission | None:
        del for_update
        return self.submission if organization_id == ORG and submission_id == SUBMISSION else None

    async def get_course_run(
        self, organization_id: UUID, course_run_id: UUID, *, for_update: bool
    ) -> CourseRun | None:
        del for_update
        return self.course_run if organization_id == ORG and course_run_id == RUN else None

    async def get_artifact_version(
        self, organization_id: UUID, artifact_version_id: UUID
    ) -> ArtifactVersion | None:
        return self.artifact if organization_id == ORG and artifact_version_id == ARTIFACT else None

    async def get_homework_version(
        self, organization_id: UUID, homework_version_id: UUID
    ) -> HomeworkVersion | None:
        return (
            self.homework_version
            if organization_id == ORG and homework_version_id == HOMEWORK_VERSION
            else None
        )

    async def get_criterion_set_for_version(
        self, organization_id: UUID, homework_version_id: UUID
    ) -> CriterionSet | None:
        return (
            self.criterion_set
            if organization_id == ORG and homework_version_id == HOMEWORK_VERSION
            else None
        )

    async def lock_or_create_case(self, candidate: ReviewCase) -> ReviewCase:
        if self.review_case is None:
            self.review_case = candidate
        return self.review_case

    async def list_iterations(
        self, organization_id: UUID, review_case_id: UUID
    ) -> Sequence[ReviewIteration]:
        return tuple(
            row
            for row in self.iterations
            if row.organization_id == organization_id and row.review_case_id == review_case_id
        )

    async def find_by_submission_version(
        self, organization_id: UUID, review_case_id: UUID, submission_version_id: UUID
    ) -> ReviewIteration | None:
        return next(
            (
                row
                for row in self.iterations
                if row.organization_id == organization_id
                and row.review_case_id == review_case_id
                and row.submission_version_id == submission_version_id
            ),
            None,
        )

    async def add_iteration(self, iteration: ReviewIteration) -> ReviewIteration:
        self.iterations.append(iteration)
        return iteration

    async def compare_and_set_current(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        expected_revision: int,
        expected_current_iteration_id: UUID | None,
        new_iteration_id: UUID,
    ) -> bool:
        case = self.review_case
        if (
            self.force_cas_failure
            or case is None
            or case.organization_id != organization_id
            or case.id != review_case_id
            or case.revision != expected_revision
            or case.current_iteration_id != expected_current_iteration_id
        ):
            return False
        case.current_iteration_id = new_iteration_id
        case.revision += 1
        return True


class FakeAuthorization:
    def __init__(self) -> None:
        self.initial = 0
        self.final = 0

    async def authorize_open(
        self, *, actor: RequestActor, organization_id: UUID, transaction: object
    ) -> None:
        del actor, organization_id, transaction
        self.initial += 1

    async def revalidate_for_commit(self, *, actor: RequestActor, transaction: object) -> None:
        del actor, transaction
        self.final += 1


class FakeAudit:
    def __init__(self) -> None:
        self.iterations: list[UUID] = []

    async def record_open(self, **values: object) -> None:
        iteration = values["iteration"]
        assert isinstance(iteration, ReviewIteration)
        self.iterations.append(iteration.id)


def _actor(*, organization_id: UUID = ORG, roles: list[str] | None = None) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=USER,
        roles=roles or ["reviewer"],
        membership_revision=0,
        auth_epoch=0,
    )


def _command(*, version: UUID = VERSION, expected_revision: int = 0) -> OpenReviewIterationCommand:
    return OpenReviewIterationCommand(
        organization_id=ORG,
        review_case_id=CASE,
        submission_version_id=version,
        expected_review_case_revision=expected_revision,
        request_id=UUID("00000000-0000-7000-8000-000000000930"),
        trace_id=UUID("00000000-0000-7000-8000-000000000931"),
    )


def _service(
    repository: FakeRepository,
    authorization: FakeAuthorization | None = None,
    audit: FakeAudit | None = None,
) -> tuple[ReviewIterationService, FakeAuthorization, FakeAudit]:
    auth = authorization or FakeAuthorization()
    recorder = audit or FakeAudit()
    ids = iter(
        [UUID("00000000-0000-7000-8000-000000000940"), UUID("00000000-0000-7000-8000-000000000941")]
    )
    return (
        ReviewIterationService(
            repository=repository, authorization=auth, audit=recorder, id_factory=lambda: next(ids)
        ),
        auth,
        recorder,
    )


@pytest.mark.anyio
async def test_explicit_open_captures_exact_inputs_and_late_version_waits_for_command() -> None:
    repository = FakeRepository(status="pending_review")
    service, auth, audit = _service(repository)
    assert repository.iterations == []
    async with repository.transaction() as transaction:
        result = await service.open(_command(), actor=_actor(), transaction=transaction)
    iteration = repository.iterations[0]
    assert (result.origin, result.replayed, result.review_case_revision) == ("initial", False, 1)
    assert (
        iteration.submission_version_id,
        iteration.artifact_version_id,
        iteration.homework_version_id,
        iteration.criterion_set_id,
        iteration.effective_deadline,
    ) == (
        VERSION,
        ARTIFACT,
        HOMEWORK_VERSION,
        CRITERION_SET,
        repository.versions[VERSION].effective_deadline,
    )
    assert auth.initial == auth.final == 1
    assert audit.iterations == [iteration.id]


@pytest.mark.anyio
async def test_wrong_tenant_role_archived_run_and_missing_artifact_fail_closed() -> None:
    repository = FakeRepository()
    service, _, _ = _service(repository)
    with pytest.raises(ReviewIterationPermissionDenied):
        await service.open(
            _command(), actor=_actor(organization_id=OTHER_ORG), transaction=object()
        )
    with pytest.raises(ReviewIterationPermissionDenied):
        await service.open(_command(), actor=_actor(roles=["student"]), transaction=object())
    repository.course_run.status = "archived"
    with pytest.raises(ReviewIterationConflict, match="archived"):
        await service.open(_command(), actor=_actor(), transaction=object())
    repository.course_run.status = "active"
    repository.versions[VERSION].artifact_version_id = None
    with pytest.raises(ReviewIterationInvalidInput, match="ArtifactVersion"):
        await service.open(_command(), actor=_actor(), transaction=object())


@pytest.mark.anyio
async def test_stale_current_pointer_cas_rejects_without_a_second_current() -> None:
    repository = FakeRepository()
    repository.force_cas_failure = True
    service, _, _ = _service(repository)
    with pytest.raises(ReviewIterationConflict, match="changed concurrently"):
        await service.open(_command(), actor=_actor(), transaction=object())
    assert repository.review_case is not None
    assert repository.review_case.current_iteration_id is None


@pytest.mark.anyio
async def test_concurrent_duplicate_open_creates_one_iteration_and_stable_replay() -> None:
    repository = FakeRepository()
    service, _, _ = _service(repository)
    results = []

    async def open_once() -> None:
        async with repository.transaction() as transaction:
            results.append(await service.open(_command(), actor=_actor(), transaction=transaction))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(open_once)
        tasks.start_soon(open_once)
    assert len(repository.iterations) == 1
    assert sorted(result.replayed for result in results) == [False, True]
    assert len({result.review_iteration_id for result in results}) == 1


@pytest.mark.anyio
async def test_resubmission_advances_current_without_mutating_predecessor() -> None:
    repository = FakeRepository()
    service, _, _ = _service(repository)
    async with repository.transaction() as transaction:
        first = await service.open(_command(), actor=_actor(), transaction=transaction)
    predecessor = repository.iterations[0]
    snapshot = (
        predecessor.submission_version_id,
        predecessor.artifact_version_id,
        predecessor.homework_version_id,
        predecessor.criterion_set_id,
    )
    second_version = UUID("00000000-0000-7000-8000-000000000950")
    repository.versions[second_version] = SubmissionVersion(
        **{
            column.name: getattr(repository.versions[VERSION], column.name)
            for column in SubmissionVersion.__table__.columns
            if column.name not in {"id", "sequence", "status", "created_at", "updated_at"}
        },
        id=second_version,
        sequence=2,
        status="pending_review",
    )
    async with repository.transaction() as transaction:
        second = await service.open(
            _command(version=second_version, expected_revision=1),
            actor=_actor(),
            transaction=transaction,
        )
    assert (second.origin, second.iteration_number) == ("resubmission", 2)
    assert repository.iterations[1].predecessor_iteration_id == first.review_iteration_id
    assert (
        predecessor.submission_version_id,
        predecessor.artifact_version_id,
        predecessor.homework_version_id,
        predecessor.criterion_set_id,
    ) == snapshot
