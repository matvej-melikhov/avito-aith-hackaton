"""Taskiq broker policy, routing, retry, correlation, and guard hook points."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol, cast

from taskiq import SmartRetryMiddleware, TaskiqMessage, TaskiqMiddleware, TaskiqResult
from taskiq.abc.broker import AsyncBroker
from taskiq_redis import ListQueueBroker

from review_platform.settings import Settings, get_settings

TENANT_LABEL: Final = "organization_id"
MESSAGE_LABEL: Final = "message_id"
CORRELATION_LABEL: Final = "correlation_id"
KIND_LABEL: Final = "task_kind"
AUTH_REVALIDATION_LABEL: Final = "requires_auth_revalidation"


class BrokerConfigurationError(RuntimeError):
    """Worker broker configuration is incomplete or unsafe."""


class RetryableTaskError(RuntimeError):
    """A handler explicitly classified its sanitized failure as retryable."""


class WorkerAuthRevalidator(Protocol):
    """Re-check current tenant/actor authorization immediately before work."""

    async def revalidate(self, message: TaskiqMessage) -> None: ...


@dataclass(frozen=True, slots=True)
class BrokerPolicy:
    redis_url: str
    max_attempts: int
    initial_retry_seconds: int
    max_retry_seconds: int
    concurrency_by_kind: Mapping[str, int]
    queue_by_kind: Mapping[str, str]

    @classmethod
    def from_settings(cls, settings: Settings) -> BrokerPolicy:
        if settings.redis_url is None:
            raise BrokerConfigurationError("REVIEW_PLATFORM_REDIS_URL is required for workers")
        return cls(
            redis_url=settings.redis_url,
            max_attempts=settings.provider_max_attempts,
            initial_retry_seconds=settings.retry_initial_seconds,
            max_retry_seconds=settings.retry_max_seconds,
            concurrency_by_kind={
                "course_import": settings.course_import_concurrency,
                "artifact_capture": settings.course_import_concurrency,
                "ai_review": settings.ai_review_concurrency,
                "external_delivery": settings.delivery_concurrency,
                "email": settings.delivery_concurrency,
            },
            queue_by_kind={
                "course_import": "review-platform:worker",
                "artifact_capture": "review-platform:worker",
                "ai_review": "review-platform:worker",
                "external_delivery": "review-platform:worker",
                "email": "review-platform:email",
            },
        )

    def queue_for(self, kind: str) -> str:
        try:
            return self.queue_by_kind[kind]
        except KeyError as exc:
            raise BrokerConfigurationError(f"no Taskiq route for task kind {kind!r}") from exc


class TenantCorrelationMiddleware(TaskiqMiddleware):
    """Fail closed unless every background task carries tenant correlation."""

    def _validate(self, message: TaskiqMessage) -> TaskiqMessage:
        organization_id = message.labels.get(TENANT_LABEL)
        message_id = message.labels.get(MESSAGE_LABEL)
        correlation_id = message.labels.get(CORRELATION_LABEL)
        if not all(isinstance(value, str) and value for value in (organization_id, message_id)):
            raise BrokerConfigurationError(
                "background task requires organization_id and stable message_id labels"
            )
        if correlation_id is None:
            message.labels[CORRELATION_LABEL] = message_id
        elif not isinstance(correlation_id, str) or not correlation_id:
            raise BrokerConfigurationError("correlation_id label must be a non-empty string")
        message.labels["tenant_namespace"] = f"review-platform:{organization_id}"
        return message

    def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        return self._validate(message)

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        return self._validate(message)


class AuthRevalidationMiddleware(TaskiqMiddleware):
    """Invoke the concrete membership/agent guard for authorization-bound jobs."""

    def __init__(self, revalidator: WorkerAuthRevalidator | None) -> None:
        super().__init__()
        self._revalidator = revalidator

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        if not message.labels.get(AUTH_REVALIDATION_LABEL, False):
            return message
        if self._revalidator is None:
            raise BrokerConfigurationError(
                "authorization-bound task has no worker auth revalidation hook"
            )
        await self._revalidator.revalidate(message)
        return message


class KindConcurrencyMiddleware(TaskiqMiddleware):
    """Bound in-process concurrency independently for each task kind."""

    def __init__(self, limits: Mapping[str, int]) -> None:
        super().__init__()
        if not limits or any(limit < 1 for limit in limits.values()):
            raise BrokerConfigurationError("every task-kind concurrency limit must be positive")
        self._semaphores = {kind: asyncio.Semaphore(limit) for kind, limit in limits.items()}
        self._claimed: dict[str, str] = {}

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        kind = message.labels.get(KIND_LABEL)
        if not isinstance(kind, str) or kind not in self._semaphores:
            raise BrokerConfigurationError(f"unconfigured task kind {kind!r}")
        await self._semaphores[kind].acquire()
        self._claimed[message.task_id] = kind
        return message

    def post_execute(self, message: TaskiqMessage, result: TaskiqResult[object]) -> None:
        del result
        self._release(message.task_id)

    def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[object],
        exception: BaseException,
    ) -> None:
        del result, exception
        self._release(message.task_id)

    def _release(self, task_id: str) -> None:
        kind = self._claimed.pop(task_id, None)
        if kind is not None:
            self._semaphores[kind].release()


def create_broker(
    *,
    policy: BrokerPolicy,
    queue_name: str,
    auth_revalidator: WorkerAuthRevalidator | None = None,
) -> AsyncBroker:
    """Create one queue-specific broker with all mandatory policies attached."""

    if queue_name not in set(policy.queue_by_kind.values()):
        raise BrokerConfigurationError(f"unknown Taskiq queue {queue_name!r}")
    broker = ListQueueBroker(policy.redis_url, queue_name=queue_name)
    retry = SmartRetryMiddleware(
        default_retry_count=policy.max_attempts,
        default_retry_label=True,
        no_result_on_retry=True,
        default_delay=policy.initial_retry_seconds,
        use_jitter=False,
        use_delay_exponent=True,
        max_delay_exponent=policy.max_retry_seconds,
        types_of_exceptions=(RetryableTaskError,),
    )
    return cast(
        AsyncBroker,
        broker.with_middlewares(
            TenantCorrelationMiddleware(),
            AuthRevalidationMiddleware(auth_revalidator),
            KindConcurrencyMiddleware(policy.concurrency_by_kind),
            retry,
        ),
    )


def get_worker_broker() -> AsyncBroker:
    """Taskiq CLI factory for the general worker queue."""

    policy = BrokerPolicy.from_settings(get_settings())
    broker = create_broker(policy=policy, queue_name="review-platform:worker")
    _bind_implemented_handlers(broker, policy=policy, queue_name="review-platform:worker")
    return broker


def get_email_broker() -> AsyncBroker:
    """Taskiq CLI factory for the isolated email worker queue."""

    policy = BrokerPolicy.from_settings(get_settings())
    broker = create_broker(policy=policy, queue_name="review-platform:email")
    _bind_implemented_handlers(broker, policy=policy, queue_name="review-platform:email")
    return broker


def _bind_implemented_handlers(
    broker: AsyncBroker,
    *,
    policy: BrokerPolicy,
    queue_name: str,
) -> None:
    # Delayed import avoids broker/registry import cycles and lets story phases
    # extend the explicit module list without changing Taskiq CLI entrypoints.
    from .registry import HandlerRegistryError, bind_handlers, load_handler_modules

    registry = load_handler_modules()
    names = bind_handlers(
        broker=broker,
        policy=policy,
        queue_name=queue_name,
        registry=registry,
    )
    if not names:
        raise HandlerRegistryError(f"no implemented handlers are routed to {queue_name!r}")


__all__ = [
    "AUTH_REVALIDATION_LABEL",
    "CORRELATION_LABEL",
    "KIND_LABEL",
    "MESSAGE_LABEL",
    "TENANT_LABEL",
    "AuthRevalidationMiddleware",
    "BrokerConfigurationError",
    "BrokerPolicy",
    "KindConcurrencyMiddleware",
    "RetryableTaskError",
    "TenantCorrelationMiddleware",
    "WorkerAuthRevalidator",
    "create_broker",
    "get_email_broker",
    "get_worker_broker",
]
