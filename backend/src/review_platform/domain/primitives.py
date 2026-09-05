"""Small domain primitives with fail-closed validation."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID

import rfc8785

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_BEARER = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"eyJ[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"(?![A-Za-z0-9_-])"
)
_EMAIL_PII = re.compile(
    r"(?<![A-Z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}"
    r"(?![A-Z0-9-])",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(access[_-]?token|refresh[_-]?token|password|client[_-]?secret|api[_-]?key)"
    r"\s*[:=]\s*[^\s,;]+"
)
_MAGIC_LINK = re.compile(
    r"https?://\S*(?:magic|invite|invitation|callback|token)\S*",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set_cookie",
    "password",
    "access_token",
    "refresh_token",
    "client_secret",
    "secret",
    "token",
    "private_key",
    "api_key",
    "magic_link",
    "invitation_token",
    "bearer_token",
    "raw_body",
    "response_body",
    "request_body",
    "body",
    "artifact_content",
    "provider_body",
}
_PROVIDER_CONTAINERS = {
    "provider_error",
    "provider_payload",
    "provider_raw",
    "provider_request",
    "provider_response",
    "provider_result",
}
_PROVIDER_BODY_KEYS = {"body", "content", "raw", "request", "response"}

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type RandomBytes = Callable[[int], bytes]
type UtcClock = Callable[[], datetime]


class RevisionConflict(ValueError):
    """The caller's expected revision differs from current persistent state."""

    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"revision conflict: expected {expected}, actual {actual}")


def require_utc(value: datetime) -> datetime:
    """Accept an aware UTC value and canonicalize its tzinfo to datetime.UTC."""

    if (
        value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC)


def utc_now() -> datetime:
    """Return an aware UTC wall-clock value."""

    return datetime.now(UTC)


class UUID7Generator:
    """Thread-safe UUIDv7 generator with injectable UTC clock and entropy.

    Within one generator, equal or regressing millisecond clocks still produce
    monotonically increasing UUID integers by incrementing the 74 random bits.
    """

    def __init__(
        self,
        *,
        clock: UtcClock = utc_now,
        random_bytes: RandomBytes = secrets.token_bytes,
    ) -> None:
        self._clock = clock
        self._random_bytes = random_bytes
        self._last_milliseconds = -1
        self._last_random = -1
        self._lock = Lock()

    def __call__(self) -> UUID:
        now = require_utc(self._clock())
        milliseconds = int(now.timestamp() * 1000)
        if milliseconds < 0 or milliseconds >= 1 << 48:
            raise ValueError("UUIDv7 timestamp is outside the 48-bit range")

        with self._lock:
            if milliseconds > self._last_milliseconds:
                random_value = int.from_bytes(self._entropy(10), "big") & ((1 << 74) - 1)
            else:
                milliseconds = self._last_milliseconds
                random_value = self._last_random + 1
                if random_value >= 1 << 74:
                    milliseconds += 1
                    if milliseconds >= 1 << 48:
                        raise OverflowError("UUIDv7 timestamp space exhausted")
                    random_value = 0

            self._last_milliseconds = milliseconds
            self._last_random = random_value

        rand_a = random_value >> 62
        rand_b = random_value & ((1 << 62) - 1)
        value = (
            (milliseconds << 80)
            | (7 << 76)
            | (rand_a << 64)
            | (0b10 << 62)
            | rand_b
        )
        return UUID(int=value)

    def _entropy(self, size: int) -> bytes:
        value = self._random_bytes(size)
        if not isinstance(value, bytes) or len(value) != size:
            raise ValueError(f"random_bytes must return exactly {size} bytes")
        return value


_DEFAULT_UUID7_GENERATOR = UUID7Generator()


def uuid7() -> UUID:
    """Generate a process-local monotonic UUIDv7."""

    return _DEFAULT_UUID7_GENERATOR()


generate_uuid7 = uuid7


