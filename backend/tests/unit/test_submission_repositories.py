from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

from review_platform.application.request_context import RequestActor
from review_platform.application.services.artifact_capture import (
    ArtifactCaptureCommand,
    ArtifactCaptureLimits,
    ArtifactPromotionCaptureRecord,
    ArtifactPromotionRequested,
    ArtifactVersionCaptureRecord,
)
from review_platform.application.services.artifact_preflight import (
    ArtifactCredentialBinding,
    ArtifactReferenceRecord,
)
from review_platform.application.services.review_iterations import (
    OpenReviewIterationCommand,
    ReviewIterationRepository,
    ReviewIterationService,
)
from review_platform.application.services.submissions import (
    ArtifactCaptureRequest,
    SubmissionVersionRecord,
)
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CourseRunHomeworkPublication,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course, CourseMembership, CourseRun
from review_platform.infrastructure.db.models.operations import (
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.submission import (
    ArtifactPromotion,
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.repositories.submissions import (
    SqlArtifactCaptureRepository,
    SqlArtifactCredentialBindings,
    SqlArtifactPreflightRepository,
    SqlArtifactPromotionOutbox,
    SqlCaptureScheduler,
    SqlReviewIterationRepository,
    SqlSubmissionRepository,
    SubmissionPersistenceConflict,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
STUDENT = UUID("00000000-0000-7000-8000-000000004001")
REVIEWER = UUID("00000000-0000-7000-8000-000000004002")
COURSE = UUID("00000000-0000-7000-8000-000000004011")
RUN_A = UUID("00000000-0000-7000-8000-000000004021")
RUN_B = UUID("00000000-0000-7000-8000-000000004022")
HOMEWORK_ID = UUID("00000000-0000-7000-8000-000000004031")
HOMEWORK_VERSION_ID = UUID("00000000-0000-7000-8000-000000004032")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000004033")
RELATION_A = UUID("00000000-0000-7000-8000-000000004041")
RELATION_B = UUID("00000000-0000-7000-8000-000000004042")
PUBLICATION_A = UUID("00000000-0000-7000-8000-000000004051")
PUBLICATION_B = UUID("00000000-0000-7000-8000-000000004052")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000004061")
CREDENTIAL_SECOND = UUID("00000000-0000-7000-8000-000000004062")
REFERENCE = UUID("00000000-0000-7000-8000-000000004071")
SUBMISSION_A = UUID("00000000-0000-7000-8000-000000004081")
SUBMISSION_B = UUID("00000000-0000-7000-8000-000000004082")
SUBMISSION_VERSION_ID = UUID("00000000-0000-7000-8000-000000004091")
ARTIFACT_VERSION_ID = UUID("00000000-0000-7000-8000-000000004101")
PROMOTION_ID = UUID("00000000-0000-7000-8000-000000004102")
OPERATION_ID = UUID("00000000-0000-7000-8000-000000004103")
REVIEW_CASE_ID = UUID("00000000-0000-7000-8000-000000004111")
ITERATION_A = UUID("00000000-0000-7000-8000-000000004112")
ITERATION_B = UUID("00000000-0000-7000-8000-000000004113")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


class IDs:
    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=STUDENT, display_name="Student", status="active"),
                User(id=REVIEWER, display_name="Reviewer", status="active"),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=UUID("00000000-0000-7000-8000-000000004121"),
                    organization_id=ORG,
                    user_id=STUDENT,
                    roles=["student"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                CourseRun(
                    id=RUN_A,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id=None,
                    title="Run A",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=RUN_B,
                    organization_id=ORG,
                    course_id=COURSE,
                    external_run_id=None,
                    title="Run B",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK_ID,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Homework",
                    revision=0,
                ),
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted",
                    key_id="test-key",
                    status="active",
                ),
                ExternalCredential(
                    id=CREDENTIAL_SECOND,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-second",
                    key_id="test-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add(
            HomeworkVersion(
                id=HOMEWORK_VERSION_ID,
                organization_id=ORG,
                homework_id=HOMEWORK_ID,
                version_number=1,
                student_text="Requirements",
                max_score=Decimal("10"),
                artifact_kinds=["github"],
                estimated_review_minutes=30,
                revision=0,
            )
        )
        await session.flush()
        session.add(
            CriterionSet(
                id=CRITERION_SET, organization_id=ORG, homework_version_id=HOMEWORK_VERSION_ID
            )
        )
        session.add_all(
            [
                CourseRunHomework(
                    id=RELATION_A,
                    organization_id=ORG,
                    course_run_id=RUN_A,
                    homework_id=HOMEWORK_ID,
                    current_publication_id=None,
                    status="active",
                    revision=1,
                ),
                CourseRunHomework(
                    id=RELATION_B,
                    organization_id=ORG,
                    course_run_id=RUN_B,
                    homework_id=HOMEWORK_ID,
                    current_publication_id=None,
                    status="active",
                    revision=1,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _publication(PUBLICATION_A, RELATION_A),
                _publication(PUBLICATION_B, RELATION_B),
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000004131"),
                    organization_id=ORG,
                    course_run_id=RUN_A,
                    user_id=STUDENT,
                    kind="student",
                    source="imported",
                    status="active",
                    external_version="v1",
                    joined_at=NOW,
                ),
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000004132"),
                    organization_id=ORG,
                    course_run_id=RUN_B,
                    user_id=STUDENT,
                    kind="student",
                    source="imported",
                    status="active",
                    external_version="v1",
                    joined_at=NOW,
                ),
            ]
        )
        await session.flush()
        relation_a = await session.get(CourseRunHomework, RELATION_A)
        relation_b = await session.get(CourseRunHomework, RELATION_B)
        assert relation_a is not None and relation_b is not None
        relation_a.current_publication_id = PUBLICATION_A
        relation_b.current_publication_id = PUBLICATION_B


def _publication(identity: UUID, relation: UUID) -> CourseRunHomeworkPublication:
    return CourseRunHomeworkPublication(
        id=identity,
        organization_id=ORG,
        course_run_homework_id=relation,
        homework_id=HOMEWORK_ID,
        homework_version_id=HOMEWORK_VERSION_ID,
        publication_sequence=1,
        submission_deadline=NOW + timedelta(days=1),
        review_deadline=NOW + timedelta(days=2),
        published_at=NOW,
    )


def _candidate_reference(identity: UUID) -> ArtifactReferenceRecord:
    return ArtifactReferenceRecord(
        organization_id=ORG,
        artifact_reference_id=identity,
        provider="github",
        credential_binding_id=CREDENTIAL,
        credential_binding_version=1,
        original_url="https://github.com/example/repository",
        locator={
            "canonical_url": "https://github.com/example/repository",
            "external_id": "example/repository",
        },
        read_capability="available",
        feedback_capability="available",
        last_checked_at=NOW,
        revision=0,
    )


async def test_preflight_reference_and_submission_unique_races_are_tenant_scoped(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    reference_ids: list[UUID] = []

    async def upsert(identity: UUID) -> None:
        async with session_scope(foundation_session_factory) as session:
            result = await SqlArtifactPreflightRepository().upsert_available_reference(
                _candidate_reference(identity), transaction=session
            )
            reference_ids.append(result.artifact_reference_id)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(upsert, REFERENCE)
        tasks.start_soon(upsert, UUID("00000000-0000-7000-8000-000000004072"))
    assert len(set(reference_ids)) == 1

    async with foundation_session_factory() as session:
        repository = SqlArtifactPreflightRepository()
        context_a = await repository.lock_context(
            ORG, RELATION_A, STUDENT, expected_revision=1, transaction=session
        )
        context_b = await repository.lock_context(
            ORG, RELATION_B, STUDENT, expected_revision=1, transaction=session
        )
        assert context_a is not None and context_b is not None
        assert (
            await repository.lock_context(
                OTHER_ORG, RELATION_A, STUDENT, expected_revision=1, transaction=session
            )
            is None
        )
        first = await repository.get_or_create_submission(
            context_a, student_id=STUDENT, new_submission_id=SUBMISSION_A, transaction=session
        )
        replay = await repository.get_or_create_submission(
            context_a,
            student_id=STUDENT,
            new_submission_id=UUID("00000000-0000-7000-8000-000000004083"),
            transaction=session,
        )
        second_run = await repository.get_or_create_submission(
            context_b, student_id=STUDENT, new_submission_id=SUBMISSION_B, transaction=session
        )
        assert first.submission_id == replay.submission_id == SUBMISSION_A
        assert second_run.submission_id == SUBMISSION_B
        assert first.course_run_id != second_run.course_run_id
        await SqlArtifactCredentialBindings().require_exact_active(
            ORG,
            ArtifactCredentialBinding(CREDENTIAL, 1, "github"),
            transaction=session,
        )
        await session.commit()

    async with foundation_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ArtifactReference)) == 1
        assert await session.scalar(select(func.count()).select_from(Submission)) == 2


