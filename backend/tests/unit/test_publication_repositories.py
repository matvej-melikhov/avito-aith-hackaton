"""MySQL-backed repository tests for successors and human publication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

from review_platform.application.services.publication_requests import PublicationRequestRecord
from review_platform.application.services.review_publication import ReviewPublicationRecord
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    ArtifactReference,
    ArtifactVersion,
    AuditEvent,
    Course,
    CourseRun,
    CourseRunHomework,
    Criterion,
    CriterionSet,
    DestinationBinding,
    ExternalCredential,
    Homework,
    HomeworkVersion,
    OrganizationMembership,
    PublicationRequest,
    ReviewCase,
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewIterationRelation,
    ReviewPublication,
    ReviewRevision,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.repositories.publications import (
    InvalidPublicationTransaction,
    SqlPublicationRepository,
    require_successor_revision_repository,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewRevisionDraft,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
REVIEWER = UUID("00000000-0000-7000-8000-000000129001")
STUDENT = UUID("00000000-0000-7000-8000-000000129002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000129003")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000129004")
AGENT = UUID("00000000-0000-7000-8000-000000129005")
COURSE = UUID("00000000-0000-7000-8000-000000129011")
RUN = UUID("00000000-0000-7000-8000-000000129012")
HOMEWORK = UUID("00000000-0000-7000-8000-000000129013")
VERSION_ONE = UUID("00000000-0000-7000-8000-000000129014")
VERSION_TWO = UUID("00000000-0000-7000-8000-000000129015")
SET_ONE = UUID("00000000-0000-7000-8000-000000129016")
SET_TWO = UUID("00000000-0000-7000-8000-000000129017")
CRITERION_ONE = UUID("00000000-0000-7000-8000-000000129018")
CRITERION_TWO = UUID("00000000-0000-7000-8000-000000129019")
RUN_HOMEWORK = UUID("00000000-0000-7000-8000-000000129020")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000129021")
DESTINATION = UUID("00000000-0000-7000-8000-000000129022")
REFERENCE = UUID("00000000-0000-7000-8000-000000129023")
ARTIFACT = UUID("00000000-0000-7000-8000-000000129024")
SUBMISSION = UUID("00000000-0000-7000-8000-000000129025")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000129026")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000129027")
ITERATION = UUID("00000000-0000-7000-8000-000000129028")
REVISION = UUID("00000000-0000-7000-8000-000000129029")
DECISION = UUID("00000000-0000-7000-8000-000000129030")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=REVIEWER, display_name="Reviewer"),
                User(id=STUDENT, display_name="Student"),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Publication course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-fixture",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=REVIEWER,
                    roles=["reviewer", "methodologist"],
                    status="active",
                    revision=1,
                    auth_epoch=0,
                ),
                CourseRun(
                    id=RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Publication run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/publication",
                    locator={"external_id": "example/publication"},
                    read_capability="available",
                    feedback_capability="available",
                    last_checked_at=NOW,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AgentAuthorization(
                    id=AUTHORIZATION,
                    organization_id=ORG,
                    user_id=REVIEWER,
                    agent_id=AGENT,
                    scopes=["publication_requests:write"],
                    membership_revision=1,
                    auth_epoch=0,
                    token_digest="sha256:" + "1" * 64,
                    status="active",
                    expires_at=NOW + timedelta(days=1),
                    revision=0,
                ),
                HomeworkVersion(
                    id=VERSION_ONE,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Version one",
                    max_score=Decimal("5"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                HomeworkVersion(
                    id=VERSION_TWO,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=2,
                    student_text="Version two",
                    max_score=Decimal("5"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                CourseRunHomework(
                    id=RUN_HOMEWORK,
                    organization_id=ORG,
                    course_run_id=RUN,
                    homework_id=HOMEWORK,
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
                ArtifactVersion(
                    id=ARTIFACT,
                    organization_id=ORG,
                    artifact_reference_id=REFERENCE,
                    provider_version="commit:fixture",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG}/{ARTIFACT}/artifact.zip",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW - timedelta(hours=2),
                    artifact_metadata={},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CriterionSet(
                    id=SET_ONE,
                    organization_id=ORG,
                    homework_version_id=VERSION_ONE,
                ),
                CriterionSet(
                    id=SET_TWO,
                    organization_id=ORG,
                    homework_version_id=VERSION_TWO,
                ),
                DestinationBinding(
                    id=DESTINATION,
                    organization_id=ORG,
                    course_run_id=RUN,
                    kind="github",
                    binding_version=1,
                    recipient_ref="example/publication",
                    credential_id=CREDENTIAL,
                    credential_binding_version=1,
                    required=True,
                    status="active",
                    revision=0,
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG,
                    course_run_homework_id=RUN_HOMEWORK,
                    course_run_id=RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_predeadline_version_id=None,
                    revision=0,
                ),
                ReviewCase(
                    id=REVIEW_CASE,
                    organization_id=ORG,
                    course_run_id=RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_iteration_id=None,
                    revision=3,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Criterion(
                    id=CRITERION_ONE,
                    organization_id=ORG,
                    criterion_set_id=SET_ONE,
                    stable_key="correctness",
                    position=0,
                    title="Correctness",
                    description="Correct result",
                    max_points=Decimal("5"),
                    active=True,
                ),
                Criterion(
                    id=CRITERION_TWO,
                    organization_id=ORG,
                    criterion_set_id=SET_TWO,
                    stable_key="correctness",
                    position=0,
                    title="Correctness",
                    description="Correct result",
                    max_points=Decimal("5"),
                    active=True,
                ),
                SubmissionVersion(
                    id=SUBMISSION_VERSION,
                    organization_id=ORG,
                    submission_id=SUBMISSION,
                    course_run_id=RUN,
                    homework_id=HOMEWORK,
                    sequence=1,
                    homework_version_id=VERSION_ONE,
                    artifact_reference_id=REFERENCE,
                    artifact_version_id=ARTIFACT,
                    submitted_at=NOW - timedelta(hours=2),
                    effective_deadline=NOW - timedelta(hours=1),
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add(
            ReviewIteration(
                id=ITERATION,
                organization_id=ORG,
                review_case_id=REVIEW_CASE,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                student_id=STUDENT,
                iteration_number=1,
                submission_version_id=SUBMISSION_VERSION,
                artifact_version_id=ARTIFACT,
                homework_version_id=VERSION_ONE,
                criterion_set_id=SET_ONE,
                effective_deadline=NOW - timedelta(hours=1),
                responsible_reviewer_id=REVIEWER,
                status="ready_to_publish",
                current_revision_id=None,
                predecessor_iteration_id=None,
                origin="initial",
                revision=0,
            )
        )
        await session.flush()
        session.add(
            ReviewRevision(
                id=REVISION,
                organization_id=ORG,
                review_iteration_id=ITERATION,
                revision_number=1,
                author_user_id=REVIEWER,
                base_revision_id=None,
                feedback="Immutable feedback",
                total_score=Decimal("5"),
                created_at=NOW - timedelta(minutes=10),
            )
        )
        await session.flush()
        session.add(
            ReviewCriterionDecision(
                id=DECISION,
                organization_id=ORG,
                review_revision_id=REVISION,
                criterion_id=CRITERION_ONE,
                ai_suggestion_id=None,
                points=Decimal("5"),
                decision="manual",
                reason="Human decision",
                evidence_ids=["evidence:one"],
            )
        )
        await session.flush()
        review_case = await session.get(ReviewCase, REVIEW_CASE)
        iteration = await session.get(ReviewIteration, ITERATION)
        submission = await session.get(Submission, SUBMISSION)
        assert review_case is not None and iteration is not None and submission is not None
        review_case.current_iteration_id = ITERATION
        iteration.current_revision_id = REVISION
        iteration.revision = 1
        submission.current_predeadline_version_id = SUBMISSION_VERSION


async def _revision_bytes(factory: AsyncSessionFactory) -> tuple[object, ...]:
    async with factory() as session:
        revision = await session.get(ReviewRevision, REVISION)
        decision = await session.get(ReviewCriterionDecision, DECISION)
        assert revision is not None and decision is not None
        return (
            revision.review_iteration_id,
            revision.revision_number,
            revision.author_user_id,
            revision.base_revision_id,
            revision.feedback,
            revision.total_score,
            revision.created_at,
            decision.criterion_id,
            decision.points,
            decision.decision,
            decision.reason,
            tuple(decision.evidence_ids),
        )


async def test_requirements_successor_race_has_one_winner_and_preserves_bytes(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    before = await _revision_bytes(foundation_session_factory)
    repository = SqlPublicationRepository()
    outcomes: list[bool] = []

    async def create_successor(suffix: int) -> None:
        async with session_scope(foundation_session_factory) as session:
            context = await repository.lock_current_predecessor(
                ORG,
                ITERATION,
                expected_predecessor_revision=1,
                transaction=session,
            )
            if context is None:
                outcomes.append(False)
                return
            target = await repository.load_target(
                ORG,
                HOMEWORK,
                VERSION_TWO,
                SET_TWO,
                transaction=session,
            )
            assert target is not None
            successor_id = UUID(f"00000000-0000-7000-8000-{129100 + suffix:012d}")
            relation_id = UUID(f"00000000-0000-7000-8000-{129110 + suffix:012d}")
            revision_id = UUID(f"00000000-0000-7000-8000-{129120 + suffix:012d}")
            successor = ReviewIteration(
                id=successor_id,
                organization_id=ORG,
                review_case_id=REVIEW_CASE,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                student_id=STUDENT,
                iteration_number=await repository.next_iteration_number(
                    ORG,
                    REVIEW_CASE,
                    transaction=session,
                ),
                submission_version_id=SUBMISSION_VERSION,
                artifact_version_id=ARTIFACT,
                homework_version_id=VERSION_TWO,
                criterion_set_id=SET_TWO,
                effective_deadline=NOW - timedelta(hours=1),
                responsible_reviewer_id=REVIEWER,
                status="in_review",
                current_revision_id=None,
                predecessor_iteration_id=ITERATION,
                origin="requirements_migration",
                revision=0,
            )
            relation = ReviewIterationRelation(
                id=relation_id,
                organization_id=ORG,
                review_case_id=REVIEW_CASE,
                predecessor_iteration_id=ITERATION,
                successor_iteration_id=successor_id,
                kind="requirements_migration",
                created_at=NOW,
            )
            await repository.append_successor(successor, relation, transaction=session)
            revisions = require_successor_revision_repository(session)
            stored = await revisions.append(
                ReviewRevisionDraft(
                    organization_id=ORG,
                    review_revision_id=revision_id,
                    review_iteration_id=successor_id,
                    revision_number=1,
                    author_user_id=REVIEWER,
                    base_revision_id=None,
                    feedback="",
                    decisions=(),
                    notes=(),
                    created_at=NOW,
                )
            )
            assert stored.review_revision_id == revision_id
            assert await revisions.compare_and_set_current(
                ORG,
                successor_id,
                expected_iteration_revision=0,
                expected_current_revision_id=None,
                new_revision_id=revision_id,
            )
            outcomes.append(
                await repository.compare_and_set_current_iteration(
                    ORG,
                    REVIEW_CASE,
                    expected_review_case_revision=3,
                    expected_current_iteration_id=ITERATION,
                    new_iteration_id=successor_id,
                    transaction=session,
                )
            )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(create_successor, 1)
        tasks.start_soon(create_successor, 2)

    assert sorted(outcomes) == [False, True]
    async with foundation_session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ReviewIterationRelation)
                .where(
                    ReviewIterationRelation.organization_id == ORG,
                    ReviewIterationRelation.predecessor_iteration_id == ITERATION,
                )
            )
            == 1
        )
        existing = await repository.find_existing_migration(
            ORG,
            ITERATION,
            VERSION_TWO,
            SET_TWO,
            transaction=session,
        )
        assert existing is not None
        assert existing.successor_iteration_revision == 1
        assert existing.transferred_decision_count == 0
    assert await _revision_bytes(foundation_session_factory) == before


async def test_publication_request_reserve_collision_and_confirm_are_atomic(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    repository = SqlPublicationRepository()
    results: list[tuple[UUID, bool]] = []

    async def reserve_request(suffix: int) -> None:
        async with session_scope(foundation_session_factory) as session:
            context = await repository.lock_iteration_context(
                ORG,
                ITERATION,
                expected_revision=1,
                transaction=session,
            )
            assert context is not None and context.current_review_revision_id == REVISION
            stored, created = await repository.reserve(
                PublicationRequestRecord(
                    organization_id=ORG,
                    publication_request_id=UUID(f"00000000-0000-7000-8000-{129200 + suffix:012d}"),
                    review_iteration_id=ITERATION,
                    review_revision_id=REVISION,
                    requested_by_user_id=REVIEWER,
                    agent_id=AGENT,
                    agent_authorization_id=AUTHORIZATION,
                    idempotency_key="publication-request-race-0001",
                    status="pending",
                    expires_at=NOW + timedelta(hours=1),
                    revision=0,
                ),
                transaction=session,
            )
            results.append((stored.publication_request_id, created))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reserve_request, 1)
        tasks.start_soon(reserve_request, 2)

    assert sorted(created for _, created in results) == [False, True]
    assert len({identity for identity, _ in results}) == 1
    request_id = results[0][0]
    async with session_scope(foundation_session_factory) as session:
        collision, created = await repository.reserve(
            PublicationRequestRecord(
                organization_id=ORG,
                publication_request_id=UUID("00000000-0000-7000-8000-000000129203"),
                review_iteration_id=ITERATION,
                review_revision_id=REVISION,
                requested_by_user_id=REVIEWER,
                agent_id=AGENT,
                agent_authorization_id=AUTHORIZATION,
                idempotency_key="publication-request-race-0001",
                status="pending",
                expires_at=NOW + timedelta(hours=2),
                revision=0,
            ),
            transaction=session,
        )
        assert not created
        assert collision.expires_at == NOW + timedelta(hours=1)
        locked = await repository.lock_publication_request(
            ORG,
            request_id,
            transaction=session,
        )
        assert locked is not None and locked.status == "pending"
        assert await repository.confirm_publication_request(
            ORG,
            request_id,
            expected_revision=0,
            confirmed_by=REVIEWER,
            confirmed_at=NOW,
            transaction=session,
        )
        assert not await repository.confirm_publication_request(
            ORG,
            request_id,
            expected_revision=0,
            confirmed_by=REVIEWER,
            confirmed_at=NOW,
            transaction=session,
        )

    async with foundation_session_factory() as session:
        row = await session.get(PublicationRequest, request_id)
        assert row is not None
        assert (row.status, row.revision, row.confirmed_by, row.confirmed_at) == (
            "confirmed",
            1,
            REVIEWER,
            NOW,
        )


async def test_published_snapshot_and_correction_replay_are_exact(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    repository = SqlPublicationRepository()
    publication_id = UUID("00000000-0000-7000-8000-000000129240")
    successor_id = UUID("00000000-0000-7000-8000-000000129241")
    successor_revision_id = UUID("00000000-0000-7000-8000-000000129242")
    reason = "Correct immutable published result"

    async with session_scope(foundation_session_factory) as session:
        context = await repository.lock_publication_context(
            ORG,
            ITERATION,
            expected_iteration_revision=1,
            expected_current_revision_id=REVISION,
            transaction=session,
        )
        assert context is not None
        stored, created = await repository.reserve_publication(
            ReviewPublicationRecord(
                organization_id=ORG,
                publication_id=publication_id,
                review_iteration_id=ITERATION,
                review_revision_id=REVISION,
                publication_request_id=None,
                publication_version=1,
                published_by=REVIEWER,
                published_at=NOW,
            ),
            transaction=session,
        )
        assert created and stored.publication.publication_id == publication_id
        assert await repository.compare_and_set_published(
            ORG,
            ITERATION,
            expected_iteration_revision=1,
            expected_current_revision_id=REVISION,
            transaction=session,
        )

    async with session_scope(foundation_session_factory) as session:
        published = await repository.require_published_revision(
            ORG,
            ITERATION,
            REVISION,
            transaction=session,
        )
        assert published is not None
        assert published.publication_id == publication_id
        assert published.revision.feedback == "Immutable feedback"
        assert published.revision.decisions[0].evidence_ids == ("evidence:one",)
        successor_context = await repository.lock_current_predecessor(
            ORG,
            ITERATION,
            expected_predecessor_revision=2,
            transaction=session,
        )
        assert successor_context is not None
        successor = ReviewIteration(
            id=successor_id,
            organization_id=ORG,
            review_case_id=REVIEW_CASE,
            course_run_id=RUN,
            homework_id=HOMEWORK,
            student_id=STUDENT,
            iteration_number=2,
            submission_version_id=SUBMISSION_VERSION,
            artifact_version_id=ARTIFACT,
            homework_version_id=VERSION_ONE,
            criterion_set_id=SET_ONE,
            effective_deadline=NOW - timedelta(hours=1),
            responsible_reviewer_id=REVIEWER,
            status="in_review",
            current_revision_id=None,
            predecessor_iteration_id=ITERATION,
            origin="correction",
            revision=0,
        )
        relation = ReviewIterationRelation(
            id=UUID("00000000-0000-7000-8000-000000129243"),
            organization_id=ORG,
            review_case_id=REVIEW_CASE,
            predecessor_iteration_id=ITERATION,
            successor_iteration_id=successor_id,
            kind="correction",
            created_at=NOW + timedelta(minutes=1),
        )
        await repository.append_successor(successor, relation, transaction=session)
        revisions = require_successor_revision_repository(session)
        await revisions.append(
            ReviewRevisionDraft(
                organization_id=ORG,
                review_revision_id=successor_revision_id,
                review_iteration_id=successor_id,
                revision_number=1,
                author_user_id=REVIEWER,
                base_revision_id=None,
                feedback="Immutable feedback",
                decisions=(),
                notes=(),
                created_at=NOW + timedelta(minutes=1),
            )
        )
        assert await revisions.compare_and_set_current(
            ORG,
            successor_id,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            new_revision_id=successor_revision_id,
        )
        assert await repository.compare_and_set_current_iteration(
            ORG,
            REVIEW_CASE,
            expected_review_case_revision=3,
            expected_current_iteration_id=ITERATION,
            new_iteration_id=successor_id,
            transaction=session,
        )
        session.add(
            AuditEvent(
                id=UUID("00000000-0000-7000-8000-000000129244"),
                organization_id=ORG,
                actor_type="user",
                actor_user_id=REVIEWER,
                action="create_review_correction",
                entity_type="review_iteration",
                entity_id=successor_id,
                request_id=UUID("00000000-0000-7000-8000-000000129245"),
                trace_id=UUID("00000000-0000-7000-8000-000000129246"),
                outcome="succeeded",
                sanitized_details={"reason": reason},
                occurred_at=NOW + timedelta(minutes=1),
            )
        )

    async with foundation_session_factory() as session:
        replay = await repository.find_existing_correction(
            ORG,
            ITERATION,
            REVISION,
            reason,
            transaction=session,
        )
        assert replay is not None
        assert replay.successor_iteration_id == successor_id
        assert replay.successor_revision_id == successor_revision_id
        assert (
            await repository.find_existing_correction(
                ORG,
                ITERATION,
                REVISION,
                "different reason",
                transaction=session,
            )
            is None
        )


async def test_human_publication_context_and_reserve_have_one_cas_winner(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    before = await _revision_bytes(foundation_session_factory)
    repository = SqlPublicationRepository()
    outcomes: list[tuple[UUID, bool]] = []
    snapshots: list[tuple[UUID, int, UUID, int]] = []

    async with foundation_session_factory() as session:
        run = await session.get(CourseRun, RUN)
        assert run is not None
        run.status = "archived"
        await session.flush()
        archived = await repository.lock_publication_context(
            ORG,
            ITERATION,
            expected_iteration_revision=1,
            expected_current_revision_id=REVISION,
            transaction=session,
        )
        assert archived is not None
        assert archived.course_status == "active"
        assert archived.course_run_status == "archived"
        await session.rollback()

    async def publish(suffix: int) -> None:
        async with session_scope(foundation_session_factory) as session:
            context = await repository.lock_publication_context(
                ORG,
                ITERATION,
                expected_iteration_revision=1,
                expected_current_revision_id=REVISION,
                transaction=session,
            )
            if context is None:
                existing = await repository.find_existing_publication(
                    ORG,
                    ITERATION,
                    REVISION,
                    transaction=session,
                )
                assert existing is not None
                outcomes.append((existing.publication.publication_id, False))
                return
            assert context.course_status == context.course_run_status == "active"
            assert context.current_revision.feedback == "Immutable feedback"
            assert len(context.destinations) == 1
            destination = context.destinations[0]
            snapshots.append(
                (
                    destination.destination_binding_id,
                    destination.binding_version,
                    destination.credential_binding_id,
                    destination.credential_binding_version,
                )
            )
            publication = ReviewPublicationRecord(
                organization_id=ORG,
                publication_id=UUID(f"00000000-0000-7000-8000-{129300 + suffix:012d}"),
                review_iteration_id=ITERATION,
                review_revision_id=REVISION,
                publication_request_id=None,
                publication_version=await repository.next_publication_version(
                    ORG,
                    ITERATION,
                    transaction=session,
                ),
                published_by=REVIEWER,
                published_at=NOW,
            )
            stored, created = await repository.reserve_publication(
                publication,
                transaction=session,
            )
            assert created
            assert stored.review_iteration_revision == 2
            assert stored.deliveries == ()
            assert await repository.compare_and_set_published(
                ORG,
                ITERATION,
                expected_iteration_revision=1,
                expected_current_revision_id=REVISION,
                transaction=session,
            )
            outcomes.append((publication.publication_id, True))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(publish, 1)
        tasks.start_soon(publish, 2)

    assert sorted(created for _, created in outcomes) == [False, True]
    assert len({identity for identity, _ in outcomes}) == 1
    assert snapshots == [(DESTINATION, 1, CREDENTIAL, 1)]
    async with foundation_session_factory() as session:
        replay = await repository.find_existing_publication(
            ORG,
            ITERATION,
            REVISION,
            transaction=session,
        )
        assert replay is not None
        assert replay.review_iteration_revision == 2
        assert replay.deliveries == ()
        assert await session.scalar(select(func.count()).select_from(ReviewPublication)) == 1
        iteration = await session.get(ReviewIteration, ITERATION)
        assert iteration is not None
        assert (iteration.status, iteration.revision, iteration.current_revision_id) == (
            "published",
            2,
            REVISION,
        )
        assert (
            await repository.find_existing_publication(
                OTHER_ORG,
                ITERATION,
                REVISION,
                transaction=session,
            )
            is None
        )
    assert await _revision_bytes(foundation_session_factory) == before


async def test_publication_repository_requires_caller_owned_session() -> None:
    repository = SqlPublicationRepository()
    with pytest.raises(InvalidPublicationTransaction, match="caller-owned AsyncSession"):
        await repository.find_existing_publication(
            ORG,
            ITERATION,
            REVISION,
            transaction=object(),
        )

    with pytest.raises(InvalidPublicationTransaction, match="caller-owned AsyncSession"):
        require_successor_revision_repository(object())