def sha256_digest(value: bytes | str) -> str:
    """Return the canonical lower-case prefixed SHA-256 representation."""

    data = value.encode("utf-8") if isinstance(value, str) else value
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def validate_digest(value: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("digest must use canonical sha256:<64 lowercase hex> form")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON-compatible input according to RFC 8785 (JCS)."""

    return rfc8785.dumps(value)


def canonical_json_sha256(value: Any) -> str:
    return sha256_digest(canonical_json_bytes(value))


def verify_and_increment_revision(*, current: int, expected: int) -> int:
    """Compare-and-swap revision helper returning the next revision."""

    if (
        not isinstance(current, int)
        or isinstance(current, bool)
        or not isinstance(expected, int)
        or isinstance(expected, bool)
        or current < 0
        or expected < 0
    ):
        raise ValueError("revisions must be nonnegative integers")
    if current != expected:
        raise RevisionConflict(expected=expected, actual=current)
    return current + 1


compare_and_swap_revision = verify_and_increment_revision


def sanitize_error(
    error: Mapping[str, Any] | BaseException | str,
    *,
    max_bytes: int = 2048,
) -> dict[str, JsonValue]:
    """Redact secret-bearing fields and bound the full UTF-8 JSON representation."""

    if max_bytes < 256:
        raise ValueError("max_bytes must leave room for actionable error fields")
    if isinstance(error, BaseException):
        source: Mapping[str, Any] = {
            "code": type(error).__name__,
            "message": str(error),
        }
    elif isinstance(error, str):
        source = {"code": "error", "message": error}
    else:
        source = error

    sanitized = _sanitize_mapping(source, provider_container=False)
    for key in ("code", "action"):
        value = sanitized.get(key)
        if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) is None:
            sanitized[key] = "unknown_error" if key == "code" else "contact_operator"

    if _encoded_size(sanitized) <= max_bytes:
        return sanitized

    priority_keys = ("code", "action", "safe_code", "safe_action", "message")
    bounded = {key: sanitized[key] for key in priority_keys if key in sanitized}
    message = bounded.get("message")
    if not isinstance(message, str):
        bounded["message"] = "Additional error details omitted"
    bounded["message"] = _fit_message(bounded, str(bounded["message"]), max_bytes)
    if _encoded_size(bounded) > max_bytes:
        bounded["message"] = "Error details omitted"
    return bounded


sanitize_details = sanitize_error


def _sanitize_mapping(
    source: Mapping[str, Any],
    *,
    provider_container: bool,
) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for raw_key, value in source.items():
        key = str(raw_key)
        normalized = key.lower().replace("-", "_")
        if normalized in _SENSITIVE_KEYS or normalized.endswith("_secret"):
            continue
        if provider_container and normalized in _PROVIDER_BODY_KEYS:
            continue
        nested_provider = provider_container or normalized in _PROVIDER_CONTAINERS
        sanitized = _sanitize_value(value, provider_container=nested_provider)
        if sanitized is not None or value is None:
            result[_redact_string(key)] = sanitized
    return result


def _sanitize_value(value: Any, *, provider_container: bool) -> JsonValue:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, provider_container=provider_container)
    if isinstance(value, list | tuple):
        return [
            _sanitize_value(item, provider_container=provider_container)
            for item in value
        ]
    return _redact_string(str(value))


def _redact_string(value: str) -> str:
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    redacted = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    redacted = _JWT.sub("[REDACTED]", redacted)
    redacted = _MAGIC_LINK.sub("[REDACTED_LINK]", redacted)
    return _EMAIL_PII.sub("[REDACTED_EMAIL]", redacted)


def _encoded_size(value: Mapping[str, JsonValue]) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def _fit_message(base: dict[str, JsonValue], message: str, max_bytes: int) -> str:
    low = 0
    high = len(message.encode("utf-8"))
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate = _truncate_utf8(message, midpoint)
        base["message"] = candidate
        if _encoded_size(base) <= max_bytes:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    suffix = "…"
    budget = max(0, max_bytes - len(suffix.encode("utf-8")))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix
