"""Foundation outbox relay acceptance against real MySQL and Redis."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import cast

import pytest
from redis.asyncio import Redis
from testcontainers.core.container import DockerContainer

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.infrastructure.db.outbox import SqlOutboxUnitOfWork
from review_platform.infrastructure.tasks.outbox_relay import (
    OutboxRelay,
    RedisOutboxPublisher,
    redis_outbox_key,
)

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = "00000000-0000-7000-8000-000000000001"
MESSAGE_ID = "00000000-0000-7000-8000-000000000071"


async def test_sql_outbox_relay_publishes_only_a_stable_tenant_signal(
    foundation_runtime: FoundationRuntime,
    redis_container: DockerContainer,
    fixed_clock: Callable[[], datetime],
) -> None:
    redis = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    publisher = RedisOutboxPublisher(redis)
    outbox = cast(
        SqlOutboxUnitOfWork,
        foundation_runtime.components()["outbox_repository"],
    )
    relay = OutboxRelay(
        outbox=outbox,
        publisher=publisher,
        owner="relay-integration",
        clock=fixed_clock,
    )
    key = redis_outbox_key(organization_id=ORG, event_type="CourseImportRequested")
    try:
        await foundation_runtime.enqueue_outbox(
            organization_id=ORG,
            message_id=MESSAGE_ID,
            payload={"event_type": "CourseImportRequested", "private": "must-stay-in-mysql"},
            max_attempts=2,
        )
        cycle = await relay.run_once()
        encoded = await redis.lpop(key)
        assert encoded is not None
        signal = json.loads(encoded)
        assert cycle.claimed == cycle.published == 1
        assert signal["message_id"] == MESSAGE_ID
        assert signal["organization_id"] == ORG
        assert "must-stay-in-mysql" not in encoded.decode()
    finally:
        await redis.delete(key)
        await publisher.close()
