"""MySQL-backed unit boundary for tenant-scoped learning repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from review_platform.infrastructure.db.models.identity import ExternalCredential, User
from review_platform.infrastructure.db.models.learning import (
    Course,
    CourseMembership,
    CourseRun,
    DestinationBinding,
    ExternalCourseBinding,
)
from review_platform.infrastructure.db.repositories.learning import (
    CourseMembershipRepository,
    CourseRepository,
    CourseRunRepository,
    DestinationBindingRepository,
    ExternalCourseBindingRepository,
    course_cache_key,
    course_membership_cache_key,
    course_run_cache_key,
    destination_binding_cache_key,
    external_course_binding_cache_key,
    tenant_cache_key,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
COURSE_A = UUID("00000000-0000-7000-8000-000000000101")
COURSE_B = UUID("00000000-0000-7000-8000-000000000102")
RUN_A = UUID("00000000-0000-7000-8000-000000000111")
RUN_B = UUID("00000000-0000-7000-8000-000000000112")
USER_A = UUID("00000000-0000-7000-8000-000000000121")
USER_B = UUID("00000000-0000-7000-8000-000000000122")
CREDENTIAL_A = UUID("00000000-0000-7000-8000-000000000131")
CREDENTIAL_B = UUID("00000000-0000-7000-8000-000000000132")
EXTERNAL_BINDING_A = UUID("00000000-0000-7000-8000-000000000141")
DESTINATION_A = UUID("00000000-0000-7000-8000-000000000151")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _course(*, organization_id: UUID, course_id: UUID, status: str = "active") -> Course:
    return Course(
        id=course_id,
        organization_id=organization_id,
        title=f"Course {course_id}",
        description="",
        source_kind="external",
        status=status,
        revision=0,
    )


def _run(
    *,
    organization_id: UUID,
    course_id: UUID,
    run_id: UUID,
    status: str = "active",
) -> CourseRun:
    return CourseRun(
        id=run_id,
        organization_id=organization_id,
        course_id=course_id,
        title=f"Run {run_id}",
        starts_at=None,
        ends_at=None,
        timezone="Europe/Moscow",
        status=status,
        revision=0,
    )


async def _seed_courses(factory: AsyncSessionFactory) -> None:
    async with factory.begin() as session:
        session.add_all(
            [
                _course(organization_id=ORG_A, course_id=COURSE_A),
                _course(
                    organization_id=ORG_B,
                    course_id=COURSE_B,
                    status="archived",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _run(organization_id=ORG_A, course_id=COURSE_A, run_id=RUN_A),
                _run(
                    organization_id=ORG_B,
                    course_id=COURSE_B,
                    run_id=RUN_B,
                    status="archived",
                ),
            ]
        )


async def _seed_users(factory: AsyncSessionFactory) -> None:
    async with factory.begin() as session:
        session.add_all(
            [
                User(id=USER_A, display_name="User A", status="active"),
                User(id=USER_B, display_name="User B", status="active"),
            ]
        )


async def _seed_credentials(factory: AsyncSessionFactory) -> None:
    async with factory.begin() as session:
        session.add_all(
            [
                ExternalCredential(
                    id=CREDENTIAL_A,
                    organization_id=ORG_A,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-a",
                    key_id="test-key",
                    status="active",
                    rotated_at=None,
                    revoked_at=None,
                ),
                ExternalCredential(
                    id=CREDENTIAL_B,
                    organization_id=ORG_B,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-b",
                    key_id="test-key",
                    status="active",
                    rotated_at=None,
                    revoked_at=None,
                ),
            ]
        )


def test_cache_keys_are_tenant_first_complete_and_reject_unsafe_segments() -> None:
    expected_prefix = f"review-platform:{ORG_A}:"
    keys = (
        course_cache_key(ORG_A, COURSE_A),
        course_run_cache_key(ORG_A, RUN_A),
        course_membership_cache_key(ORG_A, RUN_A, USER_A, "student"),
        external_course_binding_cache_key(ORG_A, EXTERNAL_BINDING_A, 3),
        destination_binding_cache_key(ORG_A, DESTINATION_A, 4),
    )

    assert all(key.startswith(expected_prefix) for key in keys)
    assert keys == (
        f"{expected_prefix}course:{COURSE_A}",
        f"{expected_prefix}course-run:{RUN_A}",
        f"{expected_prefix}course-membership:{RUN_A}:{USER_A}:student",
        f"{expected_prefix}external-course-binding:{EXTERNAL_BINDING_A}:3",
        f"{expected_prefix}destination-binding:{DESTINATION_A}:4",
    )
    with pytest.raises(ValueError, match="unsafe"):
        tenant_cache_key(str(ORG_A), "course", "../foreign")
    with pytest.raises(ValueError, match="unsafe"):
        tenant_cache_key(str(ORG_A), "course:global", COURSE_A)
    with pytest.raises(ValueError, match="identity"):
        tenant_cache_key(str(ORG_A), "course")
    with pytest.raises(ValueError, match="organization_id"):
        tenant_cache_key("not-a-uuid", "course", COURSE_A)
    with pytest.raises(ValueError, match="membership kind"):
        course_membership_cache_key(
            ORG_A,
            RUN_A,
            USER_A,
            "administrator",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="positive"):
        destination_binding_cache_key(ORG_A, DESTINATION_A, 0)


async def test_course_and_run_reads_are_tenant_scoped_status_explicit_and_cas_locked(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_courses(foundation_session_factory)
    async with foundation_session_factory.begin() as session:
        courses = CourseRepository(session)
        runs = CourseRunRepository(session)

        assert await courses.get(ORG_B, COURSE_A) is None
        assert await runs.get(ORG_B, RUN_A) is None
        assert [row.id for row in await courses.list_for_organization(
            ORG_A, statuses={"active"}
        )] == [COURSE_A]
        assert await courses.list_for_organization(ORG_A, statuses={"archived"}) == []
        assert [row.id for row in await courses.list_for_organization(
            ORG_B, statuses={"active", "archived"}
        )] == [COURSE_B]
        assert [row.id for row in await runs.list_for_course(
            ORG_A, COURSE_A, statuses={"active"}
        )] == [RUN_A]

        assert await courses.compare_and_set_status(
            ORG_A,
            COURSE_A,
            expected_revision=0,
            new_status="archived",
        )
        assert not await courses.compare_and_set_status(
            ORG_A,
            COURSE_A,
            expected_revision=0,
            new_status="active",
        )
        locked = await courses.get(ORG_A, COURSE_A, for_update=True)
        assert locked is not None
        assert (locked.status, locked.revision) == ("archived", 1)

        assert await runs.compare_and_set_status(
            ORG_A,
            RUN_A,
            expected_revision=0,
            new_status="archived",
        )
        assert not await runs.compare_and_set_status(
            ORG_B,
            RUN_A,
            expected_revision=1,
            new_status="active",
        )
        locked_run = await runs.get(ORG_A, RUN_A, for_update=True)
        assert locked_run is not None
        assert (locked_run.status, locked_run.revision) == ("archived", 1)

        with pytest.raises(ValueError, match="unknown course status"):
            await courses.list_for_organization(
                ORG_A,
                statuses={"deleted"},  # type: ignore[arg-type]
            )


async def test_imported_roster_upsert_is_idempotent_and_keeps_history_queryable(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_courses(foundation_session_factory)
    await _seed_users(foundation_session_factory)
    first_id = UUID("00000000-0000-7000-8000-000000000161")
    replacement_id = UUID("00000000-0000-7000-8000-000000000162")

    async with foundation_session_factory.begin() as session:
        repository = CourseMembershipRepository(session)
        first = await repository.upsert_imported(
            CourseMembership(
                id=first_id,
                organization_id=ORG_A,
                course_run_id=RUN_A,
                user_id=USER_A,
                kind="student",
                source="imported",
                status="active",
                external_version="roster-v1",
                joined_at=NOW,
                removed_at=None,
            )
        )
        replay = await repository.upsert_imported(
            CourseMembership(
                id=replacement_id,
                organization_id=ORG_A,
                course_run_id=RUN_A,
                user_id=USER_A,
                kind="student",
                source="imported",
                status="removed",
                external_version="roster-v2",
                joined_at=NOW,
                removed_at=NOW,
            )
        )
        reviewer_id = UUID("00000000-0000-7000-8000-000000000163")
        await repository.add(
            CourseMembership(
                id=reviewer_id,
                organization_id=ORG_A,
                course_run_id=RUN_A,
                user_id=USER_B,
                kind="reviewer",
                source="self_selected",
                status="active",
                external_version=None,
                joined_at=NOW,
                removed_at=None,
            )
        )

        assert first.id == replay.id == first_id
        assert replay.external_version == "roster-v2"
        assert replay.status == "removed"
        assert await repository.get_by_identity(ORG_B, RUN_A, USER_A, "student") is None
        active_rows = await repository.list_for_course_run(
            ORG_A,
            RUN_A,
            statuses={"active"},
            kinds={"student", "reviewer"},
        )
        assert [(row.id, row.kind) for row in active_rows] == [(reviewer_id, "reviewer")]
        history = await repository.list_for_course_run(
            ORG_A,
            RUN_A,
            statuses={"active", "removed", "archived"},
            kinds={"student", "reviewer"},
        )
        assert [(row.id, row.kind, row.status) for row in history] == [
            (first_id, "student", "removed"),
            (reviewer_id, "reviewer", "active"),
        ]
        student_count = await session.scalar(
            select(func.count())
            .select_from(CourseMembership)
            .where(
                CourseMembership.organization_id == ORG_A,
                CourseMembership.course_run_id == RUN_A,
                CourseMembership.user_id == USER_A,
                CourseMembership.kind == "student",
            )
        )
        assert student_count == 1


async def test_external_course_binding_upsert_preserves_identity_and_exact_credential_version(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_courses(foundation_session_factory)
    await _seed_credentials(foundation_session_factory)

    async with foundation_session_factory.begin() as session:
        repository = ExternalCourseBindingRepository(session)
        first = await repository.upsert_imported(
            ExternalCourseBinding(
                id=EXTERNAL_BINDING_A,
                organization_id=ORG_A,
                course_id=COURSE_A,
                provider="stepik",
                external_course_id="course-1",
                external_url="https://stepik.org/course/1",
                provider_version="provider-v1",
                credential_id=CREDENTIAL_A,
                credential_binding_version=1,
                binding_version=1,
                status="active",
                last_synced_at=NOW,
            )
        )
        replay = await repository.upsert_imported(
            ExternalCourseBinding(
                id=EXTERNAL_BINDING_A,
                organization_id=ORG_A,
                course_id=COURSE_A,
                provider="stepik",
                external_course_id="course-1",
                external_url="https://stepik.org/course/1?updated=1",
                provider_version="provider-v2",
                credential_id=CREDENTIAL_A,
                credential_binding_version=1,
                binding_version=1,
                status="active",
                last_synced_at=NOW,
            )
        )
        await repository.add(
            ExternalCourseBinding(
                id=EXTERNAL_BINDING_A,
                organization_id=ORG_A,
                course_id=COURSE_A,
                provider="stepik",
                external_course_id="course-1",
                external_url="https://stepik.org/course/1",
                provider_version="provider-v2-archived",
                credential_id=CREDENTIAL_A,
                credential_binding_version=1,
                binding_version=2,
                status="archived",
                last_synced_at=NOW,
            )
        )

        assert first.id == replay.id == EXTERNAL_BINDING_A
        assert replay.provider_version == "provider-v2"
        assert await repository.get(ORG_B, EXTERNAL_BINDING_A, 1) is None
        assert await repository.get_exact_credential_binding(
            ORG_A,
            EXTERNAL_BINDING_A,
            1,
            credential_id=CREDENTIAL_A,
            credential_binding_version=1,
        ) is not None
        assert await repository.get_exact_credential_binding(
            ORG_A,
            EXTERNAL_BINDING_A,
            1,
            credential_id=CREDENTIAL_A,
            credential_binding_version=2,
        ) is None
        history = await repository.list_for_course(
            ORG_A,
            COURSE_A,
            statuses={"active", "archived"},
        )
        assert [(row.binding_version, row.status) for row in history] == [
            (1, "active"),
            (2, "archived"),
        ]
        count = await session.scalar(select(func.count()).select_from(ExternalCourseBinding))
        assert count == 2


async def test_destination_binding_lookup_requires_tenant_and_both_exact_versions(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_courses(foundation_session_factory)
    await _seed_credentials(foundation_session_factory)

    async with foundation_session_factory.begin() as session:
        repository = DestinationBindingRepository(session)
        await repository.add(
            DestinationBinding(
                id=DESTINATION_A,
                organization_id=ORG_A,
                course_run_id=RUN_A,
                kind="stepik",
                binding_version=1,
                recipient_ref="submission-1",
                credential_id=CREDENTIAL_A,
                credential_binding_version=1,
                required=True,
                status="active",
                revision=0,
            )
        )
        await repository.add(
            DestinationBinding(
                id=DESTINATION_A,
                organization_id=ORG_A,
                course_run_id=RUN_A,
                kind="stepik",
                binding_version=2,
                recipient_ref="submission-1",
                credential_id=CREDENTIAL_A,
                credential_binding_version=1,
                required=True,
                status="archived",
                revision=0,
            )
        )

        assert await repository.get(ORG_B, DESTINATION_A, 1) is None
        assert await repository.get(ORG_A, DESTINATION_A, 3) is None
        assert await repository.get(ORG_A, DESTINATION_A, 2) is not None
        assert await repository.get_exact_credential_binding(
            ORG_A,
            DESTINATION_A,
            1,
            credential_id=CREDENTIAL_A,
            credential_binding_version=1,
            for_update=True,
        ) is not None
        assert await repository.get_exact_credential_binding(
            ORG_A,
            DESTINATION_A,
            1,
            credential_id=CREDENTIAL_A,
            credential_binding_version=2,
        ) is None
        active = await repository.list_for_course_run(
            ORG_A,
            RUN_A,
            statuses={"active"},
            required_only=True,
        )
        archived = await repository.list_for_course_run(
            ORG_A,
            RUN_A,
            statuses={"archived"},
            required_only=False,
        )
        assert [binding.id for binding in active] == [DESTINATION_A]
        assert [(binding.binding_version, binding.status) for binding in archived] == [
            (2, "archived")
        ]
        assert session.in_transaction()