async def test_capture_bundle_outbox_failure_and_replay_are_atomic(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        session.add(
            ArtifactReference(
                id=REFERENCE,
                organization_id=ORG,
                provider="github",
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                original_url="https://github.com/example/repository",
                locator={
                    "canonical_url": "https://github.com/example/repository",
                    "external_id": "example/repository",
                },
                read_capability="available",
                feedback_capability="available",
                last_checked_at=NOW,
                revision=0,
            )
        )
    command = ArtifactCaptureCommand(
        organization_id=ORG,
        artifact_reference_id=REFERENCE,
        operation_id=OPERATION_ID,
        provider="github",
        credential_binding=ArtifactCredentialBinding(CREDENTIAL, 1, "github"),
        limits=ArtifactCaptureLimits(10, 1024, 1024, 1024, 2048),
        actor=RequestActor.user(
            organization_id=ORG,
            user_id=STUDENT,
            roles={"student"},
            membership_revision=0,
            auth_epoch=0,
        ),
    )
    version = ArtifactVersionCaptureRecord(
        ORG,
        ARTIFACT_VERSION_ID,
        REFERENCE,
        "github",
        "commit:abc",
        DIGEST,
        f"{ORG}/{ARTIFACT_VERSION_ID}/artifact.bin",
        "application/zip",
        128,
        NOW,
        {"credential_binding_id": str(CREDENTIAL), "credential_binding_version": 1},
    )
    promotion = ArtifactPromotionCaptureRecord(
        ORG,
        PROMOTION_ID,
        ARTIFACT_VERSION_ID,
        OPERATION_ID,
        f"{ORG}/{ARTIFACT_VERSION_ID}/staged/{OPERATION_ID}",
        version.object_key,
        "db_committed",
        0,
        5,
    )
    repository = SqlArtifactCaptureRepository(id_factory=IDs(1400))
    event = ArtifactPromotionRequested(
        ORG,
        UUID("00000000-0000-7000-8000-000000004141"),
        REFERENCE,
        ARTIFACT_VERSION_ID,
        PROMOTION_ID,
        OPERATION_ID,
        promotion.staged_key,
        promotion.final_key,
        DIGEST,
        128,
    )
    async with session_scope(foundation_session_factory) as session:
        bundle = await repository.record_success(
            command=command,
            input_version="artifact-capture:1.1.0:sha256:" + "b" * 64,
            artifact_version=version,
            promotion=promotion,
            attempt_metadata={
                "credential_binding_id": str(CREDENTIAL),
                "credential_binding_version": 1,
            },
            now=NOW,
            transaction=session,
        )
        await SqlArtifactPromotionOutbox(clock=lambda: NOW).append(event, transaction=session)
        assert bundle.replayed is False
    async with foundation_session_factory() as session:
        replay = await repository.find_by_reference_digest(
            ORG, REFERENCE, DIGEST, transaction=session
        )
        assert replay is not None and replay.replayed
        assert replay.artifact_version.artifact_version_id == ARTIFACT_VERSION_ID
        assert await session.scalar(select(func.count()).select_from(ArtifactVersion)) == 1
        assert await session.scalar(select(func.count()).select_from(ArtifactPromotion)) == 1
        assert await session.scalar(select(func.count()).select_from(OperationAttempt)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 1
        assert (
            await repository.find_by_reference_digest(
                OTHER_ORG, REFERENCE, DIGEST, transaction=session
            )
            is None
        )

    failure_operation = UUID("00000000-0000-7000-8000-000000004142")
    failure_command = ArtifactCaptureCommand(
        organization_id=ORG,
        artifact_reference_id=REFERENCE,
        operation_id=failure_operation,
        provider="github",
        credential_binding=ArtifactCredentialBinding(CREDENTIAL, 1, "github"),
        limits=command.limits,
        actor=command.actor,
    )
    async with session_scope(foundation_session_factory) as session:
        failure = await repository.record_failure(
            command=failure_command,
            input_version="artifact-capture:1.1.0:sha256:" + "c" * 64,
            state="retryable_failed",
            error={"code": "provider_down", "message": "Bearer secret", "retryable": True},
            attempt_metadata={
                "credential_binding_id": str(CREDENTIAL),
                "credential_binding_version": 1,
            },
            now=NOW,
            transaction=session,
        )
        assert failure.operation_state == "retryable_failed"
        assert "secret" not in str(failure.error)
    async with foundation_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ArtifactPromotion)) == 1


