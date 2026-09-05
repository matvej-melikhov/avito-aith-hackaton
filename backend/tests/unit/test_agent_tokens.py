from __future__ import annotations

import hmac

import pytest

from review_platform.domain.primitives import sha256_digest
from review_platform.infrastructure.auth.agent_tokens import (
    TOKEN_ENTROPY_BYTES,
    TOKEN_LENGTH,
    AgentTokenSecret,
    MalformedAgentToken,
    TokenEntropyError,
    TokenRotationError,
    digest_agent_token,
    issue_agent_token,
    parse_agent_token,
    rotate_agent_token,
    verify_agent_token,
)


def test_issue_uses_256_bits_of_injected_entropy_and_canonical_digest() -> None:
    requested: list[int] = []

    def entropy(size: int) -> bytes:
        requested.append(size)
        return bytes(range(size))

    issued = issue_agent_token(random_bytes=entropy)
    raw = issued.access_token.reveal()

    assert requested == [TOKEN_ENTROPY_BYTES]
    assert raw == "rpat_v1.AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    assert len(raw) == TOKEN_LENGTH
    assert issued.version == "v1"
    assert issued.digest_algorithm == "sha256"
    assert issued.token_digest == sha256_digest(raw)
    assert digest_agent_token(issued.access_token) == issued.token_digest
    assert parse_agent_token(raw).entropy_bytes == TOKEN_ENTROPY_BYTES


def test_default_issuance_produces_distinct_high_entropy_secrets() -> None:
    issued = [issue_agent_token() for _ in range(16)]

    assert len({item.access_token.reveal() for item in issued}) == len(issued)
    assert len({item.token_digest for item in issued}) == len(issued)


def test_secret_and_issued_value_never_render_raw_token() -> None:
    issued = issue_agent_token(random_bytes=lambda size: b"a" * size)
    raw = issued.access_token.reveal()

    assert raw not in str(issued.access_token)
    assert raw not in repr(issued.access_token)
    assert raw not in repr(issued)
    assert "redacted" in repr(issued).lower()


def test_rotation_returns_a_new_secret_and_rejects_entropy_collision() -> None:
    previous = issue_agent_token(random_bytes=lambda size: b"a" * size)
    rotated = rotate_agent_token(
        previous.token_digest,
        random_bytes=lambda size: b"b" * size,
    )

    assert rotated.access_token.reveal() != previous.access_token.reveal()
    assert rotated.token_digest != previous.token_digest
    assert verify_agent_token(rotated.access_token, rotated.token_digest)
    assert not verify_agent_token(previous.access_token, rotated.token_digest)
    with pytest.raises(TokenRotationError, match="new secret"):
        rotate_agent_token(
            previous.token_digest,
            random_bytes=lambda size: b"a" * size,
        )


@pytest.mark.parametrize(
    "token",
    [
        "",
        "rpat_v1.short",
        "rpat_v0." + "A" * 43,
        "rpat_v1." + "A" * 42 + "=",
        "rpat_v1." + "A" * 42 + "+",
        " rpat_v1." + "A" * 43,
        "rpat_v1." + "A" * 43 + "\n",
    ],
)
def test_strict_parser_and_digest_reject_malformed_tokens(token: str) -> None:
    with pytest.raises(MalformedAgentToken):
        parse_agent_token(token)
    with pytest.raises(MalformedAgentToken):
        digest_agent_token(token)
    assert not verify_agent_token(token, "sha256:" + "0" * 64)


@pytest.mark.parametrize("value", [b"", b"x" * 31, b"x" * 33, "x" * 32])
def test_issuance_rejects_invalid_entropy_source(value: object) -> None:
    with pytest.raises(TokenEntropyError, match="exactly 32 bytes"):
        issue_agent_token(random_bytes=lambda _size: value)  # type: ignore[return-value]


def test_verifier_uses_constant_time_digest_comparison_even_for_malformed_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = issue_agent_token(random_bytes=lambda size: b"c" * size)
    comparisons: list[tuple[bytes, bytes]] = []
    constant_time_compare = hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        comparisons.append((left, right))
        return constant_time_compare(left, right)

    monkeypatch.setattr(
        "review_platform.infrastructure.auth.agent_tokens.hmac.compare_digest",
        recording_compare,
    )

    assert verify_agent_token(issued.access_token, issued.token_digest)
    assert not verify_agent_token("malformed", issued.token_digest)
    assert len(comparisons) == 2
    assert all(len(left) == len(right) == 71 for left, right in comparisons)


def test_secret_constructor_and_rotation_digest_are_fail_closed() -> None:
    with pytest.raises(MalformedAgentToken):
        AgentTokenSecret("raw-secret")
    with pytest.raises(TokenRotationError, match="canonical sha256"):
        rotate_agent_token("SHA256:" + "A" * 64)
