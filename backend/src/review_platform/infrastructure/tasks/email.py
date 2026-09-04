"""Registered invitation-email handler over frozen email provider contracts."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime
from importlib import import_module
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr

from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    EmailProvider,
    JsonValue,
    ProviderPayloadValidator,
)
from review_platform.domain.primitives import require_utc, utc_now
from review_platform.infrastructure.db.repositories.identity import (
    ExternalCredentialRepository,
    InvitationRepository,
)
from review_platform.infrastructure.db.repositories.operations import (
    OutboxMessageRepository,
)
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from review_platform.infrastructure.providers.email_smtp import SMTPConfig, SMTPEmailProvider
from review_platform.infrastructure.providers.mocks import JsonSchemaPayloadValidator
from review_platform.settings import get_settings

from .broker import RetryableTaskError
from .registry import task_handler

EMAIL_UNSEALER_FACTORY_ENV = "REVIEW_PLATFORM_INVITATION_UNSEALER_FACTORY"
SMTP_HOST_ENV = "REVIEW_PLATFORM_SMTP_HOST"
SMTP_PORT_ENV = "REVIEW_PLATFORM_SMTP_PORT"
SMTP_SENDER_ENV = "REVIEW_PLATFORM_SMTP_SENDER"
SMTP_USERNAME_ENV = "REVIEW_PLATFORM_SMTP_USERNAME"
SMTP_PASSWORD_ENV = "REVIEW_PLATFORM_SMTP_PASSWORD"
SMTP_STARTTLS_ENV = "REVIEW_PLATFORM_SMTP_STARTTLS"
SMTP_CREDENTIAL_ID_ENV = "REVIEW_PLATFORM_SMTP_CREDENTIAL_BINDING_ID"
SMTP_CREDENTIAL_VERSION_ENV = "REVIEW_PLATFORM_SMTP_CREDENTIAL_BINDING_VERSION"


class InvitationEmailTaskError(RuntimeError):
    """The durable invitation email cannot be safely delivered."""


@runtime_checkable
class InvitationSecretUnsealer(Protocol):
    def unseal(self, sealed_magic_link: str) -> str: ...


class InvitationEmailTaskHandler:
    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        provider: EmailProvider,
        unsealer: InvitationSecretUnsealer,
        credential_binding_id: UUID,
        credential_binding_version: int,
        credential_provider: str = "smtp",
        validator: ProviderPayloadValidator | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if credential_binding_version < 1:
            raise ValueError("email credential binding version must be positive")
        if (
            provider.contract_version != CONTRACT_VERSION
            or provider.schema_name != "email.schema.json"
        ):
            raise InvitationEmailTaskError("email provider is not bound to frozen 1.1.0 schema")
        self._session_factory = session_factory
        self._provider = provider
        self._unsealer = unsealer
        self._credential_binding_id = credential_binding_id
        self._credential_binding_version = credential_binding_version
        self._credential_provider = credential_provider
        self._validator = validator or JsonSchemaPayloadValidator()
        self._clock = clock

    async def __call__(self, *, organization_id: str, message_id: str) -> Mapping[str, Any]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        request: dict[str, JsonValue]
        async with session_scope(self._session_factory) as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
            )
            if message is None:
                raise InvitationEmailTaskError("tenant-scoped email outbox message was not found")
            if (
                message.event_type != "InvitationEmailRequested"
                or message.payload_version != CONTRACT_VERSION
                or message.aggregate_type != "invitation"
            ):
                raise InvitationEmailTaskError("outbox row is not a supported invitation email")
            payload = message.payload
            invitation_id = _required_uuid(payload, "invitation_id")
            if invitation_id != message.aggregate_id:
                raise InvitationEmailTaskError("email outbox aggregate does not match invitation")
            invitation = await InvitationRepository(session).get(
                organization_uuid,
                invitation_id,
            )
            now = require_utc(self._clock())
            if invitation is None or invitation.status != "active" or invitation.expires_at <= now:
                raise InvitationEmailTaskError("invitation is no longer active for email delivery")
            recipient = _required_string(payload, "recipient")
            if recipient != invitation.normalized_email:
                raise InvitationEmailTaskError("email recipient does not match invitation target")
            credential = await ExternalCredentialRepository(session).get_exact(
                organization_uuid,
                self._credential_binding_id,
                self._credential_binding_version,
                status="active",
            )
            if credential is None or credential.provider != self._credential_provider:
                raise InvitationEmailTaskError(
                    "exact active SMTP credential binding is unavailable for this tenant"
                )
            sealed = _required_string(payload, "sealed_magic_link")
            magic_link = self._unsealer.unseal(sealed)
            parsed = urlsplit(magic_link)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise InvitationEmailTaskError("unsealed invitation link is not an absolute URL")
            if magic_link == sealed:
                raise InvitationEmailTaskError("invitation link was not actually unsealed")
            expires_at = _required_string(payload, "expires_at")
            if _date_time(expires_at, field="expires_at") != invitation.expires_at:
                raise InvitationEmailTaskError(
                    "email expiry does not match invitation provenance"
                )
            request = {
                "contract_version": CONTRACT_VERSION,
                "organization_id": organization_id,
                "message_id": message_id,
                "credential_binding_id": str(self._credential_binding_id),
                "credential_binding_version": self._credential_binding_version,
                "template": "reviewer_magic_link",
                "recipient": recipient,
                "template_data": {
                    "invitation_id": str(invitation_id),
                    "magic_link_url": magic_link,
                    "expires_at": expires_at,
                },
            }
            self._validator.validate(
                schema_name="email.schema.json",
                definition="send_request",
                payload=request,
            )
        result = await self._provider.send(request)
        self._validator.validate(
            schema_name="email.schema.json",
            definition="send_result",
            payload=result,
        )
        if (
            result.get("contract_version") != CONTRACT_VERSION
            or result.get("organization_id") != organization_id
            or result.get("message_id") != message_id
        ):
            raise InvitationEmailTaskError("email provider result provenance mismatched")

        outcome = result.get("outcome")
        if outcome == "retryable_failed":
            raise RetryableTaskError(str(result.get("error") or "email delivery retryable failure"))
        if outcome not in {"accepted", "action_required"}:
            raise InvitationEmailTaskError("email provider returned an unknown outcome")
        return result


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise InvitationEmailTaskError(f"email {field} must be a non-empty string")
    return value


def _required_uuid(payload: Mapping[str, object], field: str) -> UUID:
    return _uuid(_required_string(payload, field), field=field)


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise InvitationEmailTaskError(f"{field} must be a UUID") from error


def _date_time(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvitationEmailTaskError(f"{field} must be a date-time") from error
    return require_utc(parsed)


def _load_unsealer(specification: str) -> InvitationSecretUnsealer:
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise InvitationEmailTaskError(
            f"{EMAIL_UNSEALER_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise InvitationEmailTaskError("invitation unsealer factory is not callable")
    unsealer = factory()
    if not isinstance(unsealer, InvitationSecretUnsealer):
        raise InvitationEmailTaskError("factory result does not satisfy InvitationSecretUnsealer")
    return unsealer


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise InvitationEmailTaskError(f"{name} is required")
    return value


def _env_int(name: str, *, default: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None and default is not None:
        return default
    try:
        return int(raw or "")
    except ValueError as error:
        raise InvitationEmailTaskError(f"{name} must be an integer") from error


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.casefold()
    if normalized not in {"true", "false", "1", "0"}:
        raise InvitationEmailTaskError(f"{name} must be true/false or 1/0")
    return normalized in {"true", "1"}


def _configured_provider() -> tuple[SMTPEmailProvider, UUID, int]:
    credential_id = _uuid(_required_env(SMTP_CREDENTIAL_ID_ENV), field=SMTP_CREDENTIAL_ID_ENV)
    credential_version = _env_int(SMTP_CREDENTIAL_VERSION_ENV)
    username = os.environ.get(SMTP_USERNAME_ENV)
    password_value = os.environ.get(SMTP_PASSWORD_ENV)
    password = SecretStr(password_value) if password_value is not None else None
    config = SMTPConfig(
        host=_required_env(SMTP_HOST_ENV),
        port=_env_int(SMTP_PORT_ENV, default=25),
        sender=_required_env(SMTP_SENDER_ENV),
        credential_binding_id=credential_id,
        credential_binding_version=credential_version,
        use_starttls=_env_bool(SMTP_STARTTLS_ENV),
        username=username,
        password=password,
    )
    return SMTPEmailProvider(config), credential_id, credential_version


async def _run_configured(*, organization_id: str, message_id: str) -> Mapping[str, Any]:
    settings = get_settings()
    if settings.database_url is None:
        raise InvitationEmailTaskError("REVIEW_PLATFORM_DATABASE_URL is required")
    provider, credential_id, credential_version = _configured_provider()
    unsealer = _load_unsealer(_required_env(EMAIL_UNSEALER_FACTORY_ENV))
    engine = create_database_engine(settings.database_url)
    try:
        handler = InvitationEmailTaskHandler(
            session_factory=create_session_factory(engine),
            provider=provider,
            unsealer=unsealer,
            credential_binding_id=credential_id,
            credential_binding_version=credential_version,
        )
        return await handler(organization_id=organization_id, message_id=message_id)
    finally:
        await engine.dispose()


@task_handler(
    name="review_platform.invitation_email",
    kind="email",
    event_type="InvitationEmailRequested",
    # A previously committed invitation intent is system recovery work.  The
    # handler re-checks current invitation state and exact credential binding;
    # revoking the issuing user must not silently delete the durable intent.
    requires_auth_revalidation=False,
)
async def handle_invitation_email(*, organization_id: str, message_id: str) -> Mapping[str, Any]:
    return await _run_configured(organization_id=organization_id, message_id=message_id)


__all__ = [
    "EMAIL_UNSEALER_FACTORY_ENV",
    "InvitationEmailTaskError",
    "InvitationEmailTaskHandler",
    "InvitationSecretUnsealer",
    "handle_invitation_email",
]
