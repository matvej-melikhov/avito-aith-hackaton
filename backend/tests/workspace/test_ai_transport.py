from uuid import uuid4

import httpx
import pytest

from review_platform.application.workspace.ai_adapter import FixtureWorkspaceAI, HTTPWorkspaceAI
from review_platform.contracts.workspace import PublicCriterion, SelfReviewRequest
from review_platform.settings import Settings


@pytest.mark.anyio
async def test_transport_preserves_idempotency_and_validates_binding():
    request = SelfReviewRequest(
        run_id=uuid4(),
        attempt=1,
        input_fingerprint="sha256:" + "a" * 64,
        artifact_id=uuid4(),
        artifact_url="https://store.invalid/object",
        artifact_digest="sha256:" + "b" * 64,
        media_type="text/markdown",
        student_text="Task",
        criteria=[PublicCriterion(id=uuid4(), key="x", title="X")],
    )
    event = await FixtureWorkspaceAI().submit(request)
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return httpx.Response(200, json=event.model_dump(mode="json"))

    provider = HTTPWorkspaceAI(
        Settings(environment="local", workspace_ai_url="http://ai.local"),
        transport=httpx.MockTransport(handler),
    )
    assert await provider.submit(request) == event
    assert await provider.lookup(request) == event
    assert calls[0].headers["Idempotency-Key"] == f"{request.run_id}:1"
    assert calls[0].url.path == f"/v2/self-reviews/{request.run_id}"
    assert calls[1].method == "GET" and calls[1].url.params["attempt"] == "1"
    event.run_id = uuid4()
    with pytest.raises(OSError, match="does not match"):
        await provider.submit(request)


@pytest.mark.anyio
async def test_unknown_lookup_does_not_submit():
    request = SelfReviewRequest(
        run_id=uuid4(),
        attempt=1,
        input_fingerprint="sha256:" + "a" * 64,
        artifact_id=uuid4(),
        artifact_url="https://store.invalid/object",
        artifact_digest="sha256:" + "b" * 64,
        media_type="text/markdown",
        student_text="Task",
        criteria=[],
    )
    methods = []

    def handler(req):
        methods.append(req.method)
        return httpx.Response(404)

    provider = HTTPWorkspaceAI(
        Settings(environment="local", workspace_ai_url="http://ai.local"),
        transport=httpx.MockTransport(handler),
    )
    assert await provider.lookup(request) is None
    assert methods == ["GET"]
