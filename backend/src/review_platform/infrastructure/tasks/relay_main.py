"""Executable SQL-outbox relay process."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from review_platform.domain.primitives import uuid7
from review_platform.infrastructure.db.outbox import SqlOutboxUnitOfWork
from review_platform.infrastructure.db.session import create_database_engine, create_session_factory
from review_platform.settings import Settings, get_settings

from .broker import BrokerPolicy
from .outbox_relay import OutboxPublisher, OutboxRelay, create_taskiq_publisher


class RelayConfigurationError(RuntimeError):
    """Required durable relay infrastructure is not configured."""


@dataclass(slots=True)
class RelayRuntime:
    relay: OutboxRelay
    engine: AsyncEngine
    publisher: OutboxPublisher

    async def close(self) -> None:
        await self.publisher.close()
        await self.engine.dispose()


def build_relay_runtime(
    settings: Settings | None = None,
    *,
    owner: str | None = None,
) -> RelayRuntime:
    configured = settings or get_settings()
    if configured.database_url is None:
        raise RelayConfigurationError("REVIEW_PLATFORM_DATABASE_URL is required for relay")
    if configured.redis_url is None:
        raise RelayConfigurationError("REVIEW_PLATFORM_REDIS_URL is required for relay")
    engine = create_database_engine(configured.database_url)
    uow = SqlOutboxUnitOfWork(
        create_session_factory(engine),
        retry_initial_seconds=configured.retry_initial_seconds,
        retry_max_seconds=configured.retry_max_seconds,
    )
    publisher = create_taskiq_publisher(BrokerPolicy.from_settings(configured))
    relay_owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid7()}"
    return RelayRuntime(
        relay=OutboxRelay(
            outbox=uow,
            publisher=publisher,
            owner=relay_owner,
        ),
        engine=engine,
        publisher=publisher,
    )


async def run() -> None:
    runtime = build_relay_runtime()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, stop.set)
    try:
        await runtime.relay.run_forever(stop=stop)
    finally:
        await runtime.close()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
