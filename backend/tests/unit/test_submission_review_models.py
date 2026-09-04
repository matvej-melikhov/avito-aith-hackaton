"""Structural and MySQL checks for submission, artifact, and review-case models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, update
from sqlalchemy.exc import IntegrityError
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    ArtifactPromotion,
    ArtifactReference,
    ArtifactVersion,
    Course,
    CourseRun,
    CourseRunHomework,
    CriterionSet,
    Homework,
    HomeworkVersion,
    Operation,
    Organization,
    ReviewCase,
    ReviewIteration,
    Submission,
    SubmissionVersion,
    User,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
)
from review_platform.infrastructure.db.session import (
    test_transaction as foundation_test_transaction,
)


def _uniques(model: type[object]) -> set[tuple[str, ...]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        tuple(c.name for c in item.columns)
        for item in table.constraints
        if isinstance(item, UniqueConstraint)
    }


def _fks(model: type[object]) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        (tuple(c.name for c in item.columns), tuple(e.target_fullname for e in item.elements))
        for item in table.constraints
        if isinstance(item, ForeignKeyConstraint)
    }


def test_current_pointers_are_aggregate_local_composite_foreign_keys() -> None:
    assert (
        ("organization_id", "id", "current_predeadline_version_id"),
        (
            "submission_version.organization_id",
            "submission_version.submission_id",
            "submission_version.id",
        ),
    ) in _fks(Submission)
    assert (
        ("organization_id", "id", "current_iteration_id"),
        (
            "review_iteration.organization_id",
            "review_iteration.review_case_id",
            "review_iteration.id",
        ),
    ) in _fks(ReviewCase)


def test_submission_review_identity_and_initial_iteration_uniqueness() -> None:
    assert ("organization_id", "course_run_id", "homework_id", "student_id") in _uniques(Submission)
    assert ("organization_id", "submission_id", "sequence") in _uniques(SubmissionVersion)
    assert ("organization_id", "course_run_id", "homework_id", "student_id") in _uniques(ReviewCase)
    assert ("organization_id", "review_case_id", "iteration_number") in _uniques(ReviewIteration)
    assert ("organization_id", "initial_submission_version_id") in _uniques(ReviewIteration)
    assert ReviewIteration.__table__.c.initial_submission_version_id.computed is not None


def test_artifact_and_promotion_shapes_are_durable_and_tenant_scoped() -> None:
    assert {"provider", "original_url", "locator", "read_capability", "feedback_capability"} <= set(
        ArtifactReference.__table__.columns.keys()
    )
    assert {
        "provider_version",
        "content_digest",
        "object_key",
        "media_type",
        "byte_size",
        "captured_at",
        "metadata",
    } <= set(ArtifactVersion.__table__.columns.keys())
    assert {
        "staged_key",
        "final_key",
        "state",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "attempts",
        "max_attempts",
        "sanitized_error",
    } <= set(ArtifactPromotion.__table__.columns.keys())
    assert ("organization_id", "artifact_reference_id", "content_digest") in _uniques(
        ArtifactVersion
    )


def test_submission_migration_is_single_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["0004_submissions_and_artifacts"]
    revision = scripts.get_revision("0004_submissions_and_artifacts")
    assert revision is not None and revision.down_revision == "0003_homework_versions"


@pytest.mark.infrastructure
@pytest.mark.anyio
async def test_mysql_rejects_cross_aggregate_pointers_and_duplicate_initials(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    ids = [UUID(f"00000000-0000-7000-8000-{n:012d}") for n in range(851, 880)]
    (
        org,
        user,
        course,
        run1,
        run2,
        homework,
        hv,
        criterion_set,
        crh1,
        crh2,
        reference,
        artifact,
        op,
        submission1,
        submission2,
        sv1,
        sv2,
        case1,
        case2,
        iteration1,
        iteration2,
        promotion,
        *_,
    ) = ids
    org_other, op_other, cross_promotion = ids[22:25]
    try:
        async with foundation_test_transaction(engine) as session:
            session.add_all(
                [
                    Organization(id=org, slug="submission-model-org", name="Submission Model Org"),
                    Organization(
                        id=org_other,
                        slug="submission-model-other",
                        name="Submission Model Other",
                    ),
                    User(id=user, display_name="Student"),
                ]
            )
            await session.flush()
            session.add(
                Course(
                    id=course,
                    organization_id=org,
                    title="Course",
                    description="",
                    source_kind="standalone",
                )
            )
            await session.flush()
            session.add_all(
                [
                    CourseRun(
                        id=run1,
                        organization_id=org,
                        course_id=course,
                        title="Run 1",
                        timezone="UTC",
                        status="active",
                    ),
                    CourseRun(
                        id=run2,
                        organization_id=org,
                        course_id=course,
                        title="Run 2",
                        timezone="UTC",
                        status="active",
                    ),
                    Homework(id=homework, organization_id=org, course_id=course, title="Homework"),
                    ArtifactReference(
                        id=reference,
                        organization_id=org,
                        provider="github",
                        original_url="https://github.com/example/repo",
                        locator={"external_id": "example/repo"},
                        read_capability="available",
                        feedback_capability="available",
                        last_checked_at=now,
                    ),
                    Operation(
                        id=op,
                        organization_id=org,
                        kind="artifact_capture",
                        input_version="fixture:1",
                        state="pending",
                        created_at=now,
                        updated_at=now,
                    ),
                    Operation(
                        id=op_other,
                        organization_id=org_other,
                        kind="artifact_capture",
                        input_version="fixture:other",
                        state="pending",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.flush()
            session.add(
                HomeworkVersion(
                    id=hv,
                    organization_id=org,
                    homework_id=homework,
                    version_number=1,
                    student_text="Text",
                    max_score=Decimal("10.00"),
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                )
            )
            session.add(
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
                )
            )
            session.add_all(
                [
                    CourseRunHomework(
                        id=crh1,
                        organization_id=org,
                        course_run_id=run1,
                        homework_id=homework,
                        status="active",
                    ),
                    CourseRunHomework(
                        id=crh2,
                        organization_id=org,
                        course_run_id=run2,
                        homework_id=homework,
                        status="active",
                    ),
                ]
            )
            await session.flush()
            session.add(CriterionSet(id=criterion_set, organization_id=org, homework_version_id=hv))
            session.add_all(
                [
                    Submission(
                        id=submission1,
                        organization_id=org,
                        course_run_homework_id=crh1,
                        course_run_id=run1,
                        homework_id=homework,
                        student_id=user,
                    ),
                    Submission(
                        id=submission2,
                        organization_id=org,
                        course_run_homework_id=crh2,
                        course_run_id=run2,
                        homework_id=homework,
                        student_id=user,
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    SubmissionVersion(
                        id=sv1,
                        organization_id=org,
                        submission_id=submission1,
                        course_run_id=run1,
                        homework_id=homework,
                        sequence=1,
                        homework_version_id=hv,
                        artifact_reference_id=reference,
                        artifact_version_id=artifact,
                        submitted_at=now,
                        effective_deadline=now + timedelta(days=1),
                        phase="before_deadline",
                        status="ready",
                        capture_operation_id=op,
                    ),
                    SubmissionVersion(
                        id=sv2,
                        organization_id=org,
                        submission_id=submission2,
                        course_run_id=run2,
                        homework_id=homework,
                        sequence=1,
                        homework_version_id=hv,
                        artifact_reference_id=reference,
                        artifact_version_id=artifact,
                        submitted_at=now,
                        effective_deadline=now + timedelta(days=1),
                        phase="before_deadline",
                        status="ready",
                        capture_operation_id=op,
                    ),
                    ArtifactPromotion(
                        id=promotion,
                        organization_id=org,
                        artifact_version_id=artifact,
                        operation_id=op,
                        staged_key=f"{org}/{artifact}/staged",
                        final_key=f"{org}/{artifact}/final",
                        state="db_committed",
                        attempts=0,
                        max_attempts=5,
                    ),
                ]
            )
            await session.flush()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    invalid_promotion = ArtifactPromotion(
                        id=cross_promotion,
                        organization_id=org,
                        artifact_version_id=artifact,
                        operation_id=op_other,
                        staged_key=f"{org}/{artifact}/cross-staged",
                        final_key=f"{org}/{artifact}/cross-final",
                        state="db_committed",
                        attempts=0,
                        max_attempts=5,
                    )
                    session.add(invalid_promotion)
                    await session.flush([invalid_promotion])
            await session.execute(
                update(Submission)
                .where(Submission.id == submission1)
                .values(current_predeadline_version_id=sv1)
            )
            await session.execute(
                update(Submission)
                .where(Submission.id == submission2)
                .values(current_predeadline_version_id=sv2)
            )
            session.add_all(
                [
                    ReviewCase(
                        id=case1,
                        organization_id=org,
                        course_run_id=run1,
                        homework_id=homework,
                        student_id=user,
                    ),
                    ReviewCase(
                        id=case2,
                        organization_id=org,
                        course_run_id=run2,
                        homework_id=homework,
                        student_id=user,
                    ),
                ]
            )
            await session.flush()

            def iteration(identity: UUID, case: UUID, run: UUID, sv: UUID) -> ReviewIteration:
                return ReviewIteration(
                    id=identity,
                    organization_id=org,
                    review_case_id=case,
                    course_run_id=run,
                    homework_id=homework,
                    student_id=user,
                    iteration_number=1,
                    submission_version_id=sv,
                    artifact_version_id=artifact,
                    homework_version_id=hv,
                    criterion_set_id=criterion_set,
                    effective_deadline=now + timedelta(days=1),
                    status="queued",
                    current_revision_id=None,
                    predecessor_iteration_id=None,
                    origin="initial",
                )

            session.add_all(
                [iteration(iteration1, case1, run1, sv1), iteration(iteration2, case2, run2, sv2)]
            )
            await session.flush()
            await session.execute(
                update(ReviewCase)
                .where(ReviewCase.id == case1)
                .values(current_iteration_id=iteration1)
            )
            await session.execute(
                update(ReviewCase)
                .where(ReviewCase.id == case2)
                .values(current_iteration_id=iteration2)
            )

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        update(Submission)
                        .where(Submission.id == submission1)
                        .values(current_predeadline_version_id=sv2)
                    )
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(
                        update(ReviewCase)
                        .where(ReviewCase.id == case1)
                        .values(current_iteration_id=iteration2)
                    )
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    duplicate_initial = iteration(ids[-1], case1, run1, sv1)
                    duplicate_initial.iteration_number = 2
                    session.add(duplicate_initial)
                    await session.flush([duplicate_initial])
    finally:
        await engine.dispose()
