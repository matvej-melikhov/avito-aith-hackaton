"""Operation reads derive tenant identity only from authenticated server context."""

from __future__ import annotations

import httpx
import pytest

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.main import create_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]


async def test_forged_tenant_header_cannot_replace_authenticated_request_actor(
    foundation_runtime: FoundationRuntime,
) -> None:
    app = create_app(Settings(), runtime=foundation_runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/operations/00000000-0000-7000-8000-000000000099",
            headers={"X-Organization-ID": "00000000-0000-7000-8000-000000000001"},
        )

    assert response.status_code == 401
    operation = app.openapi()["paths"]["/api/v1/operations/{operation_id}"]["get"]
    assert all(parameter["name"] != "X-Organization-ID" for parameter in operation["parameters"])
