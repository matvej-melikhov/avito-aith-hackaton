"""US1 local entrypoints, registered workers, and SMTP adapter tests."""

from __future__ import annotations

import json
import smtplib
import ssl
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import cast
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select

from review_platform import bootstrap as bootstrap_cli
from review_platform import operator as operator_cli
from review_platform.application.auth_guards.membership import StaleMembershipAuthority
from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.services.bootstrap import BootstrapAlreadyActivated
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    Invitation,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course
from review_platform.infrastructure.db.models.operations import Operation, OutboxMessage
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.providers.email_smtp import (
    SMTPConfig,
    SMTPEmailProvider,
)
from review_platform.infrastructure.providers.mocks import (
    FixtureCourseImportProvider,
    FixtureEmailProvider,
    FrozenFixtureStore,
    JsonSchemaPayloadValidator,
)
from review_platform.infrastructure.tasks.course_import import CourseImportTaskHandler
from review_platform.infrastructure.tasks.email import InvitationEmailTaskHandler
from review_platform.infrastructure.tasks.registry import HANDLER_MODULES, REGISTRY
from review_platform.main import create_app

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000000001")
OPERATION = UUID("00000000-0000-7000-8000-000000000010")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000000011")
IMPORT_MESSAGE = UUID("00000000-0000-7000-8000-000000000601")
METHOD = UUID("00000000-0000-7000-8000-000000000602")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000000603")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class _FakeSMTP:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.message: EmailMessage | None = None
        self.quit_called = False

    def ehlo(self) -> object:
        return (250, b"ok")

    def starttls(self, *, context: ssl.SSLContext) -> object:
        del context
        return (220, b"ready")

    def login(self, user: str, password: str) -> object:
        del user, password
        return (235, b"ok")

    def send_message(self, message: EmailMessage) -> Mapping[str, object]:
        if self.error is not None:
            raise self.error
        self.message = message
        return {}

    def quit(self) -> object:
        self.quit_called = True
        return (221, b"bye")


class _Unsealer:
    def __init__(self, url: str) -> None:
        self._url = url

    def unseal(self, sealed_magic_link: str) -> str:
        assert sealed_magic_link.startswith("sealed:")
        return self._url


class _BlockingCourseProvider:
    contract_version = "1.1.0"
    schema_name = "course-import.schema.json"

    def __init__(self, *, started: anyio.Event, resume: anyio.Event) -> None:
        self._started = started
        self._resume = resume
        self._delegate = FixtureCourseImportProvider()

    async def import_course_page(self, request: ProviderPayload) -> ProviderPayload:
        self._started.set()
        await self._resume.wait()
        return await self._delegate.import_course_page(request)


def _id_factory(start: int = 700) -> Callable[[], UUID]:
    counter = start

    def create() -> UUID:
        nonlocal counter
        counter += 1
        return UUID(f"00000000-0000-7000-8000-{counter:012d}")

    return create


async def _seed_import_message(factory: AsyncSessionFactory) -> None:
    async with session_scope(factory) as session:
        session.add(User(id=METHOD, display_name="Methodologist", status="active"))
        await session.flush()
        session.add(
            OrganizationMembership(
                id=MEMBERSHIP,
                organization_id=ORG,
                user_id=METHOD,
                roles=["methodologist"],
                status="active",
                revision=0,
                auth_epoch=0,
                revoked_at=None,
                revoked_by=None,
            )
        )
        session.add(
            ExternalCredential(
                id=CREDENTIAL,
                organization_id=ORG,
                provider="stepik",
                binding_version=1,
                ciphertext="encrypted-fixture",
                key_id="test-key",
                status="active",
                rotated_at=None,
                revoked_at=None,
            )
        )
        session.add(
            OutboxMessage(
                organization_id=ORG,
                message_id=IMPORT_MESSAGE,
                aggregate_type="operation",
                aggregate_id=OPERATION,
                event_type="CourseImportRequested",
                payload_version="1.1.0",
                payload={
                    "operation_id": str(OPERATION),
                    "credential_binding_id": str(CREDENTIAL),
                    "credential_binding_version": 1,
                    "provider": "stepik",
                    "external_url": "https://stepik.org/course/1",
                    "cursor": None,
                    "page_size": 100,
                    "course_binding_version": 1,
                    "actor": {
                        "type": "user",
                        "user_id": str(METHOD),
                        "roles": ["methodologist"],
                        "membership_revision": 0,
                        "auth_epoch": 0,
                    },
                },
                available_at=NOW,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                enqueue_state="completed",
                completed_at=NOW,
                attempts=1,
                max_attempts=5,
                error_code=None,
                sanitized_error=None,
            )
        )