async def test_reference_identity_and_capture_outbox_preserve_exact_binding_pair(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    first = _candidate_reference(REFERENCE)
    second = replace(
        first,
        artifact_reference_id=UUID("00000000-0000-7000-8000-000000004073"),
        credential_binding_id=CREDENTIAL_SECOND,
    )
    operation_id = UUID("00000000-0000-7000-8000-000000004074")
    async with session_scope(foundation_session_factory) as session:
        preflight = SqlArtifactPreflightRepository()
        stored_first = await preflight.upsert_available_reference(first, transaction=session)
        stored_second = await preflight.upsert_available_reference(second, transaction=session)
        assert stored_first.artifact_reference_id != stored_second.artifact_reference_id
        assert stored_first.credential_binding_id == CREDENTIAL
        assert stored_second.credential_binding_id == CREDENTIAL_SECOND

        submission_reference = await SqlSubmissionRepository().get_artifact_reference(
            ORG,
            stored_second.artifact_reference_id,
            transaction=session,
        )
        assert submission_reference is not None
        assert submission_reference.credential_binding_id == CREDENTIAL_SECOND
        assert submission_reference.credential_binding_version == 1
        await SqlCaptureScheduler(id_factory=IDs(7000), clock=lambda: NOW).schedule(
            ArtifactCaptureRequest(
                organization_id=ORG,
                operation_id=operation_id,
                submission_id=SUBMISSION_A,
                submission_version_id=SUBMISSION_VERSION_ID,
                artifact_reference_id=stored_second.artifact_reference_id,
                provider="github",
                credential_binding_id=CREDENTIAL_SECOND,
                credential_binding_version=1,
                course_run_id=RUN_A,
                homework_id=HOMEWORK_ID,
                homework_version_id=HOMEWORK_VERSION_ID,
                course_run_homework_id=RELATION_A,
                homework_publication_id=PUBLICATION_A,
                actor_user_id=STUDENT,
                actor_membership_revision=0,
                actor_auth_epoch=0,
            ),
            transaction=session,
        )
        with pytest.raises(SubmissionPersistenceConflict, match="provenance mismatched"):
            await SqlCaptureScheduler(id_factory=IDs(7100), clock=lambda: NOW).schedule(
                ArtifactCaptureRequest(
                    organization_id=ORG,
                    operation_id=UUID("00000000-0000-7000-8000-000000004075"),
                    submission_id=SUBMISSION_A,
                    submission_version_id=SUBMISSION_VERSION_ID,
                    artifact_reference_id=stored_second.artifact_reference_id,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
                    credential_binding_version=1,
                    course_run_id=RUN_A,
                    homework_id=HOMEWORK_ID,
                    homework_version_id=HOMEWORK_VERSION_ID,
                    course_run_homework_id=RELATION_A,
                    homework_publication_id=PUBLICATION_A,
                    actor_user_id=STUDENT,
                    actor_membership_revision=0,
                    actor_auth_epoch=0,
                ),
                transaction=session,
            )
    async with foundation_session_factory() as session:
        message = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.aggregate_id == operation_id)
        )
        assert message is not None
        assert message.payload["credential_binding_id"] == str(CREDENTIAL_SECOND)
        assert message.payload["credential_binding_version"] == 1


