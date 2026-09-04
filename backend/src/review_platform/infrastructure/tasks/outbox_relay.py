"""Durable SQL-outbox relay publishing tenant-scoped Redis signals."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from redis.asyncio import Redis
from taskiq import TaskiqMessage
from taskiq.abc.broker import AsyncBroker

from review_platform.domain.primitives import utc_now
from review_platform.infrastructure.db.outbox import (
    OutboxLease,
    OutboxUnitOfWork,
    StaleOutboxLease,
)

from .broker import (
    AUTH_REVALIDATION_LABEL,
    CORRELATION_LABEL,
    KIND_LABEL,
    MESSAGE_LABEL,
    TENANT_LABEL,
    BrokerPolicy,
    create_broker,
)
from .registry import HandlerRegistry, load_handler_modules


def redis_outbox_key(*, organization_id: str, event_type: str) -> str:
    """Return a tenant-prefixed queue key without trusting path separators."""

    if not organization_id or any(character in organization_id for character in " /\\"):
        raise ValueError("organization_id is not safe for a Redis namespace")
    if not event_type or any(character in event_type for character in " /\\"):
        raise ValueError("event_type is not safe for a Redis namespace")
    return f"review-platform:{organization_id}:outbox:{event_type}"


@dataclass(frozen=True, slots=True)
class OutboxSignal:
    message_id: str
    organization_id: str
    tenant_namespace: str
    event_type: str
    payload_version: str

    @classmethod
    def from_lease(cls, lease: OutboxLease) -> OutboxSignal:
        organization_id = str(lease.organization_id)
        return cls(
            message_id=str(lease.message_id),
            organization_id=organization_id,
            tenant_namespace=f"review-platform:{organization_id}",
            event_type=lease.event_type,
            payload_version=lease.payload_version,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "message_id": self.message_id,
            "organization_id": self.organization_id,
            "tenant_namespace": self.tenant_namespace,
            "event_type": self.event_type,
            "payload_version": self.payload_version,
        }


class OutboxPublisher(Protocol):
    async def publish(self, signal: OutboxSignal) -> None: ...

    async def close(self) -> None: ...


class RedisOutboxPublisher:
    """Publish only a stable DB lookup signal; payload stays in MySQL."""

    def __init__(self, redis: Redis, *, signal_ttl_seconds: int = 86_400) -> None:
        if signal_ttl_seconds < 1:
            raise ValueError("Redis outbox signal TTL must be positive")
        self._redis = redis
        self._signal_ttl_seconds = signal_ttl_seconds

    async def publish(self, signal: OutboxSignal) -> None:
        key = redis_outbox_key(
            organization_id=signal.organization_id,
            event_type=signal.event_type,
        )
        encoded = json.dumps(
            signal.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.rpush(key, encoded)
        pipeline.expire(key, self._signal_ttl_seconds)
        await pipeline.execute()

    async def close(self) -> None:
        await self._redis.aclose()


class TaskiqOutboxPublisher:
    """Format stable outbox signals as Taskiq messages on routed Redis queues."""

    def __init__(
        self,
        *,
        policy: BrokerPolicy,
        registry: HandlerRegistry,
        brokers: Mapping[str, AsyncBroker] | None = None,
    ) -> None:
        self._policy = policy
        self._registry = registry
        self._brokers = dict(brokers) if brokers is not None else {
            queue: create_broker(policy=policy, queue_name=queue)
            for queue in set(policy.queue_by_kind.values())
        }

    async def publish(self, signal: OutboxSignal) -> None:
        spec = self._registry.resolve_event(signal.event_type)
        queue_name = self._policy.queue_for(spec.kind)
        try:
            broker = self._brokers[queue_name]
        except KeyError as exc:
            raise RuntimeError(f"no broker configured for Taskiq queue {queue_name!r}") from exc
        message = TaskiqMessage(
            task_id=signal.message_id,
            task_name=spec.name,
            labels={
                TENANT_LABEL: signal.organization_id,
                MESSAGE_LABEL: signal.message_id,
                CORRELATION_LABEL: signal.message_id,
                "tenant_namespace": signal.tenant_namespace,
                KIND_LABEL: spec.kind,
                AUTH_REVALIDATION_LABEL: spec.requires_auth_revalidation,
                "queue_name": queue_name,
            },
            args=[],
            kwargs={
                "organization_id": signal.organization_id,
                "message_id": signal.message_id,
            },
        )
        await broker.kick(broker.formatter.dumps(message))

    async def close(self) -> None:
        for broker in self._brokers.values():
            await broker.shutdown()


@dataclass(frozen=True, slots=True)
class RelayCycle:
    claimed: int
    published: int
    failed: int
    stale: int
    action_required: int


class OutboxRelay:
    """Claim in SQL, publish a stable signal, then CAS-complete the lease."""

    def __init__(
        self,
        *,
        outbox: OutboxUnitOfWork,
        publisher: OutboxPublisher,
        owner: str,
        batch_size: int = 100,
        lease_seconds: int = 30,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not owner:
            raise ValueError("relay owner is required")
        if not 1 <= batch_size <= 1000:
            raise ValueError("relay batch size must be between 1 and 1000")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("relay lease must be between 1 and 3600 seconds")
        self._outbox = outbox
        self._publisher = publisher
        self._owner = owner
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._clock = clock

    async def run_once(self) -> RelayCycle:
        # Claim transaction ends before Redis I/O, so SKIP LOCKED row locks are
        # short-lived and a crash leaves only an expiring durable lease.
        async with self._outbox.transaction() as service:
            leases = await service.lease(
                owner=self._owner,
                now=self._clock(),
                limit=self._batch_size,
                lease_seconds=self._lease_seconds,
            )

        published = 0
        failed = 0
        stale = 0
        action_required = 0
        for lease in leases:
            try:
                await self._publisher.publish(OutboxSignal.from_lease(lease))
            except Exception as error:
                failed += 1
                try:
                    async with self._outbox.transaction() as service:
                        recovery = await service.fail(lease, error=error, now=self._clock())
                    action_required += int(recovery.state == "action_required")
                except StaleOutboxLease:
                    stale += 1
                continue
            try:
                async with self._outbox.transaction() as service:
                    await service.complete(lease, now=self._clock())
                published += 1
            except StaleOutboxLease:
                # The stable message may now exist twice in Redis.  Workers use
                # message_id to claim DB truth idempotently, so never fabricate a
                # successful DB completion here.
                stale += 1

        return RelayCycle(
            claimed=len(leases),
            published=published,
            failed=failed,
            stale=stale,
            action_required=action_required,
        )

    async def run_forever(
        self,
        *,
        stop: asyncio.Event,
        idle_seconds: float = 1.0,
    ) -> None:
        if idle_seconds <= 0:
            raise ValueError("relay idle delay must be positive")
        while not stop.is_set():
            cycle = await self.run_once()
            if cycle.claimed:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=idle_seconds)


def create_redis_publisher(redis_url: str) -> RedisOutboxPublisher:
    if not redis_url:
        raise ValueError("redis_url is required")
    return RedisOutboxPublisher(Redis.from_url(redis_url))


def create_taskiq_publisher(policy: BrokerPolicy) -> TaskiqOutboxPublisher:
    return TaskiqOutboxPublisher(policy=policy, registry=load_handler_modules())


__all__ = [
    "OutboxPublisher",
    "OutboxRelay",
    "OutboxSignal",
    "RedisOutboxPublisher",
    "RelayCycle",
    "TaskiqOutboxPublisher",
    "create_redis_publisher",
    "create_taskiq_publisher",
    "redis_outbox_key",
]
