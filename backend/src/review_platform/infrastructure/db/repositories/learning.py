"""Tenant-scoped persistence for courses, runs, rosters, and bindings.

Every public read/update takes ``organization_id`` even when a UUID is globally
unique.  Repositories flush but never commit; transaction ownership stays with
the application service so audit/outbox writes remain atomic.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.infrastructure.db.models.learning import (
    ARCHIVE_STATUSES,
    COURSE_MEMBERSHIP_KINDS,
    COURSE_MEMBERSHIP_STATUSES,
    COURSE_RUN_STATUSES,
    Course,
    CourseMembership,
    CourseRun,
    DestinationBinding,
    ExternalCourseBinding,
)

type ArchiveStatus = Literal["active", "archived"]
type CourseRunStatus = Literal["draft", "active", "archived"]
type CourseMembershipKind = Literal["student", "reviewer"]
type CourseMembershipSource = Literal["imported", "invitation", "self_selected"]
type CourseMembershipStatus = Literal["active", "removed", "archived"]

_SAFE_CACHE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


class LearningRepositoryError(RuntimeError):
    """Base repository boundary error."""


class LearningRepositoryConflict(LearningRepositoryError):
    """An upsert identity conflicts with an existing immutable identity."""


def tenant_cache_key(
    organization_id: UUID | str,
    category: str,
    *identity: UUID | str | int,
) -> str:
    """Build an unambiguous tenant-first Redis/cache key."""

    try:
        organization = str(UUID(str(organization_id)))
    except ValueError as error:
        raise ValueError("cache key organization_id must be a UUID") from error
    segments = (organization, category, *(str(part) for part in identity))
    if not identity:
        raise ValueError("cache key requires at least one entity identity segment")
    if any(_SAFE_CACHE_SEGMENT.fullmatch(segment) is None for segment in segments):
        raise ValueError("cache key contains an unsafe or empty segment")
    return "review-platform:" + ":".join(segments)


def course_cache_key(organization_id: UUID | str, course_id: UUID | str) -> str:
    return tenant_cache_key(organization_id, "course", course_id)


def course_run_cache_key(organization_id: UUID | str, course_run_id: UUID | str) -> str:
    return tenant_cache_key(organization_id, "course-run", course_run_id)


def course_membership_cache_key(
    organization_id: UUID | str,
    course_run_id: UUID | str,
    user_id: UUID | str,
    kind: CourseMembershipKind,
) -> str:
    _validated_values((kind,), COURSE_MEMBERSHIP_KINDS, field="membership kind")
    return tenant_cache_key(
        organization_id,
        "course-membership",
        course_run_id,
        user_id,
        kind,
    )


def external_course_binding_cache_key(
    organization_id: UUID | str,
    binding_id: UUID | str,
    binding_version: int,
) -> str:
    _positive_version(binding_version)
    return tenant_cache_key(
        organization_id,
        "external-course-binding",
        binding_id,
        binding_version,
    )


def destination_binding_cache_key(
    organization_id: UUID | str,
    binding_id: UUID | str,
    binding_version: int,
) -> str:
    _positive_version(binding_version)
    return tenant_cache_key(
        organization_id,
        "destination-binding",
        binding_id,
        binding_version,
    )


class CourseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, course: Course) -> Course:
        self._session.add(course)
        await self._session.flush()
        return course

    async def get(
        self,
        organization_id: UUID,
        course_id: UUID,
        *,
        for_update: bool = False,
    ) -> Course | None:
        statement = select(Course).where(
            Course.organization_id == organization_id,
            Course.id == course_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: UUID,
        *,
        statuses: Collection[ArchiveStatus],
    ) -> Sequence[Course]:
        selected = _validated_values(statuses, ARCHIVE_STATUSES, field="course status")
        result = await self._session.execute(
            select(Course)
            .where(
                Course.organization_id == organization_id,
                Course.status.in_(selected),
            )
            .order_by(Course.title, Course.id)
        )
        return result.scalars().all()

    async def compare_and_set_status(
        self,
        organization_id: UUID,
        course_id: UUID,
        *,
        expected_revision: int,
        new_status: ArchiveStatus,
    ) -> bool:
        _validated_values((new_status,), ARCHIVE_STATUSES, field="course status")
        if expected_revision < 0:
            raise ValueError("expected revision must be nonnegative")
        result = await self._session.execute(
            update(Course)
            .where(
                Course.organization_id == organization_id,
                Course.id == course_id,
                Course.revision == expected_revision,
            )
            .values(
                status=new_status,
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1


class CourseRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, course_run: CourseRun) -> CourseRun:
        self._session.add(course_run)
        await self._session.flush()
        return course_run

    async def get(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        for_update: bool = False,
    ) -> CourseRun | None:
        statement = select(CourseRun).where(
            CourseRun.organization_id == organization_id,
            CourseRun.id == course_run_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_course(
        self,
        organization_id: UUID,
        course_id: UUID,
        *,
        statuses: Collection[CourseRunStatus],
    ) -> Sequence[CourseRun]:
        selected = _validated_values(statuses, COURSE_RUN_STATUSES, field="course-run status")
        result = await self._session.execute(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.course_id == course_id,
                CourseRun.status.in_(selected),
            )
            .order_by(CourseRun.title, CourseRun.id)
        )
        return result.scalars().all()

    async def list_for_organization(
        self,
        organization_id: UUID,
        *,
        statuses: Collection[CourseRunStatus],
    ) -> Sequence[CourseRun]:
        selected = _validated_values(statuses, COURSE_RUN_STATUSES, field="course-run status")
        result = await self._session.execute(
            select(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.status.in_(selected),
            )
            .order_by(CourseRun.course_id, CourseRun.title, CourseRun.id)
        )
        return result.scalars().all()

    async def compare_and_set_status(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        expected_revision: int,
        new_status: CourseRunStatus,
    ) -> bool:
        _validated_values((new_status,), COURSE_RUN_STATUSES, field="course-run status")
        if expected_revision < 0:
            raise ValueError("expected revision must be nonnegative")
        result = await self._session.execute(
            update(CourseRun)
            .where(
                CourseRun.organization_id == organization_id,
                CourseRun.id == course_run_id,
                CourseRun.revision == expected_revision,
            )
            .values(
                status=new_status,
                revision=expected_revision + 1,
                updated_at=func.current_timestamp(),
            )
        )
        return result.rowcount == 1


class CourseMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, membership: CourseMembership) -> CourseMembership:
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def get(
        self,
        organization_id: UUID,
        membership_id: UUID,
        *,
        for_update: bool = False,
    ) -> CourseMembership | None:
        statement = select(CourseMembership).where(
            CourseMembership.organization_id == organization_id,
            CourseMembership.id == membership_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_identity(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        user_id: UUID,
        kind: CourseMembershipKind,
        *,
        for_update: bool = False,
    ) -> CourseMembership | None:
        _validated_values((kind,), COURSE_MEMBERSHIP_KINDS, field="membership kind")
        statement = select(CourseMembership).where(
            CourseMembership.organization_id == organization_id,
            CourseMembership.course_run_id == course_run_id,
            CourseMembership.user_id == user_id,
            CourseMembership.kind == kind,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def list_for_course_run(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        statuses: Collection[CourseMembershipStatus],
        kinds: Collection[CourseMembershipKind],
    ) -> Sequence[CourseMembership]:
        selected_statuses = _validated_values(
            statuses,
            COURSE_MEMBERSHIP_STATUSES,
            field="membership status",
        )
        selected_kinds = _validated_values(
            kinds,
            COURSE_MEMBERSHIP_KINDS,
            field="membership kind",
        )
        result = await self._session.execute(
            select(CourseMembership)
            .where(
                CourseMembership.organization_id == organization_id,
                CourseMembership.course_run_id == course_run_id,
                CourseMembership.status.in_(selected_statuses),
                CourseMembership.kind.in_(selected_kinds),
            )
            .order_by(CourseMembership.user_id, CourseMembership.kind, CourseMembership.id)
        )
        return result.scalars().all()

    async def list_for_user(
        self,
        organization_id: UUID,
        user_id: UUID,
        *,
        statuses: Collection[CourseMembershipStatus],
        kinds: Collection[CourseMembershipKind],
    ) -> Sequence[CourseMembership]:
        selected_statuses = _validated_values(
            statuses,
            COURSE_MEMBERSHIP_STATUSES,
            field="membership status",
        )
        selected_kinds = _validated_values(
            kinds,
            COURSE_MEMBERSHIP_KINDS,
            field="membership kind",
        )
        result = await self._session.execute(
            select(CourseMembership)
            .where(
                CourseMembership.organization_id == organization_id,
                CourseMembership.user_id == user_id,
                CourseMembership.status.in_(selected_statuses),
                CourseMembership.kind.in_(selected_kinds),
            )
            .order_by(CourseMembership.course_run_id, CourseMembership.kind, CourseMembership.id)
        )
        return result.scalars().all()

    async def upsert_imported(self, membership: CourseMembership) -> CourseMembership:
        if membership.source != "imported":
            raise ValueError("imported roster upsert requires source='imported'")
        _validated_values(
            (membership.kind,),
            COURSE_MEMBERSHIP_KINDS,
            field="membership kind",
        )
        _validated_values(
            (membership.status,),
            COURSE_MEMBERSHIP_STATUSES,
            field="membership status",
        )
        existing = await self.get_by_identity(
            membership.organization_id,
            membership.course_run_id,
            membership.user_id,
            cast(CourseMembershipKind, membership.kind),
            for_update=True,
        )
        if existing is not None and _same_membership_state(existing, membership):
            return existing
        statement = mysql_insert(CourseMembership).values(
            id=membership.id,
            organization_id=membership.organization_id,
            course_run_id=membership.course_run_id,
            user_id=membership.user_id,
            kind=membership.kind,
            source=membership.source,
            status=membership.status,
            external_version=membership.external_version,
            joined_at=membership.joined_at,
            removed_at=membership.removed_at,
        )
        await self._session.execute(
            statement.on_duplicate_key_update(
                source=statement.inserted.source,
                status=statement.inserted.status,
                external_version=statement.inserted.external_version,
                joined_at=statement.inserted.joined_at,
                removed_at=statement.inserted.removed_at,
                updated_at=func.current_timestamp(),
            )
        )
        result = await self.get_by_identity(
            membership.organization_id,
            membership.course_run_id,
            membership.user_id,
            cast(CourseMembershipKind, membership.kind),
        )
        if result is None:
            raise LearningRepositoryConflict("roster upsert collided with another immutable ID")
        return result


class ExternalCourseBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, binding: ExternalCourseBinding) -> ExternalCourseBinding:
        self._session.add(binding)
        await self._session.flush()
        return binding

    async def get(
        self,
        organization_id: UUID,
        binding_id: UUID,
        binding_version: int,
        *,
        for_update: bool = False,
    ) -> ExternalCourseBinding | None:
        _positive_version(binding_version)
        statement = select(ExternalCourseBinding).where(
            ExternalCourseBinding.organization_id == organization_id,
            ExternalCourseBinding.id == binding_id,
            ExternalCourseBinding.binding_version == binding_version,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def get_by_external_identity(
        self,
        organization_id: UUID,
        *,
        provider: str,
        external_course_id: str,
        binding_version: int,
        for_update: bool = False,
    ) -> ExternalCourseBinding | None:
        _positive_version(binding_version)
        if not provider or not external_course_id:
            raise ValueError("provider and external course identity are required")
        statement = select(ExternalCourseBinding).where(
            ExternalCourseBinding.organization_id == organization_id,
            ExternalCourseBinding.provider == provider,
            ExternalCourseBinding.external_course_id == external_course_id,
            ExternalCourseBinding.binding_version == binding_version,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def get_exact_credential_binding(
        self,
        organization_id: UUID,
        binding_id: UUID,
        binding_version: int,
        *,
        credential_id: UUID,
        credential_binding_version: int,
        for_update: bool = False,
    ) -> ExternalCourseBinding | None:
        _positive_version(credential_binding_version)
        statement = _external_binding_identity_statement(
            organization_id,
            binding_id,
            binding_version,
        ).where(
            ExternalCourseBinding.credential_id == credential_id,
            ExternalCourseBinding.credential_binding_version == credential_binding_version,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def list_for_course(
        self,
        organization_id: UUID,
        course_id: UUID,
        *,
        statuses: Collection[ArchiveStatus],
    ) -> Sequence[ExternalCourseBinding]:
        selected = _validated_values(
            statuses,
            ARCHIVE_STATUSES,
            field="external course binding status",
        )
        result = await self._session.execute(
            select(ExternalCourseBinding)
            .where(
                ExternalCourseBinding.organization_id == organization_id,
                ExternalCourseBinding.course_id == course_id,
                ExternalCourseBinding.status.in_(selected),
            )
            .order_by(
                ExternalCourseBinding.provider,
                ExternalCourseBinding.external_course_id,
                ExternalCourseBinding.binding_version,
                ExternalCourseBinding.id,
            )
        )
        return result.scalars().all()

    async def upsert_imported(
        self,
        binding: ExternalCourseBinding,
    ) -> ExternalCourseBinding:
        _positive_version(binding.binding_version)
        _positive_version(binding.credential_binding_version)
        _validated_values(
            (binding.status,),
            ARCHIVE_STATUSES,
            field="external course binding status",
        )
        by_id = await self.get(
            binding.organization_id,
            binding.id,
            binding.binding_version,
            for_update=True,
        )
        if by_id is not None and (
            by_id.provider != binding.provider
            or by_id.external_course_id != binding.external_course_id
            or by_id.course_id != binding.course_id
        ):
            raise LearningRepositoryConflict(
                "external course binding ID is already attached to another immutable identity"
            )
        existing = await self.get_by_external_identity(
            binding.organization_id,
            provider=binding.provider,
            external_course_id=binding.external_course_id,
            binding_version=binding.binding_version,
            for_update=True,
        )
        if existing is not None and _same_external_binding_state(existing, binding):
            return existing

        statement = mysql_insert(ExternalCourseBinding).values(
            id=binding.id,
            organization_id=binding.organization_id,
            course_id=binding.course_id,
            provider=binding.provider,
            external_course_id=binding.external_course_id,
            external_url=binding.external_url,
            provider_version=binding.provider_version,
            credential_id=binding.credential_id,
            credential_binding_version=binding.credential_binding_version,
            binding_version=binding.binding_version,
            status=binding.status,
            last_synced_at=binding.last_synced_at,
        )
        await self._session.execute(
            statement.on_duplicate_key_update(
                external_url=statement.inserted.external_url,
                provider_version=statement.inserted.provider_version,
                credential_id=statement.inserted.credential_id,
                credential_binding_version=statement.inserted.credential_binding_version,
                status=statement.inserted.status,
                last_synced_at=statement.inserted.last_synced_at,
                updated_at=func.current_timestamp(),
            )
        )
        result = await self.get_by_external_identity(
            binding.organization_id,
            provider=binding.provider,
            external_course_id=binding.external_course_id,
            binding_version=binding.binding_version,
        )
        if result is None:
            raise LearningRepositoryConflict(
                "external course upsert collided with another immutable binding"
            )
        return result


class DestinationBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, binding: DestinationBinding) -> DestinationBinding:
        self._session.add(binding)
        await self._session.flush()
        return binding

    async def get(
        self,
        organization_id: UUID,
        binding_id: UUID,
        binding_version: int,
        *,
        for_update: bool = False,
    ) -> DestinationBinding | None:
        _positive_version(binding_version)
        statement = select(DestinationBinding).where(
            DestinationBinding.organization_id == organization_id,
            DestinationBinding.id == binding_id,
            DestinationBinding.binding_version == binding_version,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_exact_credential_binding(
        self,
        organization_id: UUID,
        binding_id: UUID,
        binding_version: int,
        *,
        credential_id: UUID,
        credential_binding_version: int,
        for_update: bool = False,
    ) -> DestinationBinding | None:
        _positive_version(credential_binding_version)
        statement = _destination_binding_identity_statement(
            organization_id,
            binding_id,
            binding_version,
        ).where(
            DestinationBinding.credential_id == credential_id,
            DestinationBinding.credential_binding_version == credential_binding_version,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_course_run(
        self,
        organization_id: UUID,
        course_run_id: UUID,
        *,
        statuses: Collection[ArchiveStatus],
        required_only: bool,
    ) -> Sequence[DestinationBinding]:
        selected = _validated_values(
            statuses,
            ARCHIVE_STATUSES,
            field="destination binding status",
        )
        statement = select(DestinationBinding).where(
            DestinationBinding.organization_id == organization_id,
            DestinationBinding.course_run_id == course_run_id,
            DestinationBinding.status.in_(selected),
        )
        if required_only:
            statement = statement.where(DestinationBinding.required.is_(True))
        result = await self._session.execute(
            statement.order_by(
                DestinationBinding.kind,
                DestinationBinding.recipient_ref,
                DestinationBinding.binding_version,
                DestinationBinding.id,
            )
        )
        return result.scalars().all()


def _external_binding_identity_statement(
    organization_id: UUID,
    binding_id: UUID,
    binding_version: int,
) -> Select[tuple[ExternalCourseBinding]]:
    _positive_version(binding_version)
    return select(ExternalCourseBinding).where(
        ExternalCourseBinding.organization_id == organization_id,
        ExternalCourseBinding.id == binding_id,
        ExternalCourseBinding.binding_version == binding_version,
    )


def _destination_binding_identity_statement(
    organization_id: UUID,
    binding_id: UUID,
    binding_version: int,
) -> Select[tuple[DestinationBinding]]:
    _positive_version(binding_version)
    return select(DestinationBinding).where(
        DestinationBinding.organization_id == organization_id,
        DestinationBinding.id == binding_id,
        DestinationBinding.binding_version == binding_version,
    )


def _validated_values(
    values: Collection[str],
    allowed: tuple[str, ...],
    *,
    field: str,
) -> tuple[str, ...]:
    selected = tuple(sorted(set(values)))
    unknown = set(selected).difference(allowed)
    if unknown:
        raise ValueError(f"unknown {field}: {sorted(unknown)!r}")
    return selected


def _positive_version(version: int) -> None:
    if isinstance(version, bool) or version < 1:
        raise ValueError("binding versions must be positive integers")


def _same_membership_state(
    current: CourseMembership,
    proposed: CourseMembership,
) -> bool:
    return (
        current.source,
        current.status,
        current.external_version,
        current.joined_at,
        current.removed_at,
    ) == (
        proposed.source,
        proposed.status,
        proposed.external_version,
        proposed.joined_at,
        proposed.removed_at,
    )


def _same_external_binding_state(
    current: ExternalCourseBinding,
    proposed: ExternalCourseBinding,
) -> bool:
    return (
        current.external_url,
        current.provider_version,
        current.credential_id,
        current.credential_binding_version,
        current.status,
        current.last_synced_at,
    ) == (
        proposed.external_url,
        proposed.provider_version,
        proposed.credential_id,
        proposed.credential_binding_version,
        proposed.status,
        proposed.last_synced_at,
    )


__all__ = [
    "ArchiveStatus",
    "CourseMembershipKind",
    "CourseMembershipRepository",
    "CourseMembershipSource",
    "CourseMembershipStatus",
    "CourseRepository",
    "CourseRunRepository",
    "CourseRunStatus",
    "DestinationBindingRepository",
    "ExternalCourseBindingRepository",
    "LearningRepositoryConflict",
    "LearningRepositoryError",
    "course_cache_key",
    "course_membership_cache_key",
    "course_run_cache_key",
    "destination_binding_cache_key",
    "external_course_binding_cache_key",
    "tenant_cache_key",
]