def test_entrypoint_help_and_operator_commands_are_exact_and_import_safe() -> None:
    bootstrap_help = bootstrap_cli.build_parser().format_help()
    recovery_help = operator_cli.build_parser().format_help()
    assert "--organization-id" in bootstrap_help
    assert "--authority-key" in recovery_help

    bootstrap = bootstrap_cli.build_bootstrap_command(
        organization_id=ORG,
        operator_id="local-operator",
        operator_reason="initial activation",
        provider="stepik",
        issuer="https://stepik.org",
        subject="methodologist-1",
        request_id=UUID("00000000-0000-7000-8000-000000000611"),
        trace_id=UUID("00000000-0000-7000-8000-000000000612"),
    )
    recovery = operator_cli.build_recovery_command(
        organization_id=ORG,
        operator_id="local-operator",
        operator_reason="emergency access recovery",
        provider="stepik",
        issuer="https://stepik.org",
        subject="methodologist-2",
        recovery_reason="restore methodologist access",
        expected_revision=1,
        authority_key="one-time-recovery-authority-0001",
        request_id=UUID("00000000-0000-7000-8000-000000000613"),
        trace_id=UUID("00000000-0000-7000-8000-000000000614"),
    )
    assert (str(bootstrap.command_name), str(bootstrap.transport)) == (
        "activate_bootstrap",
        "operator",
    )
    assert bootstrap.actor.type == "installation_operator"
    assert (str(recovery.command_name), str(recovery.transport)) == (
        "recover_methodologist",
        "operator",
    )
    assert recovery.actor.type == "installation_operator"


