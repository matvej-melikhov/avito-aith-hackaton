"""Schema-backed bounded SMTP adapter for Mailpit and configured providers."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from review_platform.application.ports.providers import (
    CONTRACT_VERSION,
    EmailProvider,
    JsonValue,
    ProviderContractError,
    ProviderPayload,
    ProviderPayloadValidator,
)
from review_platform.domain.primitives import sanitize_error
from review_platform.infrastructure.providers.mocks import JsonSchemaPayloadValidator


class SMTPAdapterError(RuntimeError):
    """The SMTP adapter configuration or bounded message is invalid."""


class SMTPConnection(Protocol):
    def ehlo(self) -> object: ...

    def starttls(self, *, context: ssl.SSLContext) -> object: ...

    def login(self, user: str, password: str) -> object: ...

    def send_message(self, message: EmailMessage) -> Mapping[str, object]: ...

    def quit(self) -> object: ...


type SMTPFactory = Callable[[str, int, float], SMTPConnection]


@dataclass(frozen=True, slots=True)
class SMTPConfig:
    host: str
    port: int
    sender: str
    credential_binding_id: UUID
    credential_binding_version: int
    timeout_seconds: float = 15.0
    use_starttls: bool = False
    username: str | None = None
    password: SecretStr | None = None
    max_message_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not self.host or any(character.isspace() for character in self.host):
            raise SMTPAdapterError("SMTP host is required and cannot contain whitespace")
        if not 1 <= self.port <= 65_535:
            raise SMTPAdapterError("SMTP port must be between 1 and 65535")
        if not self.sender or "@" not in self.sender or any(
            character in self.sender for character in "\r\n"
        ):
            raise SMTPAdapterError("SMTP sender must be a safe email address")
        if self.credential_binding_version < 1:
            raise SMTPAdapterError("credential binding version must be positive")
        if not 1 <= self.timeout_seconds <= 60:
            raise SMTPAdapterError("SMTP timeout must be between 1 and 60 seconds")
        if not 1 <= self.max_message_bytes <= 1_048_576:
            raise SMTPAdapterError("SMTP message byte limit is invalid")
        if (self.username is None) != (self.password is None):
            raise SMTPAdapterError("SMTP username and password must be configured together")


class SMTPEmailProvider(EmailProvider):
    """Validate frozen request/result envelopes around bounded SMTP I/O."""

    contract_version = CONTRACT_VERSION
    schema_name = "email.schema.json"

    def __init__(
        self,
        config: SMTPConfig,
        *,
        validator: ProviderPayloadValidator | None = None,
        smtp_factory: SMTPFactory | None = None,
    ) -> None:
        self._config = config
        self._validator = validator or JsonSchemaPayloadValidator()
        self._smtp_factory = smtp_factory or _smtp_factory

    async def send(self, request: ProviderPayload) -> ProviderPayload:
        try:
            self._validator.validate(
                schema_name=self.schema_name,
                definition="send_request",
                payload=request,
            )
        except Exception as error:
            raise ProviderContractError("email request violates frozen schema") from error
        organization_id = _required_string(request, "organization_id")
        message_id = _required_string(request, "message_id")
        if (
            request.get("credential_binding_id") != str(self._config.credential_binding_id)
            or request.get("credential_binding_version")
            != self._config.credential_binding_version
        ):
            return self._validated_result(
                organization_id=organization_id,
                message_id=message_id,
                outcome="action_required",
                provider_message_id=None,
                error={
                    "code": "credential_binding_mismatch",
                    "message": "Exact SMTP credential binding is unavailable",
                    "retryable": False,
                    "action": "configure_email_credential",
                },
            )

        try:
            message = self._message(request)
            encoded = message.as_bytes()
            if len(encoded) > self._config.max_message_bytes:
                raise SMTPAdapterError("rendered SMTP message exceeds configured byte limit")
            refused = await asyncio.wait_for(
                asyncio.to_thread(self._send_sync, message),
                timeout=self._config.timeout_seconds + 1,
            )
            if refused:
                raise SMTPAdapterError("SMTP refused one or more recipients")
        except Exception as error:
            retryable = isinstance(
                error,
                TimeoutError
                | OSError
                | smtplib.SMTPConnectError
                | smtplib.SMTPServerDisconnected,
            )
            bounded_message = str(error).encode("utf-8")[:1024].decode(
                "utf-8", errors="ignore"
            )
            sanitized = sanitize_error(
                {
                    "code": "smtp_unavailable" if retryable else "smtp_action_required",
                    "message": bounded_message,
                    "retryable": retryable,
                    "action": None if retryable else "inspect_email_configuration",
                },
                max_bytes=2048,
            )
            sanitized["retryable"] = retryable
            return self._validated_result(
                organization_id=organization_id,
                message_id=message_id,
                outcome="retryable_failed" if retryable else "action_required",
                provider_message_id=None,
                error=sanitized,
            )

        return self._validated_result(
            organization_id=organization_id,
            message_id=message_id,
            outcome="accepted",
            provider_message_id=f"smtp:{message_id}",
            error=None,
        )

    def _message(self, request: ProviderPayload) -> EmailMessage:
        recipient = _required_string(request, "recipient")
        data = request.get("template_data")
        if not isinstance(data, Mapping):
            raise ProviderContractError("email template_data must be an object")
        magic_link = _required_string(data, "magic_link_url")
        expires_at = _required_string(data, "expires_at")
        invitation_id = _required_string(data, "invitation_id")
        message_id = _required_string(request, "message_id")
        message = EmailMessage()
        message["From"] = self._config.sender
        message["To"] = recipient
        message["Subject"] = "Review platform invitation"
        message["Message-ID"] = f"<{message_id}@review-platform.local>"
        message.set_content(
            "You were invited to the review platform.\n\n"
            f"Open the one-time link: {magic_link}\n"
            f"Invitation: {invitation_id}\n"
            f"Expires at: {expires_at}\n"
        )
        return message

    def _send_sync(self, message: EmailMessage) -> Mapping[str, object]:
        client = self._smtp_factory(
            self._config.host,
            self._config.port,
            self._config.timeout_seconds,
        )
        try:
            client.ehlo()
            if self._config.use_starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self._config.username is not None and self._config.password is not None:
                client.login(
                    self._config.username,
                    self._config.password.get_secret_value(),
                )
            return client.send_message(message)
        finally:
            with suppress(OSError, smtplib.SMTPException):
                client.quit()

    def _validated_result(
        self,
        *,
        organization_id: str,
        message_id: str,
        outcome: str,
        provider_message_id: str | None,
        error: Mapping[str, JsonValue] | None,
    ) -> ProviderPayload:
        result: dict[str, JsonValue] = {
            "contract_version": CONTRACT_VERSION,
            "organization_id": organization_id,
            "message_id": message_id,
            "outcome": outcome,
            "provider_message_id": provider_message_id,
            "error": dict(error) if error is not None else None,
        }
        self._validator.validate(
            schema_name=self.schema_name,
            definition="send_result",
            payload=result,
        )
        return result


def _smtp_factory(host: str, port: int, timeout: float) -> SMTPConnection:
    return smtplib.SMTP(host=host, port=port, timeout=timeout)


def _required_string(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ProviderContractError(f"email {field} must be a non-empty string")
    return value


__all__ = [
    "SMTPAdapterError",
    "SMTPConfig",
    "SMTPConnection",
    "SMTPEmailProvider",
    "SMTPFactory",
]
