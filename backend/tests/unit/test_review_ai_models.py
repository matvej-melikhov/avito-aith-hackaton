"""Human review and AI persistence invariants, including MySQL constraints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint, Numeric, UniqueConstraint, update
from sqlalchemy.exc import IntegrityError
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AICriterionSuggestion,
    AIReviewAttempt,
    AIReviewEventReceipt,
    AIReviewRun,
    AISignal,
    ArtifactReference,
    ArtifactVersion,
    Course,
    CourseRun,
    CourseRunHomework,
    Criterion,
    CriterionSet,
    ExternalCredential,
    Homework,
    HomeworkVersion,
    Operation,
    Organization,
    ReviewCase,
    ReviewCriterionDecision,
    ReviewIteration,
    ReviewNote,
    ReviewRevision,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
)
from review_platform.infrastructure.db.session import (
    test_transaction as db_test_transaction,
)


def _uniques(model: type[object]) -> set[tuple[str, ...]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        tuple(c.name for c in item.columns)
        for item in table.constraints
        if isinstance(item, UniqueConstraint)
    }


def _fks(model: type[object]) -> set[str]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        element.target_fullname
        for item in table.constraints
        if isinstance(item, ForeignKeyConstraint)
        for element in item.elements
    }


def test_human_revision_shape_is_immutable_and_relation_local() -> None:
    assert {"status", "published_at", "publication_id", "updated_at"}.isdisjoint(
        ReviewRevision.__table__.columns
    )
    assert ("organization_id", "review_iteration_id", "revision_number") in _uniques(ReviewRevision)
    assert ("organization_id", "review_revision_id", "criterion_id") in _uniques(
        ReviewCriterionDecision
    )
    assert ("organization_id", "review_revision_id", "position") in _uniques(ReviewNote)
    assert isinstance(ReviewRevision.__table__.c.total_score.type, Numeric)
    assert isinstance(ReviewCriterionDecision.__table__.c.points.type, Numeric)
    assert "evidence_ids" in ReviewCriterionDecision.__table__.columns
    assert "evidence" not in ReviewCriterionDecision.__table__.columns
    assert "review_revision.id" in _fks(ReviewIteration)


def test_ai_run_attempt_event_and_output_uniqueness() -> None:
    assert ("organization_id", "input_fingerprint") in _uniques(AIReviewRun)
    assert ("organization_id", "ai_review_run_id", "attempt_number") in _uniques(AIReviewAttempt)
    assert AIReviewEventReceipt.__table__.c.event_id.primary_key
    assert ("organization_id", "ai_review_run_id", "attempt_number", "sequence") in _uniques(
        AIReviewEventReceipt
    )
    assert ("organization_id", "event_id", "criterion_id") in _uniques(AICriterionSuggestion)
    assert ("organization_id", "event_id") in _uniques(AISignal)


def test_ai_has_full_immutable_input_provenance_and_no_ai_to_human_path() -> None:
    assert {
        "course_run_id",
        "submission_version_id",
        "review_iteration_id",
        "artifact_version_id",
        "content_digest",
        "homework_version_id",
        "homework_digest",
        "criterion_set_id",
        "criteria_digest",
        "contract_version",
        "fingerprint_algorithm",
        "input_fingerprint",
    } <= set(AIReviewRun.__table__.columns.keys())
    ai_targets = (
        _fks(AIReviewRun)
        | _fks(AIReviewAttempt)
        | _fks(AIReviewEventReceipt)
        | _fks(AICriterionSuggestion)
        | _fks(AISignal)
    )
    assert not any(
        target.startswith(("review_revision.", "review_note.", "review_criterion_decision."))
        for target in ai_targets
    )
    assert "ai_criterion_suggestion.id" in _fks(ReviewCriterionDecision)


def test_review_ai_migrations_form_one_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert len(scripts.get_heads()) == 1
    assert (
        scripts.get_revision("0005_review_spine").down_revision == "0004_submissions_and_artifacts"
    )
    assert scripts.get_revision("0006_ai_review").down_revision == "0005_review_spine"


@pytest.mark.infrastructure
@pytest.mark.anyio
async def test_mysql_current_pointer_attempt_credential_and_event_constraints(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    def uid(number: int) -> UUID:
        return UUID(f"00000000-0000-7000-8000-{number:012d}")
    (
        org,
        other_org,
        user,
        course,
        run,
        homework,
        hv,
        cset,
        criterion,
        crh,
        reference,
        artifact,
        operation,
        submission,
        sv,
        case,
        iteration1,
        iteration2,
        revision1,
        revision2,
        credential,
        other_credential,
        ai_run,
        attempt,
        event,
        suggestion,
    ) = [uid(n) for n in range(1001, 1027)]
    try:
        async with db_test_transaction(engine) as session:
            session.add_all(
                [
                    Organization(id=org, slug="review-ai-org", name="Review AI Org"),
                    Organization(id=other_org, slug="review-ai-other", name="Review AI Other"),
                    User(id=user, display_name="Reviewer"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Course(
                        id=course,
                        organization_id=org,
                        title="Course",
                        description="",
                        source_kind="standalone",
                    ),
                    ExternalCredential(
                        id=credential,
                        organization_id=org,
                        provider="ai_component",
                        binding_version=1,
                        ciphertext="cipher",
                        key_id="key",
                    ),
                    ExternalCredential(
                        id=other_credential,
                        organization_id=other_org,
                        provider="ai_component",
                        binding_version=1,
                        ciphertext="cipher",
                        key_id="key",
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    CourseRun(
                        id=run,
                        organization_id=org,
                        course_id=course,
                        title="Run",
                        timezone="UTC",
                        status="active",
                    ),
                    Homework(id=homework, organization_id=org, course_id=course, title="Homework"),
                    ArtifactReference(
                        id=reference,
                        organization_id=org,
                        provider="github",
                        credential_binding_id=credential,
                        credential_binding_version=1,
                        original_url="https://github.com/example/repo",
                        locator={},
                        read_capability="available",
                        feedback_capability="available",
                        last_checked_at=now,
                    ),
                    Operation(
                        id=operation,
                        organization_id=org,
                        kind="artifact_capture",
                        input_version="v1",
                        state="succeeded",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    HomeworkVersion(
                        id=hv,
                        organization_id=org,
                        homework_id=homework,
                        version_number=1,
                        student_text="Text",
                        max_score=Decimal("10.00"),
                        artifact_kinds=["github"],
                        estimated_review_minutes=30,
                    ),
                    ArtifactVersion(
                        id=artifact,
                        organization_id=org,
                        artifact_reference_id=reference,
                        provider_version="v1",
                        content_digest="sha256:" + "5" * 64,
                        object_key=f"{org}/{artifact}/content",
                        media_type="application/zip",
                        byte_size=128,
                        captured_at=now,
                        artifact_metadata={},
                    ),
                    CourseRunHomework(
                        id=crh,
                        organization_id=org,
                        course_run_id=run,
                        homework_id=homework,
                        status="active",
                    ),
                ]
            )
            await session.flush()
            session.add(CriterionSet(id=cset, organization_id=org, homework_version_id=hv))
            session.add(
                Submission(
                    id=submission,
                    organization_id=org,
                    course_run_homework_id=crh,
                    course_run_id=run,
                    homework_id=homework,
                    student_id=user,
                )
            )
            await session.flush()
            session.add(
                Criterion(
                    id=criterion,
                    organization_id=org,
                    criterion_set_id=cset,
                    stable_key="correctness",
                    position=0,
                    title="Correctness",
                    description="",
                    max_points=Decimal("10.00"),
                    active=True,
                )
            )
            session.add(
                SubmissionVersion(
                    id=sv,
                    organization_id=org,
                    submission_id=submission,
                    course_run_id=run,
                    homework_id=homework,
                    sequence=1,
                    homework_version_id=hv,
                    artifact_reference_id=reference,
                    artifact_version_id=artifact,
                    submitted_at=now,
                    effective_deadline=now + timedelta(days=1),
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=operation,
                )
            )
            session.add(
                ReviewCase(
                    id=case,
                    organization_id=org,
                    course_run_id=run,
                    homework_id=homework,
                    student_id=user,
                )
            )
            await session.flush()

            def make_iteration(identity: UUID, number: int, origin: str) -> ReviewIteration:
                return ReviewIteration(
                    id=identity,
                    organization_id=org,
                    review_case_id=case,
                    course_run_id=run,
                    homework_id=homework,
                    student_id=user,
                    iteration_number=number,
                    submission_version_id=sv,
                    artifact_version_id=artifact,
                    homework_version_id=hv,
                    criterion_set_id=cset,
                    effective_deadline=now + timedelta(days=1),
                    status="queued",
                    origin=origin,
                    predecessor_iteration_id=iteration1 if number == 2 else None,
                )

            session.add_all(
                [
                    make_iteration(iteration1, 1, "initial"),
                    make_iteration(iteration2, 2, "correction"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ReviewRevision(
                        id=revision1,
                        organization_id=org,
                        review_iteration_id=iteration1,
                        revision_number=1,
                        author_user_id=user,
                        feedback="First",
                        total_score=Decimal("10.00"),
                        created_at=now,
                    ),
                    ReviewRevision(
                        id=revision2,
                        organization_id=org,
                        review_iteration_id=iteration2,
                        revision_number=1,
                        author_user_id=user,
                        feedback="Second",
                        total_score=Decimal("9.00"),
                        created_at=now,
                    ),
                ]
            )
            await session.flush()
            await session.execute(
                update(ReviewIteration)
                .where(ReviewIteration.id == iteration1)
                .values(current_revision_id=revision1)
            )
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        update(ReviewIteration)
                        .where(ReviewIteration.id == iteration1)
                        .values(current_revision_id=revision2)
                    )

            session.add(
                AIReviewRun(
                    id=ai_run,
                    organization_id=org,
                    review_iteration_id=iteration1,
                    course_run_id=run,
                    submission_version_id=sv,
                    artifact_version_id=artifact,
                    content_digest="sha256:" + "5" * 64,
                    homework_version_id=hv,
                    homework_digest="sha256:" + "6" * 64,
                    criterion_set_id=cset,
                    criteria_digest="sha256:" + "7" * 64,
                    contract_version="1.1.0",
                    fingerprint_algorithm="jcs-sha256-v1",
                    input_fingerprint="sha256:" + "8" * 64,
                    status="running",
                    current_attempt_no=1,
                )
            )
            await session.flush()
            session.add(
                AIReviewAttempt(
                    id=attempt,
                    organization_id=org,
                    ai_review_run_id=ai_run,
                    attempt_number=1,
                    credential_binding_id=credential,
                    credential_binding_version=1,
                    status="running",
                    last_sequence=0,
                    started_at=now,
                )
            )
            await session.flush()
            session.add(
                AIReviewEventReceipt(
                    event_id=event,
                    organization_id=org,
                    ai_review_run_id=ai_run,
                    attempt_id=attempt,
                    attempt_number=1,
                    sequence=1,
                    payload_digest="sha256:" + "9" * 64,
                    status="partial",
                    is_final=False,
                    received_at=now,
                )
            )
            await session.flush()
            session.add(
                AICriterionSuggestion(
                    id=suggestion,
                    organization_id=org,
                    ai_review_run_id=ai_run,
                    event_id=event,
                    criterion_id=criterion,
                    status="suggested",
                    proposed_points=Decimal("9.00"),
                    reason="Reason",
                    evidence=[],
                    confidence="high",
                    flags=[],
                )
            )
            session.add(
                AISignal(
                    id=uid(1030),
                    organization_id=org,
                    ai_review_run_id=ai_run,
                    event_id=event,
                    level="low",
                    evidence=[],
                    limitations=[],
                    questions=[],
                )
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    duplicate_run = AIReviewRun(
                        id=uid(1031),
                        organization_id=org,
                        review_iteration_id=iteration1,
                        course_run_id=run,
                        submission_version_id=sv,
                        artifact_version_id=artifact,
                        content_digest="sha256:" + "5" * 64,
                        homework_version_id=hv,
                        homework_digest="sha256:" + "6" * 64,
                        criterion_set_id=cset,
                        criteria_digest="sha256:" + "7" * 64,
                        contract_version="1.1.0",
                        fingerprint_algorithm="jcs-sha256-v1",
                        input_fingerprint="sha256:" + "8" * 64,
                        status="pending",
                        current_attempt_no=0,
                    )
                    session.add(duplicate_run)
                    await session.flush([duplicate_run])
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    wrong_credential = AIReviewAttempt(
                        id=uid(1032),
                        organization_id=org,
                        ai_review_run_id=ai_run,
                        attempt_number=2,
                        credential_binding_id=other_credential,
                        credential_binding_version=1,
                        status="pending",
                        last_sequence=0,
                        started_at=now,
                    )
                    session.add(wrong_credential)
                    await session.flush([wrong_credential])
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    duplicate_event = AIReviewEventReceipt(
                        event_id=event,
                        organization_id=org,
                        ai_review_run_id=ai_run,
                        attempt_id=attempt,
                        attempt_number=1,
                        sequence=2,
                        payload_digest="sha256:" + "a" * 64,
                        status="partial",
                        is_final=False,
                        received_at=now,
                    )
                    session.add(duplicate_event)
                    await session.flush([duplicate_event])
    finally:
        await engine.dispose()
