"""Opaque, versioned AgentAuthorization bearer-token handling."""

from __future__ import annotations

import base64
import binascii
import hmac
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass

from review_platform.domain.primitives import sha256_digest, validate_digest

TOKEN_VERSION = "v1"
TOKEN_PREFIX = f"rpat_{TOKEN_VERSION}."
TOKEN_ENTROPY_BYTES = 32
TOKEN_ENTROPY_CHARS = 43
TOKEN_LENGTH = len(TOKEN_PREFIX) + TOKEN_ENTROPY_CHARS

_TOKEN_PATTERN = re.compile(
    rf"^{re.escape(TOKEN_PREFIX)}[A-Za-z0-9_-]{{{TOKEN_ENTROPY_CHARS}}}$"
)
_DUMMY_TOKEN = TOKEN_PREFIX + "A" * TOKEN_ENTROPY_CHARS
_DUMMY_DIGEST = sha256_digest(_DUMMY_TOKEN)

type RandomBytes = Callable[[int], bytes]


class AgentTokenError(ValueError):
    """Base error for invalid or unusable agent token material."""


class MalformedAgentToken(AgentTokenError):
    """A bearer token is not the exact supported opaque format."""


class TokenEntropyError(AgentTokenError):
    """The injected entropy source did not return the requested bytes."""


class TokenRotationError(AgentTokenError):
    """Token rotation failed to produce distinct credential material."""


class AgentTokenSecret:
    """Secret-bearing value with deliberately redacted string representations."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        parse_agent_token(value)
        self.__value = value

    def reveal(self) -> str:
        """Reveal only at the one-time response boundary."""

        return self.__value

    def __repr__(self) -> str:
        return "AgentTokenSecret(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class AgentTokenParts:
    """Non-secret parse result; raw entropy is intentionally not exposed."""

    version: str
    entropy_bytes: int


@dataclass(frozen=True, slots=True)
class IssuedAgentToken:
    """One-time secret plus its persistence-safe digest."""

    access_token: AgentTokenSecret
    token_digest: str
    version: str = TOKEN_VERSION
    digest_algorithm: str = "sha256"


def issue_agent_token(
    *,
    random_bytes: RandomBytes = secrets.token_bytes,
) -> IssuedAgentToken:
    """Create a 256-bit opaque token and the only value safe to persist."""

    entropy = random_bytes(TOKEN_ENTROPY_BYTES)
    if not isinstance(entropy, bytes) or len(entropy) != TOKEN_ENTROPY_BYTES:
        raise TokenEntropyError(
            f"random_bytes must return exactly {TOKEN_ENTROPY_BYTES} bytes"
        )
    encoded = base64.urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")
    token = TOKEN_PREFIX + encoded
    secret = AgentTokenSecret(token)
    return IssuedAgentToken(
        access_token=secret,
        token_digest=sha256_digest(token),
    )


def rotate_agent_token(
    previous_digest: str,
    *,
    random_bytes: RandomBytes = secrets.token_bytes,
) -> IssuedAgentToken:
    """Issue distinct secret material without requiring the previous raw token."""

    try:
        canonical_previous = validate_digest(previous_digest)
    except (TypeError, ValueError) as error:
        raise TokenRotationError("previous token digest is not canonical sha256") from error
    rotated = issue_agent_token(random_bytes=random_bytes)
    if hmac.compare_digest(
        rotated.token_digest.encode("ascii"),
        canonical_previous.encode("ascii"),
    ):
        raise TokenRotationError("token rotation did not produce a new secret")
    return rotated


def parse_agent_token(token: str) -> AgentTokenParts:
    """Reject unsupported versions, noncanonical encoding, and wrong entropy length."""

    if not isinstance(token, str) or len(token) != TOKEN_LENGTH:
        raise MalformedAgentToken("agent token has an invalid length")
    if _TOKEN_PATTERN.fullmatch(token) is None:
        raise MalformedAgentToken("agent token has an invalid version or encoding")
    encoded = token.removeprefix(TOKEN_PREFIX).encode("ascii")
    try:
        entropy = base64.b64decode(encoded + b"=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise MalformedAgentToken("agent token encoding is invalid") from error
    if len(entropy) != TOKEN_ENTROPY_BYTES:
        raise MalformedAgentToken("agent token entropy length is invalid")
    canonical = base64.urlsafe_b64encode(entropy).rstrip(b"=")
    if canonical != encoded:
        raise MalformedAgentToken("agent token encoding is not canonical")
    return AgentTokenParts(version=TOKEN_VERSION, entropy_bytes=len(entropy))


def digest_agent_token(token: str | AgentTokenSecret) -> str:
    """Return the canonical digest for an already strict-parsed token."""

    raw = token.reveal() if isinstance(token, AgentTokenSecret) else token
    parse_agent_token(raw)
    return sha256_digest(raw)


def verify_agent_token(
    token: str | AgentTokenSecret,
    expected_digest: str,
) -> bool:
    """Constant-time compare a presented token with a persistence-safe digest."""

    raw = token.reveal() if isinstance(token, AgentTokenSecret) else token
    valid_token = True
    try:
        candidate_digest = digest_agent_token(raw)
    except (TypeError, MalformedAgentToken):
        valid_token = False
        candidate_digest = _DUMMY_DIGEST

    valid_digest = True
    try:
        canonical_expected = validate_digest(expected_digest)
    except (TypeError, ValueError):
        valid_digest = False
        canonical_expected = _DUMMY_DIGEST

    matched = hmac.compare_digest(
        candidate_digest.encode("ascii"),
        canonical_expected.encode("ascii"),
    )
    return valid_token and valid_digest and matched


__all__ = [
    "TOKEN_ENTROPY_BYTES",
    "TOKEN_LENGTH",
    "TOKEN_PREFIX",
    "TOKEN_VERSION",
    "AgentTokenError",
    "AgentTokenParts",
    "AgentTokenSecret",
    "IssuedAgentToken",
    "MalformedAgentToken",
    "TokenEntropyError",
    "TokenRotationError",
    "digest_agent_token",
    "issue_agent_token",
    "parse_agent_token",
    "rotate_agent_token",
    "verify_agent_token",
]
