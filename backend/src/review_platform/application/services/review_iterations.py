"""Explicit creation of immutable-input ReviewIterations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import utc_now, uuid7
from review_platform.infrastructure.db.models.homework import CriterionSet, HomeworkVersion
from review_platform.infrastructure.db.models.learning import CourseRun
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.submission import (
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)


class ReviewIterationError(RuntimeError):
    """Base explicit-open failure."""


class ReviewIterationPermissionDenied(ReviewIterationError):
    """Actor cannot open an iteration for this tenant."""


class ReviewIterationNotFound(ReviewIterationError):
    """A selected tenant-scoped input is absent."""


class ReviewIterationConflict(ReviewIterationError):
    """Current pointer, revision, or immutable scope changed."""


class ReviewIterationInvalidInput(ReviewIterationError):
    """Selected version lacks complete immutable review provenance."""


@dataclass(frozen=True, slots=True)
class OpenReviewIterationCommand:
    organization_id: UUID
    review_case_id: UUID
    submission_version_id: UUID
    expected_review_case_revision: int
    request_id: UUID
    trace_id: UUID


@dataclass(frozen=True, slots=True)
class OpenReviewIterationResult:
    review_case_id: UUID
    review_case_revision: int
    review_iteration_id: UUID
    submission_version_id: UUID
    iteration_number: int
    origin: str
    replayed: bool


class ReviewIterationRepository(Protocol):
    async def get_submission_version(
        self, organization_id: UUID, submission_version_id: UUID, *, for_update: bool
    ) -> SubmissionVersion | None: ...

    async def get_submission(
        self, organization_id: UUID, submission_id: UUID, *, for_update: bool
    ) -> Submission | None: ...

    async def get_course_run(
        self, organization_id: UUID, course_run_id: UUID, *, for_update: bool
    ) -> CourseRun | None: ...

    async def get_artifact_version(
        self, organization_id: UUID, artifact_version_id: UUID
    ) -> ArtifactVersion | None: ...

    async def get_homework_version(
        self, organization_id: UUID, homework_version_id: UUID
    ) -> HomeworkVersion | None: ...

    async def get_criterion_set_for_version(
        self, organization_id: UUID, homework_version_id: UUID
    ) -> CriterionSet | None: ...

    async def lock_or_create_case(self, candidate: ReviewCase) -> ReviewCase: ...

    async def list_iterations(
        self, organization_id: UUID, review_case_id: UUID
    ) -> Sequence[ReviewIteration]: ...

    async def find_by_submission_version(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        submission_version_id: UUID,
    ) -> ReviewIteration | None: ...

    async def add_iteration(self, iteration: ReviewIteration) -> ReviewIteration: ...

    async def compare_and_set_current(
        self,
        organization_id: UUID,
        review_case_id: UUID,
        *,
        expected_revision: int,
        expected_current_iteration_id: UUID | None,
        new_iteration_id: UUID,
    ) -> bool: ...


class ReviewIterationAuthorizationPort(Protocol):
    async def authorize_open(
        self, *, actor: RequestActor, organization_id: UUID, transaction: object
    ) -> None: ...

    async def revalidate_for_commit(self, *, actor: RequestActor, transaction: object) -> None: ...


class ReviewIterationAuditPort(Protocol):
    async def record_open(
        self,
        *,
        actor: RequestActor,
        iteration: ReviewIteration,
        before_revision: int,
        after_revision: int,
        request_id: UUID,
        trace_id: UUID,
        transaction: object,
    ) -> None: ...


class ReviewIterationService:
    """Open only the selected immutable SubmissionVersion, never automatically."""

    def __init__(
        self,
        *,
        repository: ReviewIterationRepository,
        authorization: ReviewIterationAuthorizationPort,
        audit: ReviewIterationAuditPort,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], object] = utc_now,
    ) -> None:
        self._repository = repository
        self._authorization = authorization
        self._audit = audit
        self._id_factory = id_factory
        self._clock = clock

    async def open(
        self,
        command: OpenReviewIterationCommand,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> OpenReviewIterationResult:
        self._validate_actor(command, actor)
        await self._authorization.authorize_open(
            actor=actor,
            organization_id=command.organization_id,
            transaction=transaction,
        )
        selected = await self._repository.get_submission_version(
            command.organization_id,
            command.submission_version_id,
            for_update=True,
        )
        if selected is None:
            raise ReviewIterationNotFound("selected tenant SubmissionVersion was not found")
        submission = await self._repository.get_submission(
            command.organization_id,
            selected.submission_id,
            for_update=True,
        )
        if submission is None:
            raise ReviewIterationNotFound("selected SubmissionVersion has no tenant Submission")
        self._validate_selected_scope(selected, submission)
        course_run = await self._repository.get_course_run(
            command.organization_id,
            selected.course_run_id,
            for_update=True,
        )
        if course_run is None:
            raise ReviewIterationNotFound("selected CourseRun was not found")
        if course_run.status == "archived":
            raise ReviewIterationConflict("archived CourseRun cannot open a review iteration")
        artifact, homework_version, criterion_set = await self._immutable_inputs(
            command.organization_id,
            selected,
        )
        candidate = ReviewCase(
            id=command.review_case_id,
            organization_id=command.organization_id,
            course_run_id=submission.course_run_id,
            homework_id=submission.homework_id,
            student_id=submission.student_id,
            current_iteration_id=None,
            revision=0,
        )
        review_case = await self._repository.lock_or_create_case(candidate)
        if review_case.id != command.review_case_id:
            raise ReviewIterationConflict("review-case scope already has another stable identity")
        self._validate_case_scope(review_case, submission)
        existing = await self._repository.find_by_submission_version(
            command.organization_id,
            review_case.id,
            selected.id,
        )
        if existing is not None:
            if review_case.current_iteration_id != existing.id:
                raise ReviewIterationConflict(
                    "selected version already has a non-current iteration"
                )
            await self._authorization.revalidate_for_commit(
                actor=actor,
                transaction=transaction,
            )
            return _result(review_case, existing, replayed=True)
        if review_case.revision != command.expected_review_case_revision:
            raise ReviewIterationConflict(
                "expected ReviewCase revision does not match the locked current revision"
            )
        iterations = tuple(
            await self._repository.list_iterations(
                command.organization_id,
                review_case.id,
            )
        )
        current_id = review_case.current_iteration_id
        iteration = ReviewIteration(
            id=self._id_factory(),
            organization_id=command.organization_id,
            review_case_id=review_case.id,
            course_run_id=submission.course_run_id,
            homework_id=submission.homework_id,
            student_id=submission.student_id,
            iteration_number=max((row.iteration_number for row in iterations), default=0) + 1,
            submission_version_id=selected.id,
            artifact_version_id=artifact.id,
            homework_version_id=homework_version.id,
            criterion_set_id=criterion_set.id,
            effective_deadline=selected.effective_deadline,
            responsible_reviewer_id=actor.user_id,
            status="in_review",
            current_revision_id=None,
            predecessor_iteration_id=current_id,
            origin="initial" if not iterations else "resubmission",
            revision=0,
        )
        await self._repository.add_iteration(iteration)
        updated = await self._repository.compare_and_set_current(
            command.organization_id,
            review_case.id,
            expected_revision=command.expected_review_case_revision,
            expected_current_iteration_id=current_id,
            new_iteration_id=iteration.id,
        )
        if not updated:
            raise ReviewIterationConflict("ReviewCase current pointer changed concurrently")
        review_case.current_iteration_id = iteration.id
        review_case.revision = command.expected_review_case_revision + 1
        await self._audit.record_open(
            actor=actor,
            iteration=iteration,
            before_revision=command.expected_review_case_revision,
            after_revision=review_case.revision,
            request_id=command.request_id,
            trace_id=command.trace_id,
            transaction=transaction,
        )
        await self._authorization.revalidate_for_commit(
            actor=actor,
            transaction=transaction,
        )
        return _result(review_case, iteration, replayed=False)

    def _validate_actor(self, command: OpenReviewIterationCommand, actor: RequestActor) -> None:
        if actor.organization_id != command.organization_id:
            raise ReviewIterationPermissionDenied("actor tenant does not match review tenant")
        if actor.actor_type not in {"user", "agent"} or not actor.roles.intersection(
            {"reviewer", "methodologist"}
        ):
            raise ReviewIterationPermissionDenied(
                "review iteration requires reviewer or methodologist"
            )
        if actor.actor_type == "agent" and "reviews:write" not in actor.scopes:
            raise ReviewIterationPermissionDenied(
                "agent authorization requires reviews:write scope"
            )
        if command.expected_review_case_revision < 0:
            raise ReviewIterationInvalidInput("expected ReviewCase revision must be nonnegative")

    @staticmethod
    def _validate_selected_scope(
        selected: SubmissionVersion,
        submission: Submission,
    ) -> None:
        if (
            selected.organization_id != submission.organization_id
            or selected.course_run_id != submission.course_run_id
            or selected.homework_id != submission.homework_id
        ):
            raise ReviewIterationConflict("selected version does not match Submission scope")
        if selected.status not in {"ready", "pending_review"}:
            raise ReviewIterationInvalidInput("selected version must be ready or pending_review")
        if selected.artifact_version_id is None:
            raise ReviewIterationInvalidInput("selected version has no immutable ArtifactVersion")

    @staticmethod
    def _validate_case_scope(review_case: ReviewCase, submission: Submission) -> None:
        if (
            review_case.organization_id != submission.organization_id
            or review_case.course_run_id != submission.course_run_id
            or review_case.homework_id != submission.homework_id
            or review_case.student_id != submission.student_id
        ):
            raise ReviewIterationConflict("ReviewCase scope differs from selected Submission")

    async def _immutable_inputs(
        self,
        organization_id: UUID,
        selected: SubmissionVersion,
    ) -> tuple[ArtifactVersion, HomeworkVersion, CriterionSet]:
        assert selected.artifact_version_id is not None
        artifact = await self._repository.get_artifact_version(
            organization_id,
            selected.artifact_version_id,
        )
        if artifact is None or artifact.artifact_reference_id != selected.artifact_reference_id:
            raise ReviewIterationInvalidInput("selected ArtifactVersion provenance is incomplete")
        homework_version = await self._repository.get_homework_version(
            organization_id,
            selected.homework_version_id,
        )
        if homework_version is None or homework_version.homework_id != selected.homework_id:
            raise ReviewIterationInvalidInput("selected HomeworkVersion provenance is incomplete")
        criterion_set = await self._repository.get_criterion_set_for_version(
            organization_id,
            selected.homework_version_id,
        )
        if criterion_set is None:
            raise ReviewIterationInvalidInput("selected version has no immutable CriterionSet")
        return artifact, homework_version, criterion_set


def _result(
    review_case: ReviewCase,
    iteration: ReviewIteration,
    *,
    replayed: bool,
) -> OpenReviewIterationResult:
    return OpenReviewIterationResult(
        review_case_id=review_case.id,
        review_case_revision=review_case.revision,
        review_iteration_id=iteration.id,
        submission_version_id=iteration.submission_version_id,
        iteration_number=iteration.iteration_number,
        origin=iteration.origin,
        replayed=replayed,
    )


__all__ = [
    "OpenReviewIterationCommand",
    "OpenReviewIterationResult",
    "ReviewIterationAuditPort",
    "ReviewIterationAuthorizationPort",
    "ReviewIterationConflict",
    "ReviewIterationError",
    "ReviewIterationInvalidInput",
    "ReviewIterationNotFound",
    "ReviewIterationPermissionDenied",
    "ReviewIterationRepository",
    "ReviewIterationService",
]
