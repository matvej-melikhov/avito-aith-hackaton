"""Frozen US1 identity, organization, course, and membership HTTP routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, cast
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Cookie, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import AuthorizationError, Authorizer
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.idempotency import IdempotencyCoordinator, IdempotencyError
from review_platform.application.ports.providers import CONTRACT_VERSION, IdentityProvider
from review_platform.application.request_context import RequestActor, Role
from review_platform.application.services.authentication import (
    AuthenticationError,
    AuthenticationService,
    SessionAuthenticationResult,
    SessionView,
)
from review_platform.application.services.courses import CourseService, CourseServiceError
from review_platform.application.services.invitations import (
    InvitationEmailIntentPort,
    InvitationSecretProtector,
    InvitationService,
    InvitationServiceError,
)
from review_platform.application.services.memberships import (
    MembershipService,
    MembershipServiceError,
)
from review_platform.contracts.commands import (
    ChangeMembershipRolesPayload,
    CreateInvitationPayload,
    ReasonPayload,
    StartCourseImportPayload,
    WireCommand,
)
from review_platform.domain.primitives import canonical_json_sha256, require_utc
from review_platform.infrastructure.db.adapters import (
    SqlAppendOnlyAuditRepository,
    SqlIdempotencyReceiptRepository,
)
from review_platform.infrastructure.db.models.identity import OrganizationMembership
from review_platform.infrastructure.db.models.operations import Operation
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.outbox import OutboxDraft, OutboxError, OutboxService
from review_platform.infrastructure.db.repositories.identity import (
    InvitationRepository,
    OrganizationMembershipRepository,
)
from review_platform.infrastructure.db.repositories.operations import (
    OperationRepository,
    OutboxMessageRepository,
)

router = APIRouter(tags=["identity", "courses"])
SESSION_COOKIE = "review_session"


class RouteConfigurationError(RuntimeError):
    """A required server-owned route dependency is not composed."""


class RouteBoundaryError(ValueError):
    """Wire command, route command, and path target are not exact."""


@router.get("/v1/auth/stepik/start", operation_id="startStepikAuthentication")
async def start_stepik_authentication(request: Request) -> Response:
    try:
        runtime = _runtime(request)
        organization_id = _installation_organization_id(request)
        async with runtime.transaction() as transaction:
            service = _authentication_service(request, transaction, runtime)
            result = await service.start_stepik_oauth(
                organization_id=organization_id,
                credential_binding_id=_state_uuid(request, "stepik_credential_binding_id"),
                credential_binding_version=_state_int(
                    request, "stepik_credential_binding_version"
                ),
                redirect_uri=_state_str(request, "stepik_redirect_uri"),
                pkce_verifier_ciphertext=_state_str(
                    request, "stepik_pkce_verifier_ciphertext"
                ),
            )
        location = _state_str(request, "stepik_authorization_url")
        separator = "&" if "?" in location else "?"
        query = urlencode({"state": result.state_secret})
        return RedirectResponse(f"{location}{separator}{query}", status_code=302)
    except _PROTOCOL_ERRORS as error:
        return _error_response(error, protocol=True)


@router.post("/v1/auth/stepik/callback", operation_id="completeStepikAuthentication")
async def complete_stepik_authentication(
    request: Request,
    state: str = Query(min_length=1),
    code: str = Query(min_length=1),
) -> Response:
    try:
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            result = await _authentication_service(
                request, transaction, runtime
            ).complete_stepik_oauth(
                organization_id=_installation_organization_id(request),
                state_secret=state,
                authorization_response=code,
            )
        return _session_response(result)
    except _PROTOCOL_ERRORS as error:
        return _error_response(error, protocol=True)


@router.post("/v1/auth/reviewer/magic-link", operation_id="consumeReviewerMagicLink")
async def consume_reviewer_magic_link(
    request: Request,
    body: Mapping[str, Any],
) -> Response:
    try:
        if set(body) != {"token", "state_id"}:
            raise RouteBoundaryError("magic-link body must contain exact token and state_id")
        token = body["token"]
        if not isinstance(token, str) or not 32 <= len(token) <= 4096:
            raise RouteBoundaryError("magic-link token is invalid")
        state_id = UUID(str(body["state_id"]))
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            result = await _authentication_service(
                request, transaction, runtime
            ).consume_reviewer_magic_link(
                organization_id=_installation_organization_id(request),
                invitation_token=token,
                state_id=state_id,
            )
        return _session_response(result)
    except _PROTOCOL_ERRORS as error:
        return _error_response(error, protocol=True)


@router.get("/v1/session", operation_id="getCurrentSession")
async def get_current_session(
    request: Request,
    session_secret: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Response:
    try:
        secret = _require_session_secret(session_secret)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            view = await _authentication_service(
                request, transaction, runtime
            ).current_session(
                organization_id=_installation_organization_id(request),
                session_secret=secret,
            )
        return JSONResponse(_session_json(view))
    except _PROTOCOL_ERRORS as error:
        return _error_response(error, protocol=True)


@router.delete("/v1/session", operation_id="revokeCurrentSession", status_code=204)
async def revoke_current_session(
    request: Request,
    session_secret: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> Response:
    try:
        secret = _require_session_secret(session_secret)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            await _authentication_service(
                request, transaction, runtime
            ).logout_current_session(
                organization_id=_installation_organization_id(request),
                session_secret=secret,
            )
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/api")
        return response
    except _PROTOCOL_ERRORS as error:
        return _error_response(error, protocol=True)


@router.get("/v1/organization", operation_id="getOrganization")
async def get_organization(request: Request) -> Response:
    try:
        actor = _actor(request)
        runtime = _runtime(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            organization = await transaction.get(Organization, actor.organization_id)
            if organization is None:
                raise RouteBoundaryError("tenant organization was not found")
            payload = {
                "id": str(organization.id),
                "name": organization.name,
                "status": organization.status,
                "revision": organization.revision,
            }
        return JSONResponse(payload)
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.get("/v1/courses", operation_id="listCourses")
async def list_courses(request: Request) -> Response:
    try:
        actor = _actor(request)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            service = _course_service(request, transaction, runtime)
            courses = await service.list_courses(
                organization_id=actor.organization_id,
                actor=actor,
                statuses={"active", "archived"},
            )
            runs = await _course_runs_for_courses(service, actor=actor, courses=courses)
            payload = {
                "items": [_course_json(course) for course in courses],
                "course_runs": [_course_run_json(course_run) for course_run in runs],
            }
        return JSONResponse(payload)
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.get("/v1/course-runs", operation_id="listCourseRuns")
async def list_course_runs(
    request: Request,
    course_id: Annotated[UUID | None, Query()] = None,
) -> Response:
    try:
        actor = _actor(request)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            service = _course_service(request, transaction, runtime)
            runs: Sequence[Any]
            if course_id is None:
                courses = await service.list_courses(
                    organization_id=actor.organization_id,
                    actor=actor,
                    statuses={"active", "archived"},
                )
                runs = await _course_runs_for_courses(service, actor=actor, courses=courses)
            else:
                runs = await service.list_course_runs(
                    organization_id=actor.organization_id,
                    actor=actor,
                    course_id=course_id,
                    statuses={"draft", "active", "archived"},
                )
        return JSONResponse({"items": [_course_run_json(run) for run in runs]})
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.get("/v1/organization/memberships", operation_id="listOrganizationMemberships")
async def list_organization_memberships(request: Request) -> Response:
    try:
        actor = _methodologist_actor(request)
        runtime = _runtime(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            memberships = await OrganizationMembershipRepository(
                transaction
            ).list_for_organization(actor.organization_id)
        return JSONResponse({"items": [_organization_membership_json(row) for row in memberships]})
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.get("/v1/invitations", operation_id="listInvitations")
async def list_invitations(request: Request) -> Response:
    try:
        actor = _methodologist_actor(request)
        runtime = _runtime(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            invitations = await InvitationRepository(transaction).list_for_organization(
                actor.organization_id
            )
        return JSONResponse({"items": [_invitation_json(row) for row in invitations]})
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.post("/v1/invitations", operation_id="createInvitation", status_code=201)
async def create_invitation(request: Request, body: Mapping[str, Any]) -> Response:
    try:
        actor = _methodologist_actor(request)
        command = _wire_command(body, command_name="create_invitation", path_target_id=None)
        payload = cast(CreateInvitationPayload, command.payload)
        if command.target_id != actor.organization_id:
            raise RouteBoundaryError("invitation command target must be current organization")
        runtime = _runtime(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            await _require_organization_revision(
                transaction,
                organization_id=actor.organization_id,
                expected_revision=command.expected_revision,
            )
            result = await _invitation_service(request, transaction, runtime).issue(
                transaction=transaction,
                actor=actor,
                email=payload.email,
                role=payload.role,
                expires_at=payload.expires_at,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await runtime.user_auth_guard.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
        return JSONResponse(
            {"id": str(result.invitation_id), "revision": result.revision},
            status_code=201,
        )
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.post(
    "/v1/invitations/{invitationId}/revoke",
    operation_id="revokeInvitation",
    status_code=204,
)
async def revoke_invitation(
    invitationId: UUID,
    request: Request,
    body: Mapping[str, Any],
) -> Response:
    try:
        actor = _methodologist_actor(request)
        command = _wire_command(
            body,
            command_name="revoke_invitation",
            path_target_id=invitationId,
        )
        runtime = _runtime(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            await _invitation_service(request, transaction, runtime).revoke(
                transaction=transaction,
                actor=actor,
                invitation_id=invitationId,
                expected_revision=command.expected_revision,
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
            await runtime.user_auth_guard.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
        return Response(status_code=204)
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.post("/v1/courses/imports", operation_id="startCourseImport", status_code=202)
async def start_course_import(request: Request, body: Mapping[str, Any]) -> Response:
    try:
        actor = _methodologist_actor(request)
        command = _wire_command(body, command_name="start_course_import", path_target_id=None)
        payload = cast(StartCourseImportPayload, command.payload)
        if command.target_id != actor.organization_id:
            raise RouteBoundaryError("course-import target must be current organization")
        runtime = _runtime(request)
        await runtime.user_auth_guard.revalidate(actor=actor)
        async with runtime.transaction() as transaction:
            await _require_organization_revision(
                transaction,
                organization_id=actor.organization_id,
                expected_revision=command.expected_revision,
            )
            operation = await _reserve_course_import(
                transaction=transaction,
                runtime=runtime,
                actor=actor,
                command=command,
                payload=payload,
                credential_binding_id=_state_uuid(
                    request, "course_import_credential_binding_id"
                ),
                credential_binding_version=_state_int(
                    request, "course_import_credential_binding_version"
                ),
            )
            await runtime.user_auth_guard.lock_and_revalidate(
                actor=actor,
                transaction=transaction,
            )
        return JSONResponse(_operation_row_json(operation), status_code=202)
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.get(
    "/v1/course-runs/{courseRunId}/memberships",
    operation_id="listCourseRunMemberships",
)
async def list_course_run_memberships(courseRunId: UUID, request: Request) -> Response:
    try:
        actor = _methodologist_actor(request)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            memberships = await _course_service(
                request, transaction, runtime
            ).read_roster(
                organization_id=actor.organization_id,
                actor=actor,
                course_run_id=courseRunId,
                include_history=True,
            )
        return JSONResponse({"items": [_course_membership_json(row) for row in memberships]})
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


@router.post(
    "/v1/courses/{courseId}/archive", operation_id="archiveCourse", status_code=204
)
async def archive_course(courseId: UUID, request: Request, body: Mapping[str, Any]) -> Response:
    return await _change_course(request, body, course_id=courseId, restore=False)


@router.post(
    "/v1/courses/{courseId}/restore", operation_id="restoreCourse", status_code=204
)
async def restore_course(courseId: UUID, request: Request, body: Mapping[str, Any]) -> Response:
    return await _change_course(request, body, course_id=courseId, restore=True)


@router.post(
    "/v1/course-runs/{courseRunId}/archive",
    operation_id="archiveCourseRun",
    status_code=204,
)
async def archive_course_run(
    courseRunId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _change_course_run(request, body, course_run_id=courseRunId, restore=False)


@router.post(
    "/v1/course-runs/{courseRunId}/restore",
    operation_id="restoreCourseRun",
    status_code=204,
)
async def restore_course_run(
    courseRunId: UUID, request: Request, body: Mapping[str, Any]
) -> Response:
    return await _change_course_run(request, body, course_run_id=courseRunId, restore=True)


@router.put(
    "/v1/memberships/{membershipId}/roles",
    operation_id="changeMembershipRoles",
)
async def change_membership_roles(
    membershipId: UUID,
    request: Request,
    body: Mapping[str, Any],
) -> Response:
    try:
        actor = _actor(request)
        command = _wire_command(
            body,
            command_name="change_membership_roles",
            path_target_id=membershipId,
        )
        payload = cast(ChangeMembershipRolesPayload, command.payload)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            result = await _membership_service(request, transaction, runtime).change_roles(
                transaction=transaction,
                actor=actor,
                membership_id=membershipId,
                expected_revision=command.expected_revision,
                roles=cast(Sequence[Role], payload.roles),
                request_id=command.request_id,
                trace_id=runtime.id_factory(),
            )
        return JSONResponse({"id": str(result.membership_id), "revision": result.revision})
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


async def _change_course(
    request: Request,
    body: Mapping[str, Any],
    *,
    course_id: UUID,
    restore: bool,
) -> Response:
    try:
        actor = _methodologist_actor(request)
        command_name = "restore_course" if restore else "archive_course"
        command = _wire_command(body, command_name=command_name, path_target_id=course_id)
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            service = _course_service(request, transaction, runtime)
            if restore:
                await service.restore_course(
                    organization_id=actor.organization_id,
                    course_id=course_id,
                    expected_revision=command.expected_revision,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                )
            else:
                reason = cast(ReasonPayload, command.payload).reason
                await service.archive_course(
                    organization_id=actor.organization_id,
                    course_id=course_id,
                    expected_revision=command.expected_revision,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                    reason=reason,
                )
        return Response(status_code=204)
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


async def _change_course_run(
    request: Request,
    body: Mapping[str, Any],
    *,
    course_run_id: UUID,
    restore: bool,
) -> Response:
    try:
        actor = _methodologist_actor(request)
        command_name = "restore_course_run" if restore else "archive_course_run"
        command = _wire_command(
            body,
            command_name=command_name,
            path_target_id=course_run_id,
        )
        runtime = _runtime(request)
        async with runtime.transaction() as transaction:
            service = _course_service(request, transaction, runtime)
            if restore:
                await service.restore_course_run(
                    organization_id=actor.organization_id,
                    course_run_id=course_run_id,
                    expected_revision=command.expected_revision,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                )
            else:
                reason = cast(ReasonPayload, command.payload).reason
                await service.archive_course_run(
                    organization_id=actor.organization_id,
                    course_run_id=course_run_id,
                    expected_revision=command.expected_revision,
                    actor=actor,
                    request_id=command.request_id,
                    trace_id=runtime.id_factory(),
                    reason=reason,
                )
        return Response(status_code=204)
    except _BUSINESS_ERRORS as error:
        return _error_response(error)


def _wire_command(
    body: Mapping[str, Any],
    *,
    command_name: str,
    path_target_id: UUID | None,
) -> WireCommand:
    command = WireCommand.model_validate(dict(body))
    if command.command_name != command_name:
        raise RouteBoundaryError("route command does not match exact command variant")
    if path_target_id is not None and command.target_id != path_target_id:
        raise RouteBoundaryError("path target does not match command target")
    return command


async def _require_organization_revision(
    transaction: AsyncSession,
    *,
    organization_id: UUID,
    expected_revision: int,
) -> None:
    result = await transaction.execute(
        select(Organization.revision)
        .where(Organization.id == organization_id)
        .with_for_update()
    )
    current_revision = result.scalar_one_or_none()
    if current_revision is None:
        raise RouteBoundaryError("tenant organization was not found")
    if current_revision != expected_revision:
        raise RouteBoundaryError(
            f"expected organization revision {expected_revision}, current is {current_revision}"
        )


async def _reserve_course_import(
    *,
    transaction: AsyncSession,
    runtime: FoundationRuntime,
    actor: RequestActor,
    command: WireCommand,
    payload: StartCourseImportPayload,
    credential_binding_id: UUID,
    credential_binding_version: int,
) -> Operation:
    if actor.user_id is None:
        raise AuthorizationError("course import requires a represented user")
    candidate_operation_id = runtime.id_factory()
    reservation = await IdempotencyCoordinator(
        SqlIdempotencyReceiptRepository(),
        receipt_id_factory=runtime.id_factory,
        result_reference_factory=lambda: {
            "kind": "operation",
            "id": str(candidate_operation_id),
        },
    ).reserve(
        organization_id=actor.organization_id,
        idempotency_key=command.idempotency_key,
        request_id=command.request_id,
        command_name=str(command.command_name),
        target_id=command.target_id,
        expected_revision=command.expected_revision,
        payload=payload,
        transaction=transaction,
    )
    raw_operation_id = reservation.receipt.result_reference.get("id")
    if not isinstance(raw_operation_id, str):
        raise RouteBoundaryError("course-import receipt has no stable Operation identity")
    try:
        operation_id = UUID(raw_operation_id)
    except ValueError as error:
        raise RouteBoundaryError("course-import receipt Operation identity is invalid") from error
    operations = OperationRepository(transaction)
    if reservation.disposition == "replay":
        existing = await operations.get(actor.organization_id, operation_id)
        if existing is None:
            raise RouteBoundaryError("course-import receipt references a missing Operation")
        return existing

    now = require_utc(runtime.clock())
    import_identity = {
        "contract_version": CONTRACT_VERSION,
        "organization_id": str(actor.organization_id),
        "credential_binding_id": str(credential_binding_id),
        "credential_binding_version": credential_binding_version,
        "provider": payload.provider,
        "external_url": payload.external_url,
        "course_binding_version": 1,
    }
    operation = Operation(
        id=operation_id,
        organization_id=actor.organization_id,
        kind="course_import",
        input_version=(
            f"course-import:{CONTRACT_VERSION}:{canonical_json_sha256(import_identity)}"
        ),
        state="pending",
        revision=0,
        created_at=now,
        updated_at=now,
        finished_at=None,
        error_code=None,
        sanitized_error=None,
        attempts=[],
    )
    await operations.add(operation)
    await OutboxService(
        OutboxMessageRepository(transaction),
        token_factory=runtime.id_factory,
        clock=runtime.clock,
    ).create(
        OutboxDraft(
            organization_id=actor.organization_id,
            message_id=runtime.id_factory(),
            aggregate_type="operation",
            aggregate_id=operation_id,
            event_type="CourseImportRequested",
            payload_version=CONTRACT_VERSION,
            payload={
                "contract_version": CONTRACT_VERSION,
                "operation_id": str(operation_id),
                "credential_binding_id": str(credential_binding_id),
                "credential_binding_version": credential_binding_version,
                "provider": payload.provider,
                "external_url": payload.external_url,
                "cursor": None,
                "page_size": 100,
                "course_binding_version": 1,
                "actor": {
                    "type": "user",
                    "user_id": str(actor.user_id),
                    "roles": sorted(actor.roles),
                    "membership_revision": actor.membership_revision,
                    "auth_epoch": actor.auth_epoch,
                },
            },
            available_at=now,
            max_attempts=runtime.settings.provider_max_attempts,
        )
    )
    return operation


def _runtime(request: Request) -> FoundationRuntime:
    runtime = getattr(request.app.state, "foundation_runtime", None)
    if not isinstance(runtime, FoundationRuntime):
        raise RouteConfigurationError("Foundation runtime is not configured")
    return runtime


def _actor(request: Request) -> RequestActor:
    actor = getattr(request.state, "request_actor", None)
    if not isinstance(actor, RequestActor):
        raise AuthenticationError("authenticated request actor is required")
    return actor


def _methodologist_actor(request: Request) -> RequestActor:
    actor = _actor(request)
    if actor.actor_type != "user" or "methodologist" not in actor.roles:
        raise AuthorizationError("methodologist role is required")
    return actor


def _authentication_service(
    request: Request,
    transaction: AsyncSession,
    runtime: FoundationRuntime,
) -> AuthenticationService:
    factory = getattr(request.app.state, "authentication_service_factory", None)
    if callable(factory):
        return cast(Callable[[AsyncSession], AuthenticationService], factory)(transaction)
    provider = getattr(request.app.state, "identity_provider", None)
    if not isinstance(provider, IdentityProvider):
        raise RouteConfigurationError("identity provider is not configured")
    return AuthenticationService(
        transaction,
        identity_provider=provider,
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


def _course_service(
    request: Request,
    transaction: AsyncSession,
    runtime: FoundationRuntime,
) -> CourseService:
    factory = getattr(request.app.state, "course_service_factory", None)
    if callable(factory):
        return cast(Callable[[AsyncSession], CourseService], factory)(transaction)
    return CourseService(
        transaction,
        authorizer=Authorizer(runtime.user_auth_guard, clock=runtime.clock),
        audit=_audit(runtime),
    )


def _invitation_service(
    request: Request,
    transaction: AsyncSession,
    runtime: FoundationRuntime,
) -> InvitationService:
    factory = getattr(request.app.state, "invitation_service_factory", None)
    if callable(factory):
        return cast(Callable[[AsyncSession], InvitationService], factory)(transaction)
    email_intents = getattr(request.app.state, "invitation_email_intents", None)
    secret_protector = getattr(request.app.state, "invitation_secret_protector", None)
    if email_intents is None or secret_protector is None:
        raise RouteConfigurationError("invitation email boundary is not configured")
    return InvitationService(
        email_intents=cast(InvitationEmailIntentPort, email_intents),
        secret_protector=cast(InvitationSecretProtector, secret_protector),
        audit=_audit(runtime),
        magic_link_base_url=_state_str(request, "reviewer_magic_link_base_url"),
        id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


def _membership_service(
    request: Request,
    transaction: AsyncSession,
    runtime: FoundationRuntime,
) -> MembershipService:
    factory = getattr(request.app.state, "membership_service_factory", None)
    if callable(factory):
        return cast(Callable[[AsyncSession], MembershipService], factory)(transaction)
    return MembershipService(
        auth_guard=runtime.user_auth_guard,
        audit=_audit(runtime),
        clock=runtime.clock,
    )


def _audit(runtime: FoundationRuntime) -> AuditRecorder:
    return AuditRecorder(
        SqlAppendOnlyAuditRepository(),
        event_id_factory=runtime.id_factory,
        clock=runtime.clock,
    )


def _installation_organization_id(request: Request) -> UUID:
    actor = getattr(request.state, "request_actor", None)
    if isinstance(actor, RequestActor):
        return actor.organization_id
    return _state_uuid(request, "installation_organization_id")


def _state_uuid(request: Request, name: str) -> UUID:
    value = getattr(request.app.state, name, None)
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as error:
            raise RouteConfigurationError(f"server state {name} is not a UUID") from error
    raise RouteConfigurationError(f"server state {name} is not configured")


def _state_int(request: Request, name: str) -> int:
    value = getattr(request.app.state, name, None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RouteConfigurationError(f"server state {name} is not a positive integer")
    return value


def _state_str(request: Request, name: str) -> str:
    value = getattr(request.app.state, name, None)
    if not isinstance(value, str) or not value:
        raise RouteConfigurationError(f"server state {name} is not configured")
    return value


def _require_session_secret(value: str | None) -> str:
    if value is None or len(value) < 32:
        raise AuthenticationError("active session cookie is required")
    return value


def _session_response(result: SessionAuthenticationResult) -> Response:
    response = Response(status_code=204)
    if result.session_secret is not None:
        response.set_cookie(
            SESSION_COOKIE,
            result.session_secret,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/api",
        )
    return response


async def _course_runs_for_courses(
    service: CourseService,
    *,
    actor: RequestActor,
    courses: Sequence[Any],
) -> tuple[Any, ...]:
    runs: list[Any] = []
    for course in courses:
        runs.extend(
            await service.list_course_runs(
                organization_id=actor.organization_id,
                actor=actor,
                course_id=course.id,
                statuses={"draft", "active", "archived"},
            )
        )
    return tuple(runs)


def _course_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "title": row.title,
        "status": row.status,
        "revision": row.revision,
    }


def _course_run_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "course_id": str(row.course_id),
        "title": row.title,
        "timezone": row.timezone,
        "status": row.status,
        "revision": row.revision,
    }


def _organization_membership_json(row: OrganizationMembership) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "roles": list(row.roles),
        "status": row.status,
        "revision": row.revision,
        "auth_epoch": row.auth_epoch,
    }


def _invitation_json(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "normalized_email": row.normalized_email,
        "role": row.role,
        "status": row.status,
        "expires_at": row.expires_at.isoformat(),
        "revision": row.revision,
    }


def _course_membership_json(row: Any) -> dict[str, Any]:
    return {
        "user_id": str(row.user_id),
        "kind": row.kind,
        "status": row.status,
        "source": row.source,
    }


def _operation_row_json(operation: Operation) -> dict[str, Any]:
    return {
        "id": str(operation.id),
        "kind": operation.kind,
        "input_version": operation.input_version,
        "state": operation.state,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "state": attempt.outcome,
                "started_at": attempt.started_at.isoformat(),
                "finished_at": (
                    attempt.finished_at.isoformat() if attempt.finished_at is not None else None
                ),
                "error": attempt.sanitized_error,
            }
            for attempt in operation.attempts
        ],
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
        "finished_at": (
            operation.finished_at.isoformat() if operation.finished_at is not None else None
        ),
        "error": operation.sanitized_error,
    }


def _session_json(view: SessionView) -> dict[str, Any]:
    return {
        "user_id": str(view.user_id),
        "organization_id": str(view.organization_id),
        "membership_id": str(view.membership_id),
        "roles": list(view.roles),
        "membership_revision": view.membership_revision,
        "auth_epoch": view.auth_epoch,
        "actor_type": view.actor_type,
        "agent_id": view.agent_id,
    }


def _error_response(error: BaseException, *, protocol: bool = False) -> JSONResponse:
    if isinstance(error, RouteConfigurationError):
        return _typed_error(503, "service_unavailable", str(error), "configure_service")
    if protocol or isinstance(error, AuthenticationError):
        return _typed_error(401, "authentication_failed", str(error), None)
    if isinstance(error, AuthorizationError):
        return _typed_error(403, "authorization_denied", str(error), None)
    return _typed_error(409, "command_conflict", str(error), "refresh_and_retry")


def _typed_error(status: int, code: str, message: str, action: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message[:2048], "action": action},
    )


_PROTOCOL_ERRORS = (
    AuthenticationError,
    RouteBoundaryError,
    RouteConfigurationError,
    ValidationError,
    ValueError,
)
_BUSINESS_ERRORS = (
    AuthenticationError,
    AuthorizationError,
    CourseServiceError,
    IdempotencyError,
    InvitationServiceError,
    MembershipServiceError,
    OutboxError,
    RouteBoundaryError,
    RouteConfigurationError,
    ValidationError,
    ValueError,
)

__all__ = ["router"]
