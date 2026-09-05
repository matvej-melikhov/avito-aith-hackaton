from __future__ import annotations

import sys
from types import ModuleType
from typing import cast

import pytest
from taskiq.abc.broker import AsyncBroker

from review_platform.infrastructure.db.session import AsyncSessionFactory
from review_platform.infrastructure.providers.mocks import FixtureDeliveryProvider
from review_platform.infrastructure.tasks.broker import (
    AUTH_REVALIDATION_LABEL,
    KIND_LABEL,
    BrokerPolicy,
)
from review_platform.infrastructure.tasks.deliveries import (
    DELIVERY_EVENT,
    DELIVERY_TASK_RUNTIME_FACTORY_ENV,
    RECONCILIATION_EVENT,
    DeliveryTaskError,
    DeliveryTaskRuntime,
    validate_delivery_task_configuration,
)
from review_platform.infrastructure.tasks.registry import (
    HANDLER_MODULES,
    REGISTRY,
    HandlerRegistry,
    bind_handlers,
    load_handler_modules,
)


class Broker:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, dict[str, object]]] = []

    def register_task(
        self,
        _: object,
        *,
        task_name: str,
        **labels: object,
    ) -> None:
        self.tasks.append((task_name, labels))


def _policy() -> BrokerPolicy:
    return BrokerPolicy(
        redis_url="redis://localhost:6379/0",
        max_attempts=5,
        initial_retry_seconds=2,
        max_retry_seconds=60,
        concurrency_by_kind={"external_delivery": 4},
        queue_by_kind={"external_delivery": "review-platform:worker"},
    )


def _delivery_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    for event in (DELIVERY_EVENT, RECONCILIATION_EVENT):
        source = REGISTRY.resolve_event(event)
        registry.register(
            name=source.name,
            kind=source.kind,
            event_type=source.event_type,
            handler=source.handler,
            requires_auth_revalidation=source.requires_auth_revalidation,
            startup_validator=source.startup_validator,
        )
    return registry


def test_delivery_modules_and_handlers_are_exactly_discoverable() -> None:
    registry = load_handler_modules()
    assert "review_platform.infrastructure.tasks.deliveries" in HANDLER_MODULES
    delivery = registry.resolve_event(DELIVERY_EVENT)
    reconciliation = registry.resolve_event(RECONCILIATION_EVENT)
    assert (delivery.name, delivery.kind) == (
        "review_platform.external_delivery",
        "external_delivery",
    )
    assert (reconciliation.name, reconciliation.kind) == (
        "review_platform.external_delivery_reconciliation",
        "external_delivery",
    )
    assert delivery.requires_auth_revalidation is False
    assert reconciliation.requires_auth_revalidation is False
    assert delivery.startup_validator is validate_delivery_task_configuration
    assert reconciliation.startup_validator is validate_delivery_task_configuration


def test_worker_bind_fails_before_partial_registration_without_runtime_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DELIVERY_TASK_RUNTIME_FACTORY_ENV, raising=False)
    broker = Broker()

    with pytest.raises(DeliveryTaskError, match=DELIVERY_TASK_RUNTIME_FACTORY_ENV):
        bind_handlers(
            broker=cast(AsyncBroker, broker),
            policy=_policy(),
            queue_name="review-platform:worker",
            registry=_delivery_registry(),
        )

    assert broker.tasks == []


@pytest.mark.anyio
async def test_worker_bind_validates_once_and_uses_bounded_external_delivery_kind(
    foundation_session_factory: AsyncSessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    provider = FixtureDeliveryProvider()

    def build() -> DeliveryTaskRuntime:
        calls.append("factory")
        return DeliveryTaskRuntime(
            session_factory=foundation_session_factory,
            providers={"stepik": provider, "github": provider},
            max_attempts=5,
            lease_seconds=60,
            retry_seconds=10,
        )

    module = ModuleType("delivery_registry_fixture_factory")
    module.build = build  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv(
        DELIVERY_TASK_RUNTIME_FACTORY_ENV,
        f"{module.__name__}:build",
    )
    broker = Broker()

    names = bind_handlers(
        broker=cast(AsyncBroker, broker),
        policy=_policy(),
        queue_name="review-platform:worker",
        registry=_delivery_registry(),
    )

    assert calls == ["factory"]
    assert names == (
        "review_platform.external_delivery",
        "review_platform.external_delivery_reconciliation",
    )
    assert _policy().concurrency_by_kind["external_delivery"] == 4
    assert [name for name, _ in broker.tasks] == list(names)
    assert all(labels[KIND_LABEL] == "external_delivery" for _, labels in broker.tasks)
    assert all(
        labels[AUTH_REVALIDATION_LABEL] is False for _, labels in broker.tasks
    )

    # Validation deliberately has no mutable runtime cache: worker startup can
    # inspect a fresh configured object and handlers load their own runtime.
    validate_delivery_task_configuration()
    assert calls == ["factory", "factory"]
