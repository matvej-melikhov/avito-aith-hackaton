from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

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
from review_platform.infrastructure.db.models.review_revision import (
    ReviewCriterionDecision,
    ReviewNote,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.submission import (
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionDraft,
    ReviewNoteDraft,
    ReviewRevisionConflict,
    ReviewRevisionDraft,
    ReviewRevisionRepositoryError,
    SqlReviewRevisionRepository,
    require_review_revision_repository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
REVIEWER = UUID("00000000-0000-7000-8000-000000010001")
STUDENT = UUID("00000000-0000-7000-8000-000000010002")
COURSE = UUID("00000000-0000-7000-8000-000000010003")
RUN = UUID("00000000-0000-7000-8000-000000010004")
HOMEWORK = UUID("00000000-0000-7000-8000-000000010005")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000010006")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000010007")
CRITERION_A = UUID("00000000-0000-7000-8000-000000010008")
CRITERION_B = UUID("00000000-0000-7000-8000-000000010009")
INACTIVE_CRITERION = UUID("00000000-0000-7000-8000-000000010010")
RELATION = UUID("00000000-0000-7000-8000-000000010011")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000010012")
REFERENCE = UUID("00000000-0000-7000-8000-000000010013")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000010014")
CAPTURE_OPERATION = UUID("00000000-0000-7000-8000-000000010015")
SUBMISSION = UUID("00000000-0000-7000-8000-000000010016")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000010017")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000010018")
REVIEW_ITERATION = UUID("00000000-0000-7000-8000-000000010019")

OTHER_COURSE = UUID("00000000-0000-7000-8000-000000010020")
OTHER_HOMEWORK = UUID("00000000-0000-7000-8000-000000010021")
OTHER_HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000010022")
OTHER_CRITERION_SET = UUID("00000000-0000-7000-8000-000000010023")
OTHER_CRITERION = UUID("00000000-0000-7000-8000-000000010024")

REVISION_1 = UUID("00000000-0000-7000-8000-000000010101")
REVISION_2 = UUID("00000000-0000-7000-8000-000000010102")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


