"""US6 RED acceptance for ambiguous delivery recovery and manual retry."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import anyio
import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Table, func, select, text

from review_platform.application.foundation_runtime import FoundationRuntime
from review_platform.application.ports.providers import DeliveryProvider, ProviderPayload
from review_platform.application.request_context import RequestActor
from review_platform.contracts.commands import WireCommand
from review_platform.domain.delivery_payload import (
    parse_deliver_request,
    parse_reconcile_request,
    render_delivery_result,
)
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import DestinationBinding
from review_platform.infrastructure.db.models.operations import Operation, OutboxMessage
from review_platform.infrastructure.db.models.publication import (
    ExternalDelivery,
    ReviewPublication,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.providers.mocks import (
    FixtureDeliveryProvider,
    FrozenFixtureStore,
)
from review_platform.infrastructure.tasks.deliveries import (
    DELIVERY_TASK_RUNTIME_FACTORY_ENV,
    DeliveryTaskRuntime,
)
from review_platform.infrastructure.tasks.registry import REGISTRY, load_handler_modules
from review_platform.main import create_app

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
METHOD = UUID("00000000-0000-7000-8000-000000017001")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000017002")
DELIVERY_A = UUID("00000000-0000-7000-8000-000000000030")
DELIVERY_B = UUID("00000000-0000-7000-8000-000000017003")
OPERATION_A = UUID("00000000-0000-7000-8000-000000017004")
OPERATION_B = UUID("00000000-0000-7000-8000-000000017005")
MESSAGE_A = UUID("00000000-0000-7000-8000-000000017006")
MESSAGE_B = UUID("00000000-0000-7000-8000-000000017007")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

DELIVERY_EVENT = "ExternalDeliveryRequested"
RECONCILE_EVENT = "ExternalDeliveryReconciliationRequested"
RETRY_ROUTE_TEMPLATE = "/api/v1/deliveries/{deliveryId}/retry"
LIST_ROUTE = "/api/v1/deliveries"


@dataclass(slots=True)
class DeliveryHarness:
    app: FastAPI
    client: AsyncClient
    session_factory: AsyncSessionFactory
    provider: ScenarioDeliveryProvider


class ScenarioDeliveryProvider:
    """Frozen-schema offline provider with per-delivery deterministic outcomes."""

    contract_version = "1.1.0"
    schema_name = "delivery.schema.json"

    def __init__(self) -> None:
        self._fixture = FrozenFixtureStore().load("delivery-v1.1.0.json")
        self._delivery_outcomes: dict[UUID, list[str]] = {}
        self._reconciliation_outcomes: dict[UUID, list[str]] = {}

    def delivery_outcomes(self, delivery_id: UUID, *outcomes: str) -> None:
        self._delivery_outcomes[delivery_id] = list(outcomes)

    def reconciliation_outcomes(self, delivery_id: UUID, *outcomes: str) -> None:
        self._reconciliation_outcomes[delivery_id] = list(outcomes)

    async def deliver(self, request: ProviderPayload) -> ProviderPayload:
        parsed = parse_deliver_request(request)
        outcome = self._next(
            self._delivery_outcomes,
            parsed.delivery_id,
            "success_result",
        )
        return self._result(outcome, parsed.organization_id, parsed.delivery_id)

    async def reconcile(self, request: ProviderPayload) -> ProviderPayload:
        parsed = parse_reconcile_request(request)
        outcome = self._next(
            self._reconciliation_outcomes,
            parsed.delivery_id,
            "reconcile_found_result",
        )
        return self._result(outcome, parsed.organization_id, parsed.delivery_id)

    @staticmethod
    def _next(
        configured: dict[UUID, list[str]],
        delivery_id: UUID,
        default: str,
    ) -> str:
        outcomes = configured.get(delivery_id)
        if not outcomes:
            return default
        return outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]

    def _result(
        self,
        outcome: str,
        organization_id: UUID,
        delivery_id: UUID,
    ) -> ProviderPayload:
        value = deepcopy(cast(dict[str, Any], self._fixture[outcome]))
        value["organization_id"] = str(organization_id)
        value["delivery_id"] = str(delivery_id)
        return cast(ProviderPayload, render_delivery_result(value))


_TASK_SESSION_FACTORY: AsyncSessionFactory | None = None
_TASK_PROVIDER: ScenarioDeliveryProvider | None = None


def build_delivery_task_runtime() -> DeliveryTaskRuntime:
    """Explicit test-only runtime factory resolved by the registered workers."""

    if _TASK_SESSION_FACTORY is None or _TASK_PROVIDER is None:
        raise RuntimeError("delivery test runtime fixture is not installed")
    provider: DeliveryProvider = _TASK_PROVIDER
    return DeliveryTaskRuntime(
        session_factory=_TASK_SESSION_FACTORY,
        providers={"stepik": provider, "github": provider},
        clock=lambda: NOW,
        max_attempts=5,
        lease_seconds=60,
        retry_seconds=30,
    )


@pytest.fixture
async def delivery_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[DeliveryHarness]:
    global _TASK_PROVIDER, _TASK_SESSION_FACTORY

    provider = ScenarioDeliveryProvider()
    _TASK_SESSION_FACTORY = foundation_session_factory
    _TASK_PROVIDER = provider
    monkeypatch.setenv(
        DELIVERY_TASK_RUNTIME_FACTORY_ENV,
        f"{__name__}:build_delivery_task_runtime",
    )
    app = create_app(runtime=foundation_runtime)
    actor = RequestActor.user(
        organization_id=ORG,
        user_id=METHOD,
        roles={"methodologist"},
        membership_revision=0,
        auth_epoch=0,
    )

    @app.middleware("http")
    async def inject_methodologist(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://review-platform.test",
    ) as client:
        yield DeliveryHarness(app, client, foundation_session_factory, provider)
    _TASK_SESSION_FACTORY = None
    _TASK_PROVIDER = None


async def _mysql_ready(factory: AsyncSessionFactory) -> None:
    async with factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1


def _recovery_tables() -> dict[str, Table]:
    names = ("delivery_attempt", "delivery_reconciliation_observation")
    missing = [name for name in names if name not in Base.metadata.tables]
    assert not missing, f"US6 delivery recovery persistence is missing: {missing}"
    return {name: Base.metadata.tables[name] for name in names}


def _require_handlers() -> tuple[object, object]:
    load_handler_modules()
    events = {spec.event_type for spec in REGISTRY}
    assert DELIVERY_EVENT in events, "US6 ExternalDeliveryRequested worker is not implemented"
    assert RECONCILE_EVENT in events, (
        "US6 ExternalDeliveryReconciliationRequested worker is not implemented"
    )
    return (
        REGISTRY.resolve_event(DELIVERY_EVENT).handler,
        REGISTRY.resolve_event(RECONCILE_EVENT).handler,
    )


def _require_routes(app: FastAPI) -> None:
    routes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert (RETRY_ROUTE_TEMPLATE, "POST") in routes, (
        "US6 human manual delivery retry route is not implemented"
    )
    assert (LIST_ROUTE, "GET") in routes, "US6 delivery list route is not implemented"


def _fixture_request() -> dict[str, Any]:
    return deepcopy(
        cast(
            dict[str, Any],
            FrozenFixtureStore().load("delivery-v1.1.0.json")["request"],
        )
    )


def _second_request() -> dict[str, Any]:
    request = _fixture_request()
    request["delivery_id"] = str(DELIVERY_B)
    request["delivery_key"] = "review:1:github:2"
    request["destination"] = {
        **request["destination"],
        "binding_id": "00000000-0000-7000-8000-000000017008",
        "credential_binding_id": "00000000-0000-7000-8000-000000017009",
        "kind": "github",
        "recipient_ref": "example/repository#review",
    }
    return request


async def test_timeout_enters_unknown_then_reconciles_found_before_any_retry(
    delivery_harness: DeliveryHarness,
) -> None:
    await _mysql_ready(delivery_harness.session_factory)
    request = cast(ProviderPayload, _fixture_request())
    unknown = await FixtureDeliveryProvider(deliver_outcome="unknown_result").deliver(
        request
    )
    found = await FixtureDeliveryProvider(
        reconcile_outcome="reconcile_found_result"
    ).reconcile(cast(ProviderPayload, _reconcile_request()))
    assert unknown["outcome"] == "unknown_outcome"
    assert found["outcome"] == "succeeded"
    tables = _recovery_tables()
    delivery_harness.provider.delivery_outcomes(DELIVERY_A, "unknown_result")
    delivery_harness.provider.reconciliation_outcomes(
        DELIVERY_A,
        "reconcile_found_result",
    )
    await _seed_delivery(delivery_harness.session_factory, request=_fixture_request())
    deliver, reconcile = _require_handlers()

    await _invoke(deliver, MESSAGE_A)
    unknown_row = await _delivery(delivery_harness.session_factory, DELIVERY_A)
    assert unknown_row.state == "unknown_outcome"
    assert not await _has_pending_event(
        delivery_harness.session_factory,
        DELIVERY_EVENT,
        exclude_message=MESSAGE_A,
    )
    reconciliation_message = await _event_message(
        delivery_harness.session_factory,
        RECONCILE_EVENT,
    )
    await _invoke(reconcile, reconciliation_message)

    succeeded = await _delivery(delivery_harness.session_factory, DELIVERY_A)
    assert succeeded.state == "succeeded"
    assert succeeded.external_id == "external-result-1"
    assert succeeded.external_url == "https://provider.example.test/results/1"
    assert await _count(delivery_harness.session_factory, tables["delivery_attempt"]) == 1
    assert await _count(
        delivery_harness.session_factory,
        tables["delivery_reconciliation_observation"],
    ) == 1


async def test_reconcile_not_found_schedules_delivery_retry_only_after_observation(
    delivery_harness: DeliveryHarness,
) -> None:
    await _mysql_ready(delivery_harness.session_factory)
    not_found = await FixtureDeliveryProvider(
        reconcile_outcome="reconcile_not_found_result"
    ).reconcile(cast(ProviderPayload, _reconcile_request()))
    assert not_found["outcome"] == "not_found"
    deliver, reconcile = _require_handlers()
    tables = _recovery_tables()
    delivery_harness.provider.delivery_outcomes(
        DELIVERY_A,
        "unknown_result",
        "success_result",
    )
    delivery_harness.provider.reconciliation_outcomes(
        DELIVERY_A,
        "reconcile_not_found_result",
    )
    await _seed_delivery(delivery_harness.session_factory, request=_fixture_request())

    await _invoke(deliver, MESSAGE_A)
    reconciliation_message = await _event_message(
        delivery_harness.session_factory,
        RECONCILE_EVENT,
    )
    await _invoke(reconcile, reconciliation_message)
    observation_count = await _count(
        delivery_harness.session_factory,
        tables["delivery_reconciliation_observation"],
    )
    retry_message = await _event_message(
        delivery_harness.session_factory,
        DELIVERY_EVENT,
        exclude=MESSAGE_A,
    )
    assert observation_count == 1
    assert retry_message != MESSAGE_A
    await _invoke(deliver, retry_message)
    assert (await _delivery(delivery_harness.session_factory, DELIVERY_A)).state in {
        "succeeded",
        "action_required",
    }


async def test_ambiguous_reconciliation_requires_human_retry_command(
    delivery_harness: DeliveryHarness,
) -> None:
    await _mysql_ready(delivery_harness.session_factory)
    ambiguous = await FixtureDeliveryProvider(
        reconcile_outcome="reconcile_action_required_result"
    ).reconcile(cast(ProviderPayload, _reconcile_request()))
    assert ambiguous["outcome"] == "action_required"
    _require_routes(delivery_harness.app)
    _recovery_tables()
    await _seed_delivery(
        delivery_harness.session_factory,
        request=_fixture_request(),
        state="action_required",
    )
    body = {
        "request_id": "00000000-0000-7000-8000-000000017020",
        "idempotency_key": "manual-delivery-retry-0001",
        "command_name": "retry_delivery",
        "revision_target": "external_delivery",
        "target_id": str(DELIVERY_A),
        "expected_revision": 0,
        "payload": {"reconcile_first": True},
    }
    WireCommand.model_validate(body)

    response = await delivery_harness.client.post(
        RETRY_ROUTE_TEMPLATE.replace("{deliveryId}", str(DELIVERY_A)),
        json=body,
    )

    assert response.status_code == 202, response.text
    assert UUID(response.json()["id"]) == OPERATION_A
    delivery = await _delivery(delivery_harness.session_factory, DELIVERY_A)
    assert delivery.state in {"unknown_outcome", "reconciling"}
    assert await _has_pending_event(
        delivery_harness.session_factory,
        RECONCILE_EVENT,
    )


async def test_two_required_destinations_recover_independently(
    delivery_harness: DeliveryHarness,
) -> None:
    await _mysql_ready(delivery_harness.session_factory)
    assert (
        await FixtureDeliveryProvider().deliver(
            cast(ProviderPayload, _fixture_request())
        )
    )["outcome"] == "succeeded"
    assert (
        await FixtureDeliveryProvider(deliver_outcome="unknown_result").deliver(
            cast(ProviderPayload, _fixture_request())
        )
    )["outcome"] == "unknown_outcome"
    deliver, reconcile = _require_handlers()
    _recovery_tables()
    delivery_harness.provider.delivery_outcomes(DELIVERY_A, "success_result")
    delivery_harness.provider.delivery_outcomes(DELIVERY_B, "unknown_result")
    delivery_harness.provider.reconciliation_outcomes(
        DELIVERY_B,
        "reconcile_found_result",
    )
    await _seed_delivery(delivery_harness.session_factory, request=_fixture_request())
    await _seed_delivery(
        delivery_harness.session_factory,
        request=_second_request(),
        delivery_id=DELIVERY_B,
        operation_id=OPERATION_B,
        message_id=MESSAGE_B,
    )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_invoke, deliver, MESSAGE_A)
        tasks.start_soon(_invoke, deliver, MESSAGE_B)
    first = await _delivery(delivery_harness.session_factory, DELIVERY_A)
    second = await _delivery(delivery_harness.session_factory, DELIVERY_B)
    assert {first.state, second.state} == {"succeeded", "unknown_outcome"}

    unknown_delivery = first if first.state == "unknown_outcome" else second
    successful_delivery = second if unknown_delivery is first else first
    successful_snapshot = _delivery_snapshot(successful_delivery)
    reconciliation_message = await _event_message(
        delivery_harness.session_factory,
        RECONCILE_EVENT,
        aggregate_id=unknown_delivery.id,
    )
    await _invoke(reconcile, reconciliation_message)
    assert _delivery_snapshot(
        await _delivery(delivery_harness.session_factory, successful_delivery.id)
    ) == successful_snapshot


async def _seed_delivery(
    factory: AsyncSessionFactory,
    *,
    request: Mapping[str, Any],
    delivery_id: UUID = DELIVERY_A,
    operation_id: UUID = OPERATION_A,
    message_id: UUID = MESSAGE_A,
    state: str = "pending",
) -> None:
    async with session_scope(factory) as session:
        if await session.get(User, METHOD) is None:
            session.add(User(id=METHOD, display_name="Delivery Methodologist"))
            await session.flush()
            session.add(
                OrganizationMembership(
                    id=MEMBERSHIP,
                    organization_id=ORG,
                    user_id=METHOD,
                    roles=["methodologist"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                )
            )
            await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        operation = Operation(
            id=operation_id,
            organization_id=ORG,
            kind="external_delivery",
            input_version=f"delivery:1.1.0:{request['payload_digest']}",
            state=state,
            revision=0,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(operation)
        destination = cast(Mapping[str, Any], request["destination"])
        provenance = cast(Mapping[str, Any], request["provenance"])
        credential_id = UUID(str(destination["credential_binding_id"]))
        credential_version = int(destination["credential_binding_version"])
        binding_id = UUID(str(destination["binding_id"]))
        binding_version = int(destination["binding_version"])
        review_iteration_id = UUID(str(provenance["review_iteration_id"]))
        review_revision_id = UUID(str(provenance["review_revision_id"]))
        publication_id = UUID("00000000-0000-7000-8000-000000017030")
        session.add(
            ExternalCredential(
                id=credential_id,
                organization_id=ORG,
                provider=str(destination["kind"]),
                binding_version=credential_version,
                ciphertext="fixture-only-ciphertext",
                key_id="fixture-only-key",
                status="active",
            )
        )
        session.add(
            DestinationBinding(
                id=binding_id,
                organization_id=ORG,
                course_run_id=UUID(str(provenance["course_run_id"])),
                kind=str(destination["kind"]),
                binding_version=binding_version,
                recipient_ref=str(destination["recipient_ref"]),
                credential_id=credential_id,
                credential_binding_version=credential_version,
                required=True,
                status="active",
                revision=0,
            )
        )
        if await session.get(ReviewPublication, publication_id) is None:
            session.add(
                ReviewPublication(
                    id=publication_id,
                    organization_id=ORG,
                    review_iteration_id=review_iteration_id,
                    review_revision_id=review_revision_id,
                    publication_request_id=None,
                    publication_version=1,
                    published_by=METHOD,
                    published_at=NOW,
                    status="published",
                    revision=0,
                )
            )
        session.add(
            ExternalDelivery(
                id=delivery_id,
                organization_id=ORG,
                publication_id=publication_id,
                operation_id=operation_id,
                delivery_key=str(request["delivery_key"]),
                destination_binding_id=binding_id,
                binding_version=binding_version,
                credential_binding_id=credential_id,
                credential_binding_version=credential_version,
                destination_kind=str(destination["kind"]),
                recipient_ref=str(destination["recipient_ref"]),
                course_run_id=UUID(str(provenance["course_run_id"])),
                homework_version_id=UUID(str(provenance["homework_version_id"])),
                criterion_set_id=UUID(str(provenance["criterion_set_id"])),
                submission_version_id=UUID(str(provenance["submission_version_id"])),
                artifact_version_id=UUID(str(provenance["artifact_version_id"])),
                artifact_content_digest=str(provenance["artifact_content_digest"]),
                review_iteration_id=review_iteration_id,
                review_revision_id=review_revision_id,
                contract_version="1.1.0",
                publication_fingerprint=str(request["publication_fingerprint"]),
                payload_version="1.1.0",
                payload_digest=str(request["payload_digest"]),
                payload=dict(request),
                state=state,
                attempt_count=0,
                revision=0,
            )
        )
        session.add(
            OutboxMessage(
                message_id=message_id,
                organization_id=ORG,
                aggregate_type="external_delivery",
                aggregate_id=delivery_id,
                event_type=DELIVERY_EVENT,
                payload_version="1.1.0",
                payload={"request": dict(request)},
                available_at=NOW,
                enqueue_state="enqueued",
                attempts=1,
                max_attempts=5,
            )
        )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _reconcile_request() -> dict[str, Any]:
    return deepcopy(
        cast(
            dict[str, Any],
            FrozenFixtureStore().load("delivery-v1.1.0.json")["reconcile_request"],
        )
    )


async def _invoke(handler: object, message_id: UUID) -> object:
    assert callable(handler)
    result = handler(organization_id=str(ORG), message_id=str(message_id))
    if isinstance(result, Awaitable):
        return await result
    return result


async def _delivery(factory: AsyncSessionFactory, delivery_id: UUID) -> ExternalDelivery:
    async with factory() as session:
        delivery = await session.get(ExternalDelivery, delivery_id)
        assert delivery is not None
        return delivery


async def _event_message(
    factory: AsyncSessionFactory,
    event_type: str,
    *,
    exclude: UUID | None = None,
    aggregate_id: UUID | None = None,
) -> UUID:
    async with factory() as session:
        statement = select(OutboxMessage.message_id).where(
            OutboxMessage.organization_id == ORG,
            OutboxMessage.event_type == event_type,
        )
        if exclude is not None:
            statement = statement.where(OutboxMessage.message_id != exclude)
        if aggregate_id is not None:
            statement = statement.where(OutboxMessage.aggregate_id == aggregate_id)
        value = await session.scalar(statement.order_by(OutboxMessage.created_at))
        assert value is not None, f"missing durable {event_type} outbox message"
        return value


async def _has_pending_event(
    factory: AsyncSessionFactory,
    event_type: str,
    *,
    exclude_message: UUID | None = None,
) -> bool:
    async with factory() as session:
        statement = select(OutboxMessage.message_id).where(
            OutboxMessage.organization_id == ORG,
            OutboxMessage.event_type == event_type,
            OutboxMessage.enqueue_state.in_(("pending", "enqueued")),
        )
        if exclude_message is not None:
            statement = statement.where(OutboxMessage.message_id != exclude_message)
        return await session.scalar(statement.limit(1)) is not None


async def _count(factory: AsyncSessionFactory, table: Table) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(table)) or 0)


def _delivery_snapshot(delivery: ExternalDelivery) -> tuple[object, ...]:
    return (
        delivery.id,
        delivery.state,
        delivery.attempt_count,
        delivery.external_id,
        delivery.external_url,
        delivery.revision,
        delivery.payload_digest,
    )