async def test_bootstrap_and_recovery_entrypoints_are_one_time_and_replay_safe(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    activate = bootstrap_cli.build_bootstrap_command(
        organization_id=ORG,
        operator_id="local-operator",
        operator_reason="initial activation",
        provider="stepik",
        issuer="https://stepik.org",
        subject="methodologist-1",
        request_id=UUID("00000000-0000-7000-8000-000000000621"),
        trace_id=UUID("00000000-0000-7000-8000-000000000622"),
    )
    activated = await bootstrap_cli.execute_bootstrap_command(
        activate,
        session_factory=foundation_session_factory,
    )
    assert activated.roles == ("methodologist",)
    with pytest.raises(BootstrapAlreadyActivated):
        await bootstrap_cli.execute_bootstrap_command(
            activate,
            session_factory=foundation_session_factory,
        )

    recover = operator_cli.build_recovery_command(
        organization_id=ORG,
        operator_id="local-operator",
        operator_reason="emergency access recovery",
        provider="stepik",
        issuer="https://stepik.org",
        subject="methodologist-2",
        recovery_reason="restore methodologist access",
        expected_revision=1,
        authority_key="one-time-recovery-authority-0002",
        request_id=UUID("00000000-0000-7000-8000-000000000623"),
        trace_id=UUID("00000000-0000-7000-8000-000000000624"),
    )
    recovered = await operator_cli.execute_recovery_command(
        recover,
        session_factory=foundation_session_factory,
    )
    replay = await operator_cli.execute_recovery_command(
        recover,
        session_factory=foundation_session_factory,
    )
    assert recovered.user_id == replay.user_id
    assert recovered.replayed is False
    assert replay.replayed is True


async def test_no_bootstrap_or_recovery_http_endpoint_exists(
    foundation_runtime: FoundationRuntime,
) -> None:
    app = create_app(runtime=foundation_runtime)
    paths = {route.path for route in app.routes}
    assert not any("bootstrap" in path or "recover" in path for path in paths)


async def test_course_import_handler_persists_operation_and_provider_provenance(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_import_message(foundation_session_factory)
    handler = CourseImportTaskHandler(
        session_factory=foundation_session_factory,
        provider=FixtureCourseImportProvider(),
        validator=JsonSchemaPayloadValidator(),
        id_factory=_id_factory(),
        clock=lambda: NOW,
        worker_identity="fixture-course-worker",
    )

    result = await handler(
        organization_id=str(ORG),
        message_id=str(IMPORT_MESSAGE),
    )

    assert result["operation_id"] == str(OPERATION)
    assert result["organization_id"] == str(ORG)
    assert result["kind"] == "course_import"
    assert result["state"] == "succeeded"
    async with foundation_session_factory() as session:
        operation = await session.get(Operation, OPERATION)
        assert operation is not None
        assert operation.input_version.startswith("course-import:1.1.0:sha256:")
        assert await session.scalar(select(func.count()).select_from(Course)) == 1


async def test_claimed_course_import_rolls_back_after_final_auth_epoch_change(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_import_message(foundation_session_factory)
    started = anyio.Event()
    resume = anyio.Event()
    provider = _BlockingCourseProvider(started=started, resume=resume)
    handler = CourseImportTaskHandler(
        session_factory=foundation_session_factory,
        provider=provider,
        validator=JsonSchemaPayloadValidator(),
        id_factory=_id_factory(800),
        clock=lambda: NOW,
    )
    outcomes: list[str] = []

    async def run_handler() -> None:
        try:
            await handler(organization_id=str(ORG), message_id=str(IMPORT_MESSAGE))
        except StaleMembershipAuthority:
            outcomes.append("rejected_stale_auth_epoch")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run_handler)
        await started.wait()
        async with session_scope(foundation_session_factory) as session:
            membership = await session.get(OrganizationMembership, MEMBERSHIP)
            assert membership is not None
            membership.auth_epoch = 1
            membership.revision = 1
        resume.set()

    assert outcomes == ["rejected_stale_auth_epoch"]
    async with foundation_session_factory() as session:
        assert await session.get(Operation, OPERATION) is None
        assert await session.scalar(select(func.count()).select_from(Course)) == 0


async def test_smtp_adapter_validates_frozen_envelope_and_bounds_failure() -> None:
    fixture = FrozenFixtureStore().load("email-v1.1.0.json")
    request = fixture["request"]
    fake = _FakeSMTP()
    provider = SMTPEmailProvider(
        SMTPConfig(
            host="127.0.0.1",
            port=1025,
            sender="noreply@example.test",
            credential_binding_id=UUID(str(request["credential_binding_id"])),
            credential_binding_version=int(str(request["credential_binding_version"])),
        ),
        smtp_factory=lambda _host, _port, _timeout: fake,
    )
    result = await provider.send(request)

    assert result["outcome"] == "accepted"
    assert fake.message is not None
    assert "https://review.example.test/invite/token" in fake.message.get_content()
    assert fake.quit_called

    failed_transport = _FakeSMTP(error=smtplib.SMTPConnectError(421, "x" * 10_000))
    failed_provider = SMTPEmailProvider(
        SMTPConfig(
            host="127.0.0.1",
            port=1025,
            sender="noreply@example.test",
            credential_binding_id=UUID(str(request["credential_binding_id"])),
            credential_binding_version=int(str(request["credential_binding_version"])),
        ),
        smtp_factory=lambda _host, _port, _timeout: failed_transport,
    )
    failure = await failed_provider.send(request)
    assert failure["outcome"] == "retryable_failed"
    assert len(json.dumps(failure["error"]).encode()) <= 2048


async def test_invitation_email_handler_uses_exact_frozen_request_and_credential(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    fixture = FrozenFixtureStore().load("email-v1.1.0.json")
    request = fixture["request"]
    template_data = cast(Mapping[str, object], request["template_data"])
    invitation_id = UUID(str(template_data["invitation_id"]))
    message_id = UUID(str(request["message_id"]))
    credential_id = UUID(str(request["credential_binding_id"]))
    issuer = UUID("00000000-0000-7000-8000-000000000641")
    expires_text = str(template_data["expires_at"])
    expires_at = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
    async with session_scope(foundation_session_factory) as session:
        session.add(User(id=issuer, display_name="Issuer", status="active"))
        await session.flush()
        session.add(
            ExternalCredential(
                id=credential_id,
                organization_id=ORG,
                provider="smtp",
                binding_version=1,
                ciphertext="encrypted-smtp",
                key_id="test-key",
                status="active",
                rotated_at=None,
                revoked_at=None,
            )
        )
        session.add(
            Invitation(
                id=invitation_id,
                organization_id=ORG,
                role="reviewer",
                normalized_email="reviewer@example.com",
                token_digest="sha256:" + "6" * 64,
                expires_at=expires_at,
                issued_by=issuer,
                consumed_by=None,
                consumed_at=None,
                revoked_at=None,
                status="active",
                revision=0,
            )
        )
        session.add(
            OutboxMessage(
                organization_id=ORG,
                message_id=message_id,
                aggregate_type="invitation",
                aggregate_id=invitation_id,
                event_type="InvitationEmailRequested",
                payload_version="1.1.0",
                payload={
                    "invitation_id": str(invitation_id),
                    "recipient": "reviewer@example.com",
                    "sealed_magic_link": "sealed:fixture-link",
                    "expires_at": expires_text,
                },
                available_at=NOW,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                enqueue_state="completed",
                completed_at=NOW,
                attempts=1,
                max_attempts=5,
                error_code=None,
                sanitized_error=None,
            )
        )

    handler = InvitationEmailTaskHandler(
        session_factory=foundation_session_factory,
        provider=FixtureEmailProvider(),
        unsealer=_Unsealer("https://review.example.test/invite/token"),
        credential_binding_id=credential_id,
        credential_binding_version=1,
        clock=lambda: NOW,
    )
    result = await handler(organization_id=str(ORG), message_id=str(message_id))
    assert result == fixture["success_result"]


def test_registry_preserves_both_implemented_us1_handlers() -> None:
    assert {
        "review_platform.infrastructure.tasks.course_import",
        "review_platform.infrastructure.tasks.email",
    }.issubset(HANDLER_MODULES)
    assert {
        "review_platform.course_import",
        "review_platform.invitation_email",
    }.issubset(REGISTRY.names())
    course = REGISTRY.resolve_event("CourseImportRequested")
    email = REGISTRY.resolve_event("InvitationEmailRequested")
    assert (course.kind, course.requires_auth_revalidation) == ("course_import", False)
    assert (email.kind, email.requires_auth_revalidation) == ("email", False)
