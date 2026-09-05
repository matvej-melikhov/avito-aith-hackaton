"""Explicit background-handler registry.

Handlers are registered only by modules that actually implement them.  There
are intentionally no placeholder task names in Foundation: later story tasks
extend ``HANDLER_MODULES`` and register concrete callables here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any, ParamSpec, TypeVar, cast

from taskiq.abc.broker import AsyncBroker

from .broker import AUTH_REVALIDATION_LABEL, KIND_LABEL, BrokerPolicy

P = ParamSpec("P")
R = TypeVar("R")
Handler = Callable[..., Awaitable[object] | object]
StartupValidator = Callable[[], None]


class HandlerRegistryError(RuntimeError):
    """The executable worker registry is inconsistent."""


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    name: str
    kind: str
    event_type: str
    handler: Handler
    requires_auth_revalidation: bool
    startup_validator: StartupValidator | None


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerSpec] = {}

    def register(
        self,
        *,
        name: str,
        kind: str,
        event_type: str,
        handler: Handler,
        requires_auth_revalidation: bool = True,
        startup_validator: StartupValidator | None = None,
    ) -> None:
        if not name or not kind or not event_type:
            raise HandlerRegistryError("handler name, kind, and event type are required")
        if name in self._handlers:
            raise HandlerRegistryError(f"duplicate handler registration: {name}")
        if any(spec.event_type == event_type for spec in self._handlers.values()):
            raise HandlerRegistryError(f"duplicate outbox event registration: {event_type}")
        self._handlers[name] = HandlerSpec(
            name=name,
            kind=kind,
            event_type=event_type,
            handler=handler,
            requires_auth_revalidation=requires_auth_revalidation,
            startup_validator=startup_validator,
        )

    def __iter__(self) -> Iterator[HandlerSpec]:
        return iter(self._handlers.values())

    def __len__(self) -> int:
        return len(self._handlers)

    def names(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def resolve_event(self, event_type: str) -> HandlerSpec:
        for spec in self._handlers.values():
            if spec.event_type == event_type:
                return spec
        raise HandlerRegistryError(f"no implemented handler for outbox event {event_type!r}")

    def for_queue(self, *, queue_name: str, policy: BrokerPolicy) -> tuple[HandlerSpec, ...]:
        return tuple(spec for spec in self if policy.queue_for(spec.kind) == queue_name)


REGISTRY = HandlerRegistry()

# T063/T064 and later story tasks append only modules containing real handlers.
HANDLER_MODULES: tuple[str, ...] = (
    "review_platform.infrastructure.tasks.course_import",
    "review_platform.infrastructure.tasks.email",
    "review_platform.infrastructure.tasks.artifacts",
    "review_platform.infrastructure.tasks.ai_review",
    "review_platform.infrastructure.tasks.review_impacts",
)


def task_handler(
    *,
    name: str,
    kind: str,
    event_type: str,
    requires_auth_revalidation: bool = True,
    startup_validator: StartupValidator | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register a concrete handler while preserving its callable type."""

    def decorator(handler: Callable[P, R]) -> Callable[P, R]:
        REGISTRY.register(
            name=name,
            kind=kind,
            event_type=event_type,
            handler=cast(Handler, handler),
            requires_auth_revalidation=requires_auth_revalidation,
            startup_validator=startup_validator,
        )
        return handler

    return decorator


def load_handler_modules(modules: Sequence[str] = HANDLER_MODULES) -> HandlerRegistry:
    for module_name in modules:
        import_module(module_name)
    return REGISTRY


def bind_handlers(
    *,
    broker: AsyncBroker,
    policy: BrokerPolicy,
    queue_name: str,
    registry: HandlerRegistry = REGISTRY,
) -> tuple[str, ...]:
    """Bind exactly the implemented handlers routed to this worker queue."""

    specs = registry.for_queue(queue_name=queue_name, policy=policy)
    validated: set[int] = set()
    for spec in specs:
        validator = spec.startup_validator
        if validator is not None and id(validator) not in validated:
            validator()
            validated.add(id(validator))
    names: list[str] = []
    for spec in specs:
        broker.register_task(
            spec.handler,
            task_name=spec.name,
            **{
                KIND_LABEL: spec.kind,
                AUTH_REVALIDATION_LABEL: spec.requires_auth_revalidation,
            },
        )
        names.append(spec.name)
    return tuple(names)


def __getattr__(name: str) -> Any:
    """Compatibility re-export without eagerly importing configured handlers."""

    if name in {"REVIEW_IMPACT_HANDLER_FACTORY_ENV", "ReviewImpactTaskRuntime"}:
        from .review_impacts import (
            REVIEW_IMPACT_HANDLER_FACTORY_ENV,
            ReviewImpactTaskRuntime,
        )

        return {
            "REVIEW_IMPACT_HANDLER_FACTORY_ENV": REVIEW_IMPACT_HANDLER_FACTORY_ENV,
            "ReviewImpactTaskRuntime": ReviewImpactTaskRuntime,
        }[name]
    raise AttributeError(name)


__all__ = [
    "HANDLER_MODULES",
    "REGISTRY",
    "HandlerRegistry",
    "HandlerRegistryError",
    "HandlerSpec",
    "StartupValidator",
    "bind_handlers",
    "load_handler_modules",
    "task_handler",
]
