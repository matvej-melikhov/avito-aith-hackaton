"""RED isolation contract for AI component grants, artifact reads, and redaction."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
import pytest

from review_platform.application.foundation_runtime import BoundaryViolation, FoundationRuntime
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = "00000000-0000-7000-8000-000000000001"
OTHER_ORG = "00000000-0000-7000-8000-000000000002"
RUN = "00000000-0000-7000-8000-000000001301"
OTHER_RUN = "00000000-0000-7000-8000-000000001302"
ARTIFACT_VERSION = "00000000-0000-7000-8000-000000001303"
OTHER_ARTIFACT_VERSION = "00000000-0000-7000-8000-000000001304"
COMPONENT_TOKEN = "component-bearer-secret-value-at-least-32-bytes"
SIGNED_SECRET = "signed-url-private-query-secret"
STUDENT_EMAIL = "private-student@example.test"
ARTIFACT_BYTES = "private artifact source bytes"


@pytest.fixture
async def component_client(
    foundation_runtime: FoundationRuntime,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), runtime=foundation_runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


async def test_component_artifact_download_requires_bearer_grant(
    component_client: httpx.AsyncClient,
) -> None:
    response = await component_client.get(
        f"/api/v1/internal/artifacts/{ARTIFACT_VERSION}/content"
    )

    assert response.status_code == 401
    assert set(response.json()) == {"code", "message", "action"}


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("revoked-component-grant-token-at-least-32-bytes", "revoked"),
        ("expired-component-grant-token-at-least-32-bytes", "expired"),
        ("other-tenant-component-token-at-least-32-bytes", "cross-tenant"),
        ("other-run-component-grant-token-at-least-32-bytes", "other-run"),
        ("other-version-component-token-at-least-32-bytes", "other-version"),
    ],
)
async def test_component_grant_fails_closed_for_revoked_expired_or_wrong_scope(
    component_client: httpx.AsyncClient,
    token: str,
    reason: str,
) -> None:
    response = await component_client.get(
        f"/api/v1/internal/artifacts/{ARTIFACT_VERSION}/content",
        headers={
            "Authorization": f"Bearer {token}",
            "X-AI-Organization": OTHER_ORG if reason == "cross-tenant" else ORG,
            "X-AI-Course-Run": OTHER_RUN if reason == "other-run" else RUN,
            "X-AI-Artifact-Version": (
                OTHER_ARTIFACT_VERSION if reason == "other-version" else ARTIFACT_VERSION
            ),
        },
    )

    assert response.status_code == 403
    assert set(response.json()) == {"code", "message", "action"}


async def test_signed_artifact_read_is_exact_tenant_version_and_short_lived(
    foundation_runtime: FoundationRuntime,
) -> None:
    signed = foundation_runtime.sign_artifact_read(
        organization_id=ORG,
        artifact_version_id=ARTIFACT_VERSION,
        requested_by_organization_id=ORG,
    )
    parsed = urlparse(signed)
    query = parse_qs(parsed.query)

    assert ORG in parsed.path
    assert ARTIFACT_VERSION in parsed.path
    ttl = foundation_runtime.settings.ai_signed_url_ttl_seconds
    if "X-Amz-Expires" in query:
        assert query["X-Amz-Expires"] == [str(ttl)]
    else:
        expires_at = int(query["Expires"][0])
        remaining = expires_at - int(datetime.now(UTC).timestamp())
        assert 0 < remaining <= ttl
    assert ttl <= 900
    with pytest.raises(BoundaryViolation, match="organization"):
        foundation_runtime.sign_artifact_read(
            organization_id=ORG,
            artifact_version_id=ARTIFACT_VERSION,
            requested_by_organization_id=OTHER_ORG,
        )


@pytest.mark.parametrize(
    "sink",
    ["log", "api_error", "operation_attempt", "outbox_message", "audit_event"],
)
async def test_ai_failures_remove_tokens_urls_provider_bodies_pii_and_artifact_bytes(
    foundation_runtime: FoundationRuntime,
    sink: str,
) -> None:
    stored = foundation_runtime.write_shared_sink(
        sink=sink,
        organization_id=ORG,
        details={
            "authorization": f"Bearer {COMPONENT_TOKEN}",
            "signed_url": f"https://minio.test/object?signature={SIGNED_SECRET}",
            "provider_response": {
                "body": {"token": COMPONENT_TOKEN, "artifact": ARTIFACT_BYTES},
                "safe_code": "ai_provider_rejected",
            },
            "student_email": STUDENT_EMAIL,
            "artifact_content": ARTIFACT_BYTES,
            "message": "x" * 10_000,
            "action": "inspect_provider_configuration",
        },
    )
    rendered = json.dumps(stored, ensure_ascii=False, sort_keys=True)

    for secret in (
        COMPONENT_TOKEN,
        SIGNED_SECRET,
        STUDENT_EMAIL,
        ARTIFACT_BYTES,
    ):
        assert secret not in rendered
    assert len(rendered.encode("utf-8")) <= 2048
    assert "ai_provider_rejected" in rendered
    assert "inspect_provider_configuration" in rendered


def test_component_scope_constants_are_distinct_contract_identities() -> None:
    assert UUID(ORG) != UUID(OTHER_ORG)
    assert UUID(RUN) != UUID(OTHER_RUN)
    assert UUID(ARTIFACT_VERSION) != UUID(OTHER_ARTIFACT_VERSION)
