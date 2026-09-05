"""Append-only audit boundary and shared-sink sanitization."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, overload
from uuid import UUID

from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import sanitize_error

MAX_SANITIZED_TEXT_BYTES = 2048

type SanitizedValue = (
    None | bool | int | float | str | list["SanitizedValue"] | dict[str, "SanitizedValue"]
)

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "artifact_content",
        "cookie",
        "set_cookie",
        "password",
        "secret",
        "client_secret",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer_token",
        "token_digest",
        "invitation_token",
        "magic_link",
        "signed_url",
        "raw_body",
        "request_body",
        "response_body",
        "provider_body",
    }
)
_PROVIDER_CONTAINERS = frozenset(
    {
        "provider_error",
        "provider_request",
        "provider_response",
        "provider_payload",
        "provider_raw",
        "provider_result",
    }
)
_PROVIDER_BODY_KEYS = frozenset({"body", "content", "raw", "request", "response"})
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"(?![A-Za-z0-9_-])"
)
_MAGIC_URL_PATTERN = re.compile(r"https?://\S*(?:magic|invite|invitation|token)\S*", re.IGNORECASE)
_EMAIL_PII_PATTERN = re.compile(
    r"(?<![A-Z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}"
    r"(?![A-Z0-9-])",
    re.IGNORECASE,
)


class AuditError(RuntimeError):
    """Base class for typed audit failures."""


class InvalidAuditEvent(AuditError):
    """An audit record would be incomplete or cross-tenant."""


@dataclass(frozen=True, slots=True)
class AuditEventDraft:
    organization_id: UUID
    actor: RequestActor
    action: str
    entity_type: str
    entity_id: UUID
    before_revision: int | None
    after_revision: int | None
    request_id: UUID
    trace_id: UUID
    outcome: str
    details: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: UUID
    organization_id: UUID
    actor_type: str
    actor_user_id: UUID | None
    installation_operator_id: str | None
    agent_id: UUID | None
    agent_authorization_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID
    before_revision: int | None
    after_revision: int | None
    request_id: UUID
    trace_id: UUID
    outcome: str
    sanitized_details: dict[str, SanitizedValue]
    occurred_at: datetime


class AppendOnlyAuditRepository(Protocol):
    """Repository port intentionally exposes append and no mutation methods."""

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        ...


class AuditRecorder:
    """Create and append an audit event inside the caller's transaction.

    Repository failures deliberately propagate.  The command transaction then
    rolls back the domain mutation and audit append together; there is no
    best-effort audit path and no second transaction.
    """

    def __init__(
        self,
        repository: AppendOnlyAuditRepository,
        *,
        event_id_factory: Callable[[], UUID],
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._event_id_factory = event_id_factory
        self._clock = clock

    async def record(self, draft: AuditEventDraft, *, transaction: object) -> AuditEvent:
        self._validate(draft)
        occurred_at = self._clock()
        if occurred_at.tzinfo is None:
            raise InvalidAuditEvent("audit clock must return a timezone-aware timestamp")
        sanitized = sanitize_shared_value(draft.details)
        event = AuditEvent(
            event_id=self._event_id_factory(),
            organization_id=draft.organization_id,
            actor_type=draft.actor.actor_type,
            actor_user_id=draft.actor.user_id,
            installation_operator_id=draft.actor.installation_operator_id,
            agent_id=draft.actor.agent_id,
            agent_authorization_id=draft.actor.agent_authorization_id,
            action=draft.action,
            entity_type=draft.entity_type,
            entity_id=draft.entity_id,
            before_revision=draft.before_revision,
            after_revision=draft.after_revision,
            request_id=draft.request_id,
            trace_id=draft.trace_id,
            outcome=draft.outcome,
            sanitized_details=sanitized,
            occurred_at=occurred_at,
        )
        await self._repository.append(event, transaction=transaction)
        return event

    @staticmethod
    def _validate(draft: AuditEventDraft) -> None:
        if draft.actor.organization_id != draft.organization_id:
            raise InvalidAuditEvent("audit actor tenant does not match event tenant")
        if not draft.action or not draft.entity_type or not draft.outcome:
            raise InvalidAuditEvent("audit action, entity type, and outcome are required")
        for name, revision in (
            ("before", draft.before_revision),
            ("after", draft.after_revision),
        ):
            if revision is not None and revision < 0:
                raise InvalidAuditEvent(f"audit {name} revision must be non-negative")


@overload
def sanitize_shared_value(value: Mapping[str, object]) -> dict[str, SanitizedValue]: ...


@overload
def sanitize_shared_value(value: object) -> SanitizedValue: ...


def sanitize_shared_value(value: object) -> SanitizedValue:
    """Recursively remove credentials/raw provider bodies and bound all text."""

    sanitized = _sanitize(value, provider_container=False)
    if isinstance(sanitized, dict):
        encoded = json.dumps(sanitized, ensure_ascii=False, sort_keys=True).encode("utf-8")
        candidate = sanitized
        if len(encoded) > MAX_SANITIZED_TEXT_BYTES:
            candidate = _actionable_summary(sanitized)
        # The domain primitive applies the total serialized-size bound while
        # retaining code/action fields needed for recovery.
        return sanitize_error(candidate, max_bytes=MAX_SANITIZED_TEXT_BYTES)
    return sanitized


def _sanitize(value: object, *, provider_container: bool) -> SanitizedValue:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        redacted = _BEARER_PATTERN.sub("[REDACTED]", value)
        redacted = _JWT_PATTERN.sub("[REDACTED]", redacted)
        redacted = _MAGIC_URL_PATTERN.sub("[REDACTED]", redacted)
        redacted = _EMAIL_PII_PATTERN.sub("[REDACTED_EMAIL]", redacted)
        return _truncate_utf8(redacted)
    if isinstance(value, Mapping):
        sanitized: dict[str, SanitizedValue] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.casefold().replace("-", "_")
            if _is_sensitive_key(normalized_key):
                continue
            if provider_container and normalized_key in _PROVIDER_BODY_KEYS:
                continue
            nested_provider = provider_container or normalized_key in _PROVIDER_CONTAINERS
            safe_key = _EMAIL_PII_PATTERN.sub("[REDACTED_EMAIL]", key)
            sanitized[_truncate_utf8(safe_key)] = _sanitize(
                item,
                provider_container=nested_provider,
            )
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_sanitize(item, provider_container=provider_container) for item in value]
    return "[unsupported]"


def _is_sensitive_key(key: str) -> bool:
    if key in _SENSITIVE_KEYS:
        return True
    return key == "token" or key.endswith("_token") or key.endswith("_secret")


def _truncate_utf8(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_SANITIZED_TEXT_BYTES:
        return value
    return encoded[:MAX_SANITIZED_TEXT_BYTES].decode("utf-8", errors="ignore")


def _actionable_summary(value: SanitizedValue) -> dict[str, SanitizedValue]:
    summary: dict[str, SanitizedValue] = {}
    wanted = {"code", "action", "safe_code", "safe_action"}

    def collect(current: SanitizedValue) -> None:
        if isinstance(current, dict):
            for key, item in current.items():
                if (
                    key in wanted
                    and key not in summary
                    and isinstance(item, str | int | float | bool)
                ):
                    summary[key] = item
                collect(item)
        elif isinstance(current, list):
            for item in current:
                collect(item)

    collect(value)
    if isinstance(value, dict) and isinstance(value.get("message"), str):
        summary["message"] = value["message"]
    else:
        summary["message"] = "Additional details omitted after sanitization"
    return summary
