"""Schema-backed, resumable Stepik course import orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    CourseImportProvider,
    JsonValue,
    ProviderContractError,
    ProviderPayload,
    ProviderPayloadValidator,
)
from review_platform.domain.primitives import (
    canonical_json_sha256,
    require_utc,
    sanitize_error,
    utc_now,
    uuid7,
)
from review_platform.infrastructure.db.models.identity import ExternalIdentity, User
from review_platform.infrastructure.db.models.learning import (
    Course,
    CourseMembership,
    CourseRun,
    ExternalCourseBinding,
)
from review_platform.infrastructure.db.models.operations import Operation, OperationAttempt
from review_platform.infrastructure.db.repositories.identity import (
    ExternalCredentialRepository,
    OrganizationMembershipRepository,
    UserIdentityRepository,
)
from review_platform.infrastructure.db.repositories.learning import (
    CourseMembershipRepository,
    CourseRepository,
    CourseRunRepository,
    ExternalCourseBindingRepository,
)
from review_platform.infrastructure.db.repositories.operations import OperationRepository


class CourseImportError(RuntimeError):
    """Base course-import orchestration error."""


class CourseImportBoundaryError(CourseImportError):
    """Tenant, provider, credential, or immutable input provenance mismatched."""


class CourseImportStateError(CourseImportError):
    """The logical Operation cannot legally be resumed."""


@dataclass(frozen=True, slots=True)
class CourseImportCommand:
    organization_id: UUID
    operation_id: UUID
    credential_binding_id: UUID
    credential_binding_version: int
    provider: str
    external_url: str
    cursor: str | None = None
    page_size: int = 100
    course_binding_version: int = 1
    worker_identity: str = "course-import-worker"


@dataclass(frozen=True, slots=True)
class CourseImportAttemptView:
    attempt_number: int
    state: str
    started_at: datetime
    finished_at: datetime | None
    error: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class CourseImportView:
    operation_id: UUID
    organization_id: UUID
    kind: str
    input_version: str
    state: str
    attempts: tuple[CourseImportAttemptView, ...]
    error: Mapping[str, Any] | None
    course_id: UUID | None
    course_run_ids: tuple[UUID, ...]
    complete: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CourseImportRepositories:
    """Caller-owned repositories bound to one AsyncSession transaction."""

    session: AsyncSession
    operations: OperationRepository
    credentials: ExternalCredentialRepository
    memberships: OrganizationMembershipRepository
    users: UserIdentityRepository
    courses: CourseRepository
    course_runs: CourseRunRepository
    course_memberships: CourseMembershipRepository
    external_bindings: ExternalCourseBindingRepository

    @classmethod
    def from_session(cls, session: AsyncSession) -> CourseImportRepositories:
        return cls(
            session=session,
            operations=OperationRepository(session),
            credentials=ExternalCredentialRepository(session),
            memberships=OrganizationMembershipRepository(session),
            users=UserIdentityRepository(session),
            courses=CourseRepository(session),
            course_runs=CourseRunRepository(session),
            course_memberships=CourseMembershipRepository(session),
            external_bindings=ExternalCourseBindingRepository(session),
        )


class CourseImportService:
    """Import one provider page and persist progress in the same transaction."""

    def __init__(
        self,
        *,
        provider: CourseImportProvider,
        validator: ProviderPayloadValidator,
        repositories: CourseImportRepositories,
        id_factory: Callable[[], UUID] = uuid7,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._provider = provider
        self._validator = validator
        self._repositories = repositories
        self._id_factory = id_factory
        self._clock = clock

    async def execute(self, command: CourseImportCommand) -> CourseImportView:
        self._validate_command(command)
        now = require_utc(self._clock())
        await self._validate_credential(command)
        operation, created = await self._operation(command, now=now)
        if not created and operation.state == "succeeded":
            return self._view(operation, complete=True, next_cursor=None)
        if operation.state in {"action_required", "stale"}:
            raise CourseImportStateError(
                f"course import operation in terminal state {operation.state!r} cannot resume"
            )

        attempt_number = await self._repositories.operations.next_attempt_number(
            command.organization_id,
            command.operation_id,
        )
        request = self._provider_request(command)
        try:
            self._validator.validate(
                schema_name="course-import.schema.json",
                definition="request",
                payload=request,
            )
            result = await self._provider.import_course_page(request)
            self._validator.validate(
                schema_name="course-import.schema.json",
                definition="result",
                payload=result,
            )
            self._validate_result_identity(command, result)
        except ProviderContractError as error:
            return await self._record_unexpected_failure(
                command,
                operation,
                attempt_number=attempt_number,
                started_at=now,
                error=error,
                retryable=False,
            )
        except Exception as error:
            return await self._record_unexpected_failure(
                command,
                operation,
                attempt_number=attempt_number,
                started_at=now,
                error=error,
                retryable=True,
            )

        outcome = _required_string(result, "outcome")
        if outcome == "failed":
            raw_error = _required_mapping(result, "error")
            return await self._record_failure_result(
                command,
                operation,
                attempt_number=attempt_number,
                started_at=now,
                raw_error=raw_error,
            )

        course_payload = _required_mapping(result, "course")
        provider_version = _required_string(result, "provider_version")
        course, course_runs = await self._upsert_course_page(
            command,
            course_payload=course_payload,
            memberships=_required_mapping_sequence(result, "memberships"),
            provider_version=provider_version,
            now=now,
        )
        complete = _required_bool(result, "complete")
        next_cursor = _optional_string(result, "next_cursor")
        if complete != (next_cursor is None):
            return await self._record_unexpected_failure(
                command,
                operation,
                attempt_number=attempt_number,
                started_at=now,
                error=CourseImportBoundaryError(
                    "provider completeness and next_cursor contradict each other"
                ),
                retryable=False,
            )
        state = "succeeded" if complete else "partial"
        operation = await self._finish_attempt(
            command,
            operation,
            attempt_number=attempt_number,
            started_at=now,
            attempt_state="succeeded",
            operation_state=state,
            error=None,
        )
        return self._view(
            operation,
            course_id=course.id,
            course_run_ids=tuple(run.id for run in course_runs),
            complete=complete,
            next_cursor=next_cursor,
        )

    def _validate_command(self, command: CourseImportCommand) -> None:
        if command.provider != "stepik":
            raise CourseImportBoundaryError("course import provider must be exactly 'stepik'")
        if self._provider.contract_version != CONTRACT_VERSION:
            raise CourseImportBoundaryError("course import provider contract version mismatched")
        if self._provider.schema_name != "course-import.schema.json":
            raise CourseImportBoundaryError("course import provider schema binding mismatched")
        if command.credential_binding_version < 1 or command.course_binding_version < 1:
            raise CourseImportBoundaryError("binding versions must be positive")
        if not 1 <= command.page_size <= 1000:
            raise CourseImportBoundaryError("page_size must be between 1 and 1000")
        parsed = urlsplit(command.external_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CourseImportBoundaryError("external_url must be an absolute HTTP URL")
        if not command.worker_identity or len(command.worker_identity) > 255:
            raise CourseImportBoundaryError("worker identity must contain 1..255 characters")

    async def _validate_credential(self, command: CourseImportCommand) -> None:
        credential = await self._repositories.credentials.get_exact(
            command.organization_id,
            command.credential_binding_id,
            command.credential_binding_version,
            status="active",
            for_update=True,
        )
        if credential is None or credential.provider != command.provider:
            raise CourseImportBoundaryError(
                "exact active credential binding is unavailable for this tenant/provider"
            )

    async def _operation(
        self,
        command: CourseImportCommand,
        *,
        now: datetime,
    ) -> tuple[Operation, bool]:
        input_version = self._input_version(command)
        operation = await self._repositories.operations.get(
            command.organization_id,
            command.operation_id,
            for_update=True,
        )
        if operation is not None:
            if operation.kind != "course_import" or operation.input_version != input_version:
                raise CourseImportBoundaryError(
                    "operation identity is bound to different immutable import inputs"
                )
            return operation, False
        operation = Operation(
            id=command.operation_id,
            organization_id=command.organization_id,
            kind="course_import",
            input_version=input_version,
            state="pending",
            created_at=now,
            updated_at=now,
        )
        try:
            await self._repositories.operations.add(operation)
        except IntegrityError as error:
            raise CourseImportBoundaryError(
                "operation identity already belongs to another tenant or input"
            ) from error
        return operation, True

    def _provider_request(self, command: CourseImportCommand) -> ProviderPayload:
        return {
            "contract_version": CONTRACT_VERSION,
            "organization_id": str(command.organization_id),
            "operation_id": str(command.operation_id),
            "credential_binding_id": str(command.credential_binding_id),
            "credential_binding_version": command.credential_binding_version,
            "provider": command.provider,
            "external_url": command.external_url,
            "cursor": command.cursor,
            "page_size": command.page_size,
        }

    def _validate_result_identity(
        self,
        command: CourseImportCommand,
        result: ProviderPayload,
    ) -> None:
        if result.get("contract_version") != CONTRACT_VERSION:
            raise ProviderContractError("course import result contract version mismatched")
        if result.get("organization_id") != str(command.organization_id):
            raise ProviderContractError("course import result tenant mismatched")
        if result.get("operation_id") != str(command.operation_id):
            raise ProviderContractError("course import result operation mismatched")

    async def _upsert_course_page(
        self,
        command: CourseImportCommand,
        *,
        course_payload: Mapping[str, JsonValue],
        memberships: Sequence[Mapping[str, JsonValue]],
        provider_version: str,
        now: datetime,
    ) -> tuple[Course, tuple[CourseRun, ...]]:
        if await self._repositories.memberships.lock_organization(command.organization_id) is None:
            raise CourseImportBoundaryError("course import organization does not exist")
        external_course_id = _required_string(course_payload, "external_course_id")
        binding = await self._repositories.external_bindings.get_by_external_identity(
            command.organization_id,
            provider=command.provider,
            external_course_id=external_course_id,
            binding_version=command.course_binding_version,
            for_update=True,
        )
        if binding is None:
            course = Course(
                id=self._id_factory(),
                organization_id=command.organization_id,
                title=_required_string(course_payload, "title"),
                description=_optional_string(course_payload, "description") or "",
                source_kind="external",
                status="active",
            )
            await self._repositories.courses.add(course)
            binding = ExternalCourseBinding(
                id=self._id_factory(),
                organization_id=command.organization_id,
                course_id=course.id,
                provider=command.provider,
                external_course_id=external_course_id,
                external_url=command.external_url,
                provider_version=provider_version,
                credential_id=command.credential_binding_id,
                credential_binding_version=command.credential_binding_version,
                binding_version=command.course_binding_version,
                status="active",
                last_synced_at=now,
            )
            binding = await self._repositories.external_bindings.upsert_imported(binding)
        else:
            exact_binding = await self._repositories.external_bindings.get_exact_credential_binding(
                command.organization_id,
                binding.id,
                binding.binding_version,
                credential_id=command.credential_binding_id,
                credential_binding_version=command.credential_binding_version,
                for_update=True,
            )
            if exact_binding is None:
                raise CourseImportBoundaryError(
                    "existing course binding uses a different exact credential version"
                )
            stored_course = await self._repositories.courses.get(
                command.organization_id,
                binding.course_id,
                for_update=True,
            )
            if stored_course is None:
                raise CourseImportBoundaryError(
                    "external binding points to a missing tenant course"
                )
            course = stored_course
            course.title = _required_string(course_payload, "title")
            course.description = _optional_string(course_payload, "description") or ""
            binding.external_url = command.external_url
            binding.provider_version = provider_version
            binding.last_synced_at = now

        run_payloads = _required_mapping_sequence(course_payload, "runs")
        course_runs = await self._upsert_runs(
            command.organization_id,
            course,
            run_payloads=run_payloads,
        )
        run_by_external_id = {
            _required_string(payload, "external_run_id"): run
            for payload, run in zip(run_payloads, course_runs, strict=True)
        }
        for membership_payload in memberships:
            external_run_id = _required_string(membership_payload, "external_run_id")
            try:
                course_run = run_by_external_id[external_run_id]
            except KeyError as error:
                raise CourseImportBoundaryError(
                    "roster row refers to an unknown course-run identity"
                ) from error
            user = await self._upsert_student(command, membership_payload)
            existing = await self._repositories.course_memberships.get_by_identity(
                command.organization_id,
                course_run.id,
                user.id,
                "student",
                for_update=True,
            )
            joined_at = existing.joined_at if existing is not None else now
            status = _required_string(membership_payload, "status")
            await self._repositories.course_memberships.upsert_imported(
                CourseMembership(
                    id=existing.id if existing is not None else self._id_factory(),
                    organization_id=command.organization_id,
                    course_run_id=course_run.id,
                    user_id=user.id,
                    kind="student",
                    source="imported",
                    status=status,
                    external_version=provider_version,
                    joined_at=joined_at,
                    removed_at=now if status == "removed" else None,
                )
            )
        await self._repositories.session.flush()
        return course, course_runs

    async def _upsert_runs(
        self,
        organization_id: UUID,
        course: Course,
        *,
        run_payloads: Sequence[Mapping[str, JsonValue]],
    ) -> tuple[CourseRun, ...]:
        existing_runs = list(
            await self._repositories.course_runs.list_for_course(
                organization_id,
                course.id,
                statuses=("draft", "active", "archived"),
            )
        )
        available: dict[str, CourseRun] = {}
        for run in existing_runs:
            if run.external_run_id is None:
                continue
            if run.external_run_id in available:
                raise CourseImportBoundaryError(
                    "duplicate persisted external course-run identity"
                )
            available[run.external_run_id] = run
        results: list[CourseRun] = []
        seen_external_ids: set[str] = set()
        for payload in run_payloads:
            external_run_id = _required_string(payload, "external_run_id")
            if external_run_id in seen_external_ids:
                raise CourseImportBoundaryError(
                    "provider returned a duplicate external course-run identity"
                )
            seen_external_ids.add(external_run_id)
            title = _required_string(payload, "title")
            timezone = _required_string(payload, "timezone")
            starts_at = _optional_datetime(payload, "starts_at")
            ends_at = _optional_datetime(payload, "ends_at")
            status = _required_string(payload, "status")
            course_run = available.get(external_run_id)
            if course_run is None:
                course_run = CourseRun(
                    id=self._id_factory(),
                    organization_id=organization_id,
                    course_id=course.id,
                    external_run_id=external_run_id,
                    title=title,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    timezone=timezone,
                    status=status,
                )
                await self._repositories.course_runs.add(course_run)
            else:
                course_run.title = title
                course_run.timezone = timezone
                course_run.starts_at = starts_at
                course_run.ends_at = ends_at
                course_run.status = status
            results.append(course_run)
        return tuple(results)

    async def _upsert_student(
        self,
        command: CourseImportCommand,
        payload: Mapping[str, JsonValue],
    ) -> User:
        subject = _required_string(payload, "external_user_id")
        identity = await self._repositories.users.get_external_identity(
            provider=command.provider,
            issuer="https://stepik.org",
            subject=subject,
            for_update=True,
        )
        display_name = _required_string(payload, "display_name")
        verified_email = _optional_string(payload, "verified_email")
        if identity is not None:
            user = await self._repositories.users.get_user(identity.user_id, for_update=True)
            if user is None:
                raise CourseImportBoundaryError("external identity points to a missing user")
            user.display_name = display_name
            identity.verified_email = verified_email
            return user

        user = User(id=self._id_factory(), display_name=display_name, status="active")
        identity = ExternalIdentity(
            id=self._id_factory(),
            user_id=user.id,
            provider=command.provider,
            issuer="https://stepik.org",
            subject=subject,
            verified_email=verified_email,
            status="active",
        )
        try:
            async with self._repositories.session.begin_nested():
                await self._repositories.users.add_user(user)
                await self._repositories.users.add_external_identity(identity)
        except IntegrityError:
            winner = await self._repositories.users.get_external_identity(
                provider=command.provider,
                issuer="https://stepik.org",
                subject=subject,
                for_update=True,
            )
            if winner is None:
                raise
            existing_user = await self._repositories.users.get_user(
                winner.user_id,
                for_update=True,
            )
            if existing_user is None:
                raise CourseImportBoundaryError(
                    "concurrent external identity points to a missing user"
                ) from None
            return existing_user
        return user

    async def _record_failure_result(
        self,
        command: CourseImportCommand,
        operation: Operation,
        *,
        attempt_number: int,
        started_at: datetime,
        raw_error: Mapping[str, JsonValue],
    ) -> CourseImportView:
        retryable = _required_bool(raw_error, "retryable")
        error = sanitize_error(raw_error)
        operation = await self._finish_attempt(
            command,
            operation,
            attempt_number=attempt_number,
            started_at=started_at,
            attempt_state="retryable_failed" if retryable else "action_required",
            operation_state="retryable_failed" if retryable else "action_required",
            error=error,
        )
        return self._view(operation, complete=False, next_cursor=command.cursor)

    async def _record_unexpected_failure(
        self,
        command: CourseImportCommand,
        operation: Operation,
        *,
        attempt_number: int,
        started_at: datetime,
        error: BaseException,
        retryable: bool,
    ) -> CourseImportView:
        sanitized = sanitize_error(
            {
                "code": "course_import_provider_error"
                if retryable
                else "course_import_contract_error",
                "message": str(error),
                "retryable": retryable,
                "action": "retry" if retryable else "inspect_provider_contract",
            }
        )
        state = "retryable_failed" if retryable else "action_required"
        operation = await self._finish_attempt(
            command,
            operation,
            attempt_number=attempt_number,
            started_at=started_at,
            attempt_state=state,
            operation_state=state,
            error=sanitized,
        )
        return self._view(operation, complete=False, next_cursor=command.cursor)

    async def _finish_attempt(
        self,
        command: CourseImportCommand,
        operation: Operation,
        *,
        attempt_number: int,
        started_at: datetime,
        attempt_state: str,
        operation_state: str,
        error: Mapping[str, Any] | None,
    ) -> Operation:
        finished_at = require_utc(self._clock())
        await self._repositories.operations.add_attempt(
            OperationAttempt(
                id=self._id_factory(),
                organization_id=command.organization_id,
                operation_id=command.operation_id,
                attempt_number=attempt_number,
                worker_identity=command.worker_identity,
                started_at=started_at,
                finished_at=finished_at,
                outcome=attempt_state,
                error_code=str(error.get("code")) if error is not None else None,
                sanitized_error=dict(error) if error is not None else None,
            )
        )
        terminal_finished_at = (
            finished_at if operation_state in {"succeeded", "action_required"} else None
        )
        updated = await self._repositories.operations.compare_and_set_state(
            command.organization_id,
            command.operation_id,
            expected_revision=operation.revision,
            new_state=operation_state,
            finished_at=terminal_finished_at,
            error_code=str(error.get("code")) if error is not None else None,
            sanitized_error=error,
        )
        if not updated:
            raise CourseImportStateError("course import operation revision changed concurrently")
        refreshed = await self._repositories.operations.get(
            command.organization_id,
            command.operation_id,
            for_update=True,
        )
        if refreshed is None:
            raise CourseImportStateError("course import operation disappeared after update")
        await self._repositories.operations.refresh_attempts(refreshed)
        return refreshed

    def _input_version(self, command: CourseImportCommand) -> str:
        digest = canonical_json_sha256(
            {
                "contract_version": CONTRACT_VERSION,
                "organization_id": str(command.organization_id),
                "credential_binding_id": str(command.credential_binding_id),
                "credential_binding_version": command.credential_binding_version,
                "provider": command.provider,
                "external_url": command.external_url,
                "course_binding_version": command.course_binding_version,
            }
        )
        return f"course-import:{CONTRACT_VERSION}:{digest}"

    @staticmethod
    def _view(
        operation: Operation,
        *,
        course_id: UUID | None = None,
        course_run_ids: tuple[UUID, ...] = (),
        complete: bool,
        next_cursor: str | None,
    ) -> CourseImportView:
        return CourseImportView(
            operation_id=operation.id,
            organization_id=operation.organization_id,
            kind=operation.kind,
            input_version=operation.input_version,
            state=operation.state,
            attempts=tuple(
                CourseImportAttemptView(
                    attempt_number=attempt.attempt_number,
                    state=attempt.outcome,
                    started_at=attempt.started_at,
                    finished_at=attempt.finished_at,
                    error=attempt.sanitized_error,
                )
                for attempt in operation.attempts
            ),
            error=operation.sanitized_error,
            course_id=course_id,
            course_run_ids=course_run_ids,
            complete=complete,
            next_cursor=next_cursor,
        )


def _required_mapping(payload: Mapping[str, JsonValue], field: str) -> Mapping[str, JsonValue]:
    value = payload.get(field)
    if not isinstance(value, Mapping):
        raise ProviderContractError(f"course import {field} must be an object")
    return cast(Mapping[str, JsonValue], value)


def _required_mapping_sequence(
    payload: Mapping[str, JsonValue],
    field: str,
) -> tuple[Mapping[str, JsonValue], ...]:
    value = payload.get(field)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ProviderContractError(f"course import {field} must be an object array")
    return tuple(cast(Mapping[str, JsonValue], item) for item in value)


def _required_string(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ProviderContractError(f"course import {field} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, JsonValue], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderContractError(f"course import {field} must be a string or null")
    return value


def _required_bool(payload: Mapping[str, JsonValue], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ProviderContractError(f"course import {field} must be a boolean")
    return value


def _optional_datetime(payload: Mapping[str, JsonValue], field: str) -> datetime | None:
    value = _optional_string(payload, field)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderContractError(f"course import {field} must be a date-time") from error
    return require_utc(parsed)


__all__ = [
    "CourseImportAttemptView",
    "CourseImportBoundaryError",
    "CourseImportCommand",
    "CourseImportError",
    "CourseImportRepositories",
    "CourseImportService",
    "CourseImportStateError",
    "CourseImportView",
]