async def _seed(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add_all(
            [
                User(id=REVIEWER, display_name="Reviewer", status="active"),
                User(id=STUDENT, display_name="Student", status="active"),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Review Course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                Course(
                    id=OTHER_COURSE,
                    organization_id=OTHER_ORG,
                    title="Other Course",
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
                    ciphertext="encrypted",
                    key_id="fixture-key",
                    status="active",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CourseRun(
                    id=RUN,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Review Run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Review Homework",
                    revision=0,
                ),
                Homework(
                    id=OTHER_HOMEWORK,
                    organization_id=OTHER_ORG,
                    course_id=OTHER_COURSE,
                    title="Other Homework",
                    revision=0,
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=CREDENTIAL,
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
                    input_version="review-repository-fixture",
                    state="succeeded",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    finished_at=NOW,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                HomeworkVersion(
                    id=HOMEWORK_VERSION,
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Review requirements",
                    max_score=Decimal("10"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                HomeworkVersion(
                    id=OTHER_HOMEWORK_VERSION,
                    organization_id=OTHER_ORG,
                    homework_id=OTHER_HOMEWORK,
                    version_number=1,
                    student_text="Other requirements",
                    max_score=Decimal("5"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                ArtifactVersion(
                    id=ARTIFACT_VERSION,
                    organization_id=ORG,
                    artifact_reference_id=REFERENCE,
                    provider_version="commit:fixture",
                    content_digest="sha256:" + "a" * 64,
                    object_key=f"{ORG}/{ARTIFACT_VERSION}/artifact.bin",
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW,
                    artifact_metadata={},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                CriterionSet(
                    id=CRITERION_SET,
                    organization_id=ORG,
                    homework_version_id=HOMEWORK_VERSION,
                ),
                CriterionSet(
                    id=OTHER_CRITERION_SET,
                    organization_id=OTHER_ORG,
                    homework_version_id=OTHER_HOMEWORK_VERSION,
                ),
                CourseRunHomework(
                    id=RELATION,
                    organization_id=ORG,
                    course_run_id=RUN,
                    homework_id=HOMEWORK,
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _criterion(CRITERION_A, CRITERION_SET, ORG, 0, "correctness", True),
                _criterion(CRITERION_B, CRITERION_SET, ORG, 1, "quality", True),
                _criterion(
                    INACTIVE_CRITERION,
                    CRITERION_SET,
                    ORG,
                    2,
                    "retired",
                    False,
                ),
                _criterion(
                    OTHER_CRITERION,
                    OTHER_CRITERION_SET,
                    OTHER_ORG,
                    0,
                    "other",
                    True,
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG,
                    course_run_homework_id=RELATION,
                    course_run_id=RUN,
                    homework_id=HOMEWORK,
                    student_id=STUDENT,
                    current_predeadline_version_id=None,
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add(
            SubmissionVersion(
                id=SUBMISSION_VERSION,
                organization_id=ORG,
                submission_id=SUBMISSION,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                sequence=1,
                homework_version_id=HOMEWORK_VERSION,
                artifact_reference_id=REFERENCE,
                artifact_version_id=ARTIFACT_VERSION,
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
                id=REVIEW_CASE,
                organization_id=ORG,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                student_id=STUDENT,
                current_iteration_id=None,
                revision=0,
            )
        )
        await session.flush()
        session.add(
            ReviewIteration(
                id=REVIEW_ITERATION,
                organization_id=ORG,
                review_case_id=REVIEW_CASE,
                course_run_id=RUN,
                homework_id=HOMEWORK,
                student_id=STUDENT,
                iteration_number=1,
                submission_version_id=SUBMISSION_VERSION,
                artifact_version_id=ARTIFACT_VERSION,
                homework_version_id=HOMEWORK_VERSION,
                criterion_set_id=CRITERION_SET,
                effective_deadline=NOW + timedelta(days=2),
                responsible_reviewer_id=REVIEWER,
                status="in_review",
                current_revision_id=None,
                predecessor_iteration_id=None,
                origin="initial",
                revision=0,
            )
        )
        await session.flush()
        review_case = await session.get(ReviewCase, REVIEW_CASE)
        assert review_case is not None
        review_case.current_iteration_id = REVIEW_ITERATION


def _criterion(
    identity: UUID,
    criterion_set_id: UUID,
    organization_id: UUID,
    position: int,
    key: str,
    active: bool,
) -> Criterion:
    return Criterion(
        id=identity,
        organization_id=organization_id,
        criterion_set_id=criterion_set_id,
        stable_key=key,
        position=position,
        title=key.title(),
        description="",
        max_points=Decimal("5"),
        active=active,
    )


def _draft(
    revision_id: UUID,
    *,
    revision_number: int,
    base_revision_id: UUID | None,
    first_criterion: UUID = CRITERION_A,
) -> ReviewRevisionDraft:
    suffix = revision_number * 10
    return ReviewRevisionDraft(
        organization_id=ORG,
        review_revision_id=revision_id,
        review_iteration_id=REVIEW_ITERATION,
        revision_number=revision_number,
        author_user_id=REVIEWER,
        base_revision_id=base_revision_id,
        feedback=f"Human feedback revision {revision_number}",
        decisions=(
            ReviewDecisionDraft(
                decision_id=UUID(
                    f"00000000-0000-7000-8000-{10000 + suffix + 2:012d}"
                ),
                criterion_id=CRITERION_B,
                points=Decimal("3"),
                decision="changed",
                reason="Human quality judgment",
                evidence_ids=("file:README.md#L4",),
            ),
            ReviewDecisionDraft(
                decision_id=UUID(
                    f"00000000-0000-7000-8000-{10000 + suffix + 1:012d}"
                ),
                criterion_id=first_criterion,
                points=Decimal("4"),
                decision="manual",
                reason="Human correctness judgment",
                evidence_ids=("file:main.py#L1", "file:test.py#L2"),
            ),
        ),
        notes=(
            ReviewNoteDraft(
                note_id=UUID(
                    f"00000000-0000-7000-8000-{10000 + suffix + 4:012d}"
                ),
                criterion_id=None,
                text="Global review note",
                author_user_id=REVIEWER,
                position=1,
            ),
            ReviewNoteDraft(
                note_id=UUID(
                    f"00000000-0000-7000-8000-{10000 + suffix + 3:012d}"
                ),
                criterion_id=CRITERION_A,
                text="Criterion-specific note",
                author_user_id=REVIEWER,
                position=0,
            ),
        ),
        created_at=NOW + timedelta(minutes=revision_number),
    )


async def test_append_cas_and_deterministic_detail_round_trip(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    async with session_scope(foundation_session_factory) as session:
        repository = require_review_revision_repository(session)
        context = await repository.lock_iteration(ORG, REVIEW_ITERATION)
        assert context is not None
        assert context.current_revision_id is None
        assert context.iteration_revision == 0
        assert [criterion.criterion_id for criterion in context.criteria] == [
            CRITERION_A,
            CRITERION_B,
        ]
        assert await repository.next_revision_number(ORG, REVIEW_ITERATION) == 1
        record = await repository.append(
            _draft(REVISION_1, revision_number=1, base_revision_id=None)
        )
        assert record.total_score == Decimal("7")
        assert await repository.compare_and_set_current(
            ORG,
            REVIEW_ITERATION,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            new_revision_id=REVISION_1,
        )
        assert session.in_transaction()
        assert [decision.criterion_id for decision in record.decisions] == [
            CRITERION_A,
            CRITERION_B,
        ]
        assert [note.position for note in record.notes] == [0, 1]
        assert record.decisions[0].evidence_ids == (
            "file:main.py#L1",
            "file:test.py#L2",
        )
        assert not hasattr(repository, "update_revision")
        assert not hasattr(repository, "delete_revision")

    async with foundation_session_factory() as session:
        history = await SqlReviewRevisionRepository(session).history(ORG, REVIEW_ITERATION)
        assert [revision.review_revision_id for revision in history] == [REVISION_1]
        assert await SqlReviewRevisionRepository(session).history(
            OTHER_ORG,
            REVIEW_ITERATION,
        ) == ()


async def test_stale_cas_rolls_back_and_prior_revision_remains_byte_stable(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    await _save_first(foundation_session_factory)
    before = await _revision_bytes(foundation_session_factory, REVISION_1)

    with pytest.raises(ReviewRevisionConflict, match="stale current revision"):
        async with session_scope(foundation_session_factory) as session:
            repository = SqlReviewRevisionRepository(session)
            await repository.append(
                _draft(REVISION_2, revision_number=2, base_revision_id=REVISION_1)
            )
            if not await repository.compare_and_set_current(
                ORG,
                REVIEW_ITERATION,
                expected_iteration_revision=0,
                expected_current_revision_id=None,
                new_revision_id=REVISION_2,
            ):
                raise ReviewRevisionConflict("stale current revision rejected")

    assert await _revision_count(foundation_session_factory) == 1
    assert await _revision_bytes(foundation_session_factory, REVISION_1) == before

    async with session_scope(foundation_session_factory) as session:
        repository = SqlReviewRevisionRepository(session)
        await repository.append(
            _draft(REVISION_2, revision_number=2, base_revision_id=REVISION_1)
        )
        assert await repository.compare_and_set_current(
            ORG,
            REVIEW_ITERATION,
            expected_iteration_revision=1,
            expected_current_revision_id=REVISION_1,
            new_revision_id=REVISION_2,
        )
    assert await _revision_bytes(foundation_session_factory, REVISION_1) == before


async def test_concurrent_save_has_one_expected_current_winner(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    winners: list[UUID] = []
    conflicts: list[UUID] = []

    async def save(identity: UUID) -> None:
        try:
            async with session_scope(foundation_session_factory) as session:
                repository = SqlReviewRevisionRepository(session)
                context = await repository.lock_iteration(ORG, REVIEW_ITERATION)
                assert context is not None
                if context.iteration_revision != 0 or context.current_revision_id is not None:
                    raise ReviewRevisionConflict("stale current revision rejected")
                await repository.append(
                    _draft(identity, revision_number=1, base_revision_id=None)
                )
                if not await repository.compare_and_set_current(
                    ORG,
                    REVIEW_ITERATION,
                    expected_iteration_revision=0,
                    expected_current_revision_id=None,
                    new_revision_id=identity,
                ):
                    raise ReviewRevisionConflict("stale current revision rejected")
            winners.append(identity)
        except ReviewRevisionConflict:
            conflicts.append(identity)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(save, REVISION_1)
        tasks.start_soon(save, REVISION_2)

    assert len(winners) == len(conflicts) == 1
    assert await _revision_count(foundation_session_factory) == 1
    async with foundation_session_factory() as session:
        iteration = await session.get(ReviewIteration, REVIEW_ITERATION)
        assert iteration is not None
        assert iteration.current_revision_id == winners[0]
        assert iteration.revision == 1


async def test_cross_tenant_inactive_and_wrong_criterion_affinity_are_rejected(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed(foundation_session_factory)
    async with foundation_session_factory() as session:
        repository = SqlReviewRevisionRepository(session)
        assert await repository.lock_iteration(OTHER_ORG, REVIEW_ITERATION) is None

    for criterion in (OTHER_CRITERION, INACTIVE_CRITERION):
        with pytest.raises(ReviewRevisionRepositoryError, match="not active"):
            async with session_scope(foundation_session_factory) as session:
                await SqlReviewRevisionRepository(session).append(
                    _draft(
                        REVISION_1,
                        revision_number=1,
                        base_revision_id=None,
                        first_criterion=criterion,
                    )
                )
    assert await _revision_count(foundation_session_factory) == 0


async def _save_first(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        repository = SqlReviewRevisionRepository(session)
        await repository.append(_draft(REVISION_1, revision_number=1, base_revision_id=None))
        assert await repository.compare_and_set_current(
            ORG,
            REVIEW_ITERATION,
            expected_iteration_revision=0,
            expected_current_revision_id=None,
            new_revision_id=REVISION_1,
        )


async def _revision_count(factory: AsyncSessionFactory) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(ReviewRevision)) or 0)


async def _revision_bytes(factory: AsyncSessionFactory, revision_id: UUID) -> bytes:
    async with factory() as session:
        revision = await session.scalar(
            select(ReviewRevision).where(ReviewRevision.id == revision_id)
        )
        assert revision is not None
        decisions = (
            await session.scalars(
                select(ReviewCriterionDecision)
                .where(ReviewCriterionDecision.review_revision_id == revision_id)
                .order_by(ReviewCriterionDecision.id)
            )
        ).all()
        notes = (
            await session.scalars(
                select(ReviewNote)
                .where(ReviewNote.review_revision_id == revision_id)
                .order_by(ReviewNote.id)
            )
        ).all()
        value: dict[str, Any] = {
            "revision": {
                "id": str(revision.id),
                "iteration": str(revision.review_iteration_id),
                "number": revision.revision_number,
                "author": str(revision.author_user_id),
                "base": str(revision.base_revision_id),
                "feedback": revision.feedback,
                "total": str(revision.total_score),
                "created": revision.created_at.isoformat(),
            },
            "decisions": [
                {
                    "id": str(row.id),
                    "criterion": str(row.criterion_id),
                    "points": str(row.points),
                    "decision": row.decision,
                    "reason": row.reason,
                    "evidence": row.evidence_ids,
                }
                for row in decisions
            ],
            "notes": [
                {
                    "id": str(row.id),
                    "criterion": str(row.criterion_id),
                    "text": row.text,
                    "author": str(row.author_user_id),
                    "position": row.position,
                }
                for row in notes
            ],
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
