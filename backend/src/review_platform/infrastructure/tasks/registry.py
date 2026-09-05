"""Explicit background-handler registry.

Handlers are registered only by modules that actually implement them.  There
are intentionally no placeholder task names in Foundation: later story tasks
extend ``HANDLER_MODULES`` and register concrete callables here.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import ParamSpec, TypeVar, cast
from uuid import UUID

from taskiq.abc.broker import AsyncBroker

from review_platform.application.services.homeworks import HomeworkRequirementsChanged
from review_platform.application.services.review_requirement_impacts import (
    ReviewRequirementImpactAuditPort,
    ReviewRequirementImpactOperationPort,
    ReviewRequirementImpactRepository,
    ReviewRequirementImpactService,
)
from review_platform.domain.primitives import utc_now, uuid7
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

from .broker import AUTH_REVALIDATION_LABEL, KIND_LABEL, BrokerPolicy

P = ParamSpec("P")
R = TypeVar("R")
Handler = Callable[..., Awaitable[object] | object]
REVIEW_IMPACT_HANDLER_FACTORY_ENV = "REVIEW_PLATFORM_REVIEW_IMPACT_HANDLER_FACTORY"


class HandlerRegistryError(RuntimeError):
    """The executable worker registry is inconsistent."""


@dataclass(frozen=True, slots=True)
class ReviewImpactTaskRuntime:
    session_factory: AsyncSessionFactory
    repository: ReviewRequirementImpactRepository
    operations: ReviewRequirementImpactOperationPort
    audit: ReviewRequirementImpactAuditPort
    id_factory: Callable[[], UUID] = uuid7
    clock: Callable[[], datetime] = utc_now


class ReviewImpactTaskHandler:
    def __init__(self, runtime: ReviewImpactTaskRuntime) -> None:
        self._runtime = runtime

    async def __call__(
        self,
        *,
        organization_id: str,
        message_id: str,
    ) -> Mapping[str, object]:
        organization_uuid = _uuid(organization_id, field="organization_id")
        message_uuid = _uuid(message_id, field="message_id")
        async with session_scope(self._runtime.session_factory) as session:
            message = await OutboxMessageRepository(session).get(
                organization_uuid,
                message_uuid,
            )
            if message is None:
                raise HandlerRegistryError(
                    "tenant HomeworkRequirementsChanged outbox message was not found"
                )
            if (
                message.organization_id != organization_uuid
                or message.event_type != "HomeworkRequirementsChanged"
                or message.payload_version != "1.1.0"
                or message.aggregate_type != "course_run_homework"
            ):
                raise HandlerRegistryError(
                    "outbox row is not a supported HomeworkRequirementsChanged event"
                )
            payload = cast(Mapping[str, object], message.payload)
            event = _homework_requirements_event(payload)
            if (
                event.organization_id != organization_uuid
                or event.course_run_homework_id != message.aggregate_id
            ):
                raise HandlerRegistryError(
                    "HomeworkRequirementsChanged aggregate provenance mismatched"
                )
            result = await ReviewRequirementImpactService(
                repository=self._runtime.repository,
                operations=self._runtime.operations,
                audit=self._runtime.audit,
                id_factory=self._runtime.id_factory,
                clock=self._runtime.clock,
            ).consume(
                source_event_id=message_uuid,
                event=event,
                transaction=session,
            )
        return {
            "organization_id": organization_id,
            "message_id": message_id,
            "impact_ids": sorted(
                str(identity)
                for identity in (
                    *result.created_impact_ids,
                    *result.replayed_impact_ids,
                )
            ),
            "proposals": [
                {
                    "review_case_id": str(item.review_case_id),
                    "predecessor_iteration_id": str(
                        item.predecessor_iteration_id
                    ),
                    "effective_homework_version_id": str(
                        item.effective_homework_version_id
                    ),
                    "target_homework_version_id": str(
                        item.target_homework_version_id
                    ),
                    "target_criterion_set_id": str(item.target_criterion_set_id),
                    "origin": item.origin,
                }
                for item in result.proposals
            ],
        }


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    name: str
    kind: str
    event_type: str
    handler: Handler
    requires_auth_revalidation: bool


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
)


def task_handler(
    *,
    name: str,
    kind: str,
    event_type: str,
    requires_auth_revalidation: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register a concrete handler while preserving its callable type."""

    def decorator(handler: Callable[P, R]) -> Callable[P, R]:
        REGISTRY.register(
            name=name,
            kind=kind,
            event_type=event_type,
            handler=cast(Handler, handler),
            requires_auth_revalidation=requires_auth_revalidation,
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

    names: list[str] = []
    for spec in registry.for_queue(queue_name=queue_name, policy=policy):
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


def _load_review_impact_handler() -> Handler:
    specification = os.environ.get(REVIEW_IMPACT_HANDLER_FACTORY_ENV)
    if not specification:
        raise HandlerRegistryError(f"{REVIEW_IMPACT_HANDLER_FACTORY_ENV} is required")
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise HandlerRegistryError(
            f"{REVIEW_IMPACT_HANDLER_FACTORY_ENV} must use module:factory syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise HandlerRegistryError("review impact handler factory is not callable")
    configured = factory()
    if isinstance(configured, ReviewImpactTaskRuntime):
        return cast(Handler, ReviewImpactTaskHandler(configured))
    if not callable(configured):
        raise HandlerRegistryError("review impact factory result is not callable")
    return cast(Handler, configured)


async def handle_homework_requirements_changed(
    *,
    organization_id: str,
    message_id: str,
) -> object:
    handler = _load_review_impact_handler()
    result = handler(organization_id=organization_id, message_id=message_id)
    if isinstance(result, Awaitable):
        return await result
    return result


REGISTRY.register(
    name="review_platform.review_requirement_impacts",
    # Broker/settings do not yet expose a dedicated local projection lane.
    # Reuse the bounded general worker lane already used by course-imported
    # requirement changes; this task itself performs no provider I/O.
    kind="course_import",
    event_type="HomeworkRequirementsChanged",
    handler=handle_homework_requirements_changed,
    requires_auth_revalidation=False,
)


def _homework_requirements_event(
    payload: Mapping[str, object],
) -> HomeworkRequirementsChanged:
    if payload.get("contract_version") != "1.1.0":
        raise HandlerRegistryError(
            "HomeworkRequirementsChanged contract version is unsupported"
        )
    publication_sequence = payload.get("publication_sequence")
    if (
        not isinstance(publication_sequence, int)
        or isinstance(publication_sequence, bool)
        or publication_sequence < 1
    ):
        raise HandlerRegistryError(
            "HomeworkRequirementsChanged publication sequence is invalid"
        )
    return HomeworkRequirementsChanged(
        organization_id=_required_uuid(payload, "organization_id"),
        course_run_id=_required_uuid(payload, "course_run_id"),
        course_run_homework_id=_required_uuid(
            payload,
            "course_run_homework_id",
        ),
        homework_id=_required_uuid(payload, "homework_id"),
        previous_homework_version_id=_optional_uuid(
            payload,
            "previous_homework_version_id",
        ),
        current_homework_version_id=_required_uuid(
            payload,
            "current_homework_version_id",
        ),
        previous_publication_id=_optional_uuid(
            payload,
            "previous_publication_id",
        ),
        current_publication_id=_required_uuid(
            payload,
            "current_publication_id",
        ),
        publication_sequence=publication_sequence,
    )


def _required_uuid(payload: Mapping[str, object], field: str) -> UUID:
    value = payload.get(field)
    if not isinstance(value, str):
        raise HandlerRegistryError(f"HomeworkRequirementsChanged {field} is invalid")
    return _uuid(value, field=field)


def _optional_uuid(payload: Mapping[str, object], field: str) -> UUID | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HandlerRegistryError(f"HomeworkRequirementsChanged {field} is invalid")
    return _uuid(value, field=field)


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise HandlerRegistryError(
            f"HomeworkRequirementsChanged {field} must be a UUID"
        ) from error


__all__ = [
    "HANDLER_MODULES",
    "REGISTRY",
    "REVIEW_IMPACT_HANDLER_FACTORY_ENV",
    "HandlerRegistry",
    "HandlerRegistryError",
    "HandlerSpec",
    "ReviewImpactTaskHandler",
    "ReviewImpactTaskRuntime",
    "bind_handlers",
    "handle_homework_requirements_changed",
    "load_handler_modules",
    "task_handler",
]