async def test_submission_cas_scheduler_and_history_keep_only_us3_fields(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    preflight = SqlArtifactPreflightRepository()
    repository = SqlSubmissionRepository()
    ids = IDs(1500)
    async with session_scope(foundation_session_factory) as session:
        reference = await preflight.upsert_available_reference(
            _candidate_reference(REFERENCE), transaction=session
        )
        context = await preflight.lock_context(
            ORG, RELATION_A, STUDENT, expected_revision=1, transaction=session
        )
        assert context is not None
        await preflight.get_or_create_submission(
            context, student_id=STUDENT, new_submission_id=SUBMISSION_A, transaction=session
        )
        locked = await repository.lock_submission(
            ORG, SUBMISSION_A, expected_revision=0, transaction=session
        )
        assert locked is not None
        publication = await repository.lock_current_publication(
            ORG, RUN_A, HOMEWORK_ID, transaction=session
        )
        assert publication is not None
        assert (
            await repository.get_artifact_reference(
                ORG, reference.artifact_reference_id, transaction=session
            )
        ).usable  # type: ignore[union-attr]
        version = SubmissionVersionRecord(
            ORG,
            SUBMISSION_VERSION_ID,
            SUBMISSION_A,
            1,
            HOMEWORK_VERSION_ID,
            REFERENCE,
            OPERATION_ID,
            NOW,
            publication.submission_deadline,
            "before_deadline",
            "validating",
            0,
        )
        await repository.append_version(version, transaction=session)
        await SqlCaptureScheduler(id_factory=ids, clock=lambda: NOW).schedule(
            ArtifactCaptureRequest(
                ORG,
                OPERATION_ID,
                SUBMISSION_A,
                SUBMISSION_VERSION_ID,
                REFERENCE,
                "github",
                CREDENTIAL,
                1,
                RUN_A,
                HOMEWORK_ID,
                HOMEWORK_VERSION_ID,
                RELATION_A,
                PUBLICATION_A,
                STUDENT,
                0,
                0,
            ),
            transaction=session,
        )
        assert await repository.compare_and_set_submission(
            ORG,
            SUBMISSION_A,
            expected_revision=0,
            current_predeadline_version_id=SUBMISSION_VERSION_ID,
            transaction=session,
        )
        assert not await repository.compare_and_set_submission(
            ORG,
            SUBMISSION_A,
            expected_revision=0,
            current_predeadline_version_id=SUBMISSION_VERSION_ID,
            transaction=session,
        )
    async with foundation_session_factory() as session:
        history = await repository.history(ORG, SUBMISSION_A, transaction=session)
        assert history is not None
        assert history.current_submission_version_id == SUBMISSION_VERSION_ID
        assert history.current_publication_id is None
        assert [item.id for item in history.versions] == [SUBMISSION_VERSION_ID]
        assert history.versions[0].capture_operation_id == OPERATION_ID
        assert history.artifact_versions == ()
        assert history.review_iterations == ()
        assert {field.name for field in fields(history)} == {
            "submission_id",
            "course_run_id",
            "homework_id",
            "current_submission_version_id",
            "current_publication_id",
            "versions",
            "artifact_versions",
            "review_iterations",
        }
        assert await repository.history(OTHER_ORG, SUBMISSION_A, transaction=session) is None
        assert await session.scalar(select(func.count()).select_from(Operation)) == 1
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 1


async def test_first_predeadline_and_replacement_flush_before_current_pointer_cas(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    repository = SqlSubmissionRepository()
    first_id = UUID("00000000-0000-7000-8000-000000004201")
    second_id = UUID("00000000-0000-7000-8000-000000004202")
    first_operation = UUID("00000000-0000-7000-8000-000000004203")
    second_operation = UUID("00000000-0000-7000-8000-000000004204")
    async with session_scope(foundation_session_factory) as session:
        reference = await SqlArtifactPreflightRepository().upsert_available_reference(
            _candidate_reference(REFERENCE),
            transaction=session,
        )
        context = await SqlArtifactPreflightRepository().lock_context(
            ORG,
            RELATION_A,
            STUDENT,
            expected_revision=1,
            transaction=session,
        )
        assert context is not None
        await SqlArtifactPreflightRepository().get_or_create_submission(
            context,
            student_id=STUDENT,
            new_submission_id=SUBMISSION_A,
            transaction=session,
        )
        publication = await repository.lock_current_publication(
            ORG,
            RUN_A,
            HOMEWORK_ID,
            transaction=session,
        )
        assert publication is not None
        first = SubmissionVersionRecord(
            organization_id=ORG,
            version_id=first_id,
            submission_id=SUBMISSION_A,
            sequence=1,
            homework_version_id=HOMEWORK_VERSION_ID,
            artifact_reference_id=reference.artifact_reference_id,
            capture_operation_id=first_operation,
            submitted_at=NOW,
            effective_deadline=publication.submission_deadline,
            phase="before_deadline",
            status="validating",
        )
        await repository.append_version(first, transaction=session)
        await SqlCaptureScheduler(id_factory=IDs(8000), clock=lambda: NOW).schedule(
            _capture_request(first),
            transaction=session,
        )
        assert await repository.compare_and_set_submission(
            ORG,
            SUBMISSION_A,
            expected_revision=0,
            current_predeadline_version_id=first_id,
            transaction=session,
        )

    async with session_scope(foundation_session_factory) as session:
        second = SubmissionVersionRecord(
            organization_id=ORG,
            version_id=second_id,
            submission_id=SUBMISSION_A,
            sequence=2,
            homework_version_id=HOMEWORK_VERSION_ID,
            artifact_reference_id=REFERENCE,
            capture_operation_id=second_operation,
            submitted_at=NOW + timedelta(minutes=1),
            effective_deadline=NOW + timedelta(days=1),
            phase="before_deadline",
            status="validating",
        )
        await repository.append_version(second, transaction=session)
        await repository.mark_superseded(ORG, first_id, transaction=session)
        await SqlCaptureScheduler(id_factory=IDs(8100), clock=lambda: NOW).schedule(
            _capture_request(second),
            transaction=session,
        )
        assert await repository.compare_and_set_submission(
            ORG,
            SUBMISSION_A,
            expected_revision=1,
            current_predeadline_version_id=second_id,
            transaction=session,
        )

    async with foundation_session_factory() as session:
        submission = await session.get(Submission, SUBMISSION_A)
        versions = (
            await session.scalars(
                select(SubmissionVersion)
                .where(SubmissionVersion.submission_id == SUBMISSION_A)
                .order_by(SubmissionVersion.sequence)
            )
        ).all()
        assert submission is not None
        assert submission.current_predeadline_version_id == second_id
        assert submission.revision == 2
        assert [(row.id, row.status) for row in versions] == [
            (first_id, "superseded"),
            (second_id, "validating"),
        ]
        assert await session.scalar(select(func.count()).select_from(Operation)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 2


def _capture_request(version: SubmissionVersionRecord) -> ArtifactCaptureRequest:
    return ArtifactCaptureRequest(
        organization_id=version.organization_id,
        operation_id=version.capture_operation_id,
        submission_id=version.submission_id,
        submission_version_id=version.version_id,
        artifact_reference_id=version.artifact_reference_id,
        provider="github",
        credential_binding_id=CREDENTIAL,
        credential_binding_version=1,
        course_run_id=RUN_A,
        homework_id=HOMEWORK_ID,
        homework_version_id=version.homework_version_id,
        course_run_homework_id=RELATION_A,
        homework_publication_id=PUBLICATION_A,
        actor_user_id=STUDENT,
        actor_membership_revision=0,
        actor_auth_epoch=0,
    )


class ReviewAuthorization:
    async def authorize_open(self, **_: object) -> None:
        return None

    async def revalidate_for_commit(self, **_: object) -> None:
        return None


class ReviewAudit:
    async def record_open(self, **_: object) -> None:
        return None


async def test_duplicate_open_review_race_has_one_case_and_iteration_winner(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        session.add(
            ArtifactReference(
                id=REFERENCE,
                organization_id=ORG,
                provider="github",
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                original_url="https://github.com/example/repository",
                locator={
                    "canonical_url": "https://github.com/example/repository",
                    "external_id": "example/repository",
                },
                read_capability="available",
                feedback_capability="available",
                last_checked_at=NOW,
                revision=0,
            )
        )
        session.add(
            Submission(
                id=SUBMISSION_A,
                organization_id=ORG,
                course_run_homework_id=RELATION_A,
                course_run_id=RUN_A,
                homework_id=HOMEWORK_ID,
                student_id=STUDENT,
                current_predeadline_version_id=None,
                revision=0,
            )
        )
        session.add(
            Operation(
                id=OPERATION_ID,
                organization_id=ORG,
                kind="artifact_capture",
                input_version="capture",
                state="succeeded",
                revision=1,
                created_at=NOW,
                updated_at=NOW,
                finished_at=NOW,
            )
        )
        await session.flush()
        session.add(
            ArtifactVersion(
                id=ARTIFACT_VERSION_ID,
                organization_id=ORG,
                artifact_reference_id=REFERENCE,
                provider_version="commit:abc",
                content_digest=DIGEST,
                object_key=f"{ORG}/{ARTIFACT_VERSION_ID}/artifact.bin",
                media_type="application/zip",
                byte_size=128,
                captured_at=NOW,
                artifact_metadata={},
            )
        )
        await session.flush()
        session.add(
            SubmissionVersion(
                id=SUBMISSION_VERSION_ID,
                organization_id=ORG,
                submission_id=SUBMISSION_A,
                course_run_id=RUN_A,
                homework_id=HOMEWORK_ID,
                sequence=1,
                homework_version_id=HOMEWORK_VERSION_ID,
                artifact_reference_id=REFERENCE,
                artifact_version_id=ARTIFACT_VERSION_ID,
                submitted_at=NOW,
                effective_deadline=NOW + timedelta(days=1),
                phase="before_deadline",
                status="ready",
                capture_operation_id=OPERATION_ID,
                revision=0,
            )
        )
        await session.flush()
        submission = await session.get(Submission, SUBMISSION_A)
        assert submission is not None
        submission.current_predeadline_version_id = SUBMISSION_VERSION_ID
        submission.revision = 1
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )
    command = OpenReviewIterationCommand(
        ORG,
        REVIEW_CASE_ID,
        SUBMISSION_VERSION_ID,
        0,
        UUID("00000000-0000-7000-8000-000000004161"),
        UUID("00000000-0000-7000-8000-000000004162"),
    )
    results = []
    ready = anyio.Event()
    arrivals = 0

    async def open_review(iteration_id: UUID) -> None:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await ready.wait()
        async with session_scope(foundation_session_factory) as session:
            repository: ReviewIterationRepository = SqlReviewIterationRepository(session)
            service = ReviewIterationService(
                repository=repository,
                authorization=ReviewAuthorization(),
                audit=ReviewAudit(),
                id_factory=lambda: iteration_id,
                clock=lambda: NOW,
            )
            results.append(await service.open(command, actor=actor, transaction=session))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(open_review, ITERATION_A)
        tasks.start_soon(open_review, ITERATION_B)
    assert len({result.review_iteration_id for result in results}) == 1
    assert sorted(result.replayed for result in results) == [False, True]
    async with foundation_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ReviewCase)) == 1
        assert await session.scalar(select(func.count()).select_from(ReviewIteration)) == 1
        history = await SqlSubmissionRepository().history(ORG, SUBMISSION_A, transaction=session)
        assert history is not None
        assert len(history.review_iterations) == 1
        assert history.review_iterations[0].artifact_version_id == ARTIFACT_VERSION_ID
