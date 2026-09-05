"""US6 RED isolation contract for delivery identity, claims, and supersession."""

from __future__ import annotations

import importlib
import importlib.util
from inspect import Parameter, signature
from types import ModuleType

import pytest
from sqlalchemy import Table, UniqueConstraint, text

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.session import AsyncSessionFactory

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]


async def _mysql_ready(factory: AsyncSessionFactory) -> None:
    async with factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1


def _table(name: str) -> Table:
    table = Base.metadata.tables.get(name)
    assert table is not None, f"T136 delivery recovery table is missing: {name}"
    return table


def _unique_sets(table: Table) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _module(name: str, *, task: str, behavior: str) -> ModuleType:
    specification = importlib.util.find_spec(name)
    assert specification is not None, f"{task} {behavior} module is missing: {name}"
    return importlib.import_module(name)


async def test_duplicate_workers_have_one_durable_attempt_identity_and_lease(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _mysql_ready(foundation_session_factory)
    attempt = _table("delivery_attempt")
    observation = _table("delivery_reconciliation_observation")

    assert {
        "organization_id",
        "external_delivery_id",
        "attempt_number",
        "worker_identity",
        "claim_token",
        "lease_expires_at",
        "state",
        "started_at",
        "finished_at",
        "error_code",
        "sanitized_error",
    } <= set(attempt.columns.keys())
    assert frozenset({"organization_id", "external_delivery_id", "attempt_number"}) in _unique_sets(
        attempt
    )
    assert frozenset({"organization_id", "claim_token"}) in _unique_sets(attempt)
    assert {
        "organization_id",
        "external_delivery_id",
        "attempt_number",
        "observed_at",
        "outcome",
    } <= set(observation.columns.keys())


async def test_two_bindings_of_one_kind_remain_distinct_delivery_identities(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _mysql_ready(foundation_session_factory)
    delivery = _table("external_delivery")
    unique_sets = _unique_sets(delivery)

    assert (
        frozenset(
            {
                "organization_id",
                "publication_id",
                "destination_binding_id",
                "binding_version",
                "payload_version",
            }
        )
        in unique_sets
    )
    assert (
        frozenset(
            {
                "organization_id",
                "publication_id",
                "destination_kind",
                "payload_version",
            }
        )
        not in unique_sets
    )
    assert not delivery.c.destination_binding_id.nullable
    assert not delivery.c.binding_version.nullable


async def test_provider_claim_batch_is_tenant_required_and_rejects_mixed_rows(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _mysql_ready(foundation_session_factory)
    module = _module(
        "review_platform.infrastructure.db.repositories.deliveries",
        task="T140",
        behavior="tenant-scoped provider claim",
    )
    repository_type = getattr(module, "SqlDeliveryRepository", None)
    assert isinstance(repository_type, type), "T140 SqlDeliveryRepository is missing"
    claim_batch = getattr(repository_type, "claim_batch", None)
    assert callable(claim_batch), "T140 duplicate-safe claim_batch is missing"
    parameters = signature(claim_batch).parameters
    assert "organization_id" in parameters
    assert parameters["organization_id"].default is Parameter.empty
    assert "provider_kind" in parameters
    assert "transaction" in parameters


async def test_archive_after_durable_intent_continues_to_terminal_state(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _mysql_ready(foundation_session_factory)
    module = _module(
        "review_platform.application.services.deliveries",
        task="T139",
        behavior="archive-safe durable delivery",
    )
    service_type = getattr(module, "DeliveryService", None)
    assert isinstance(service_type, type), "T139 DeliveryService is missing"
    advance = getattr(service_type, "record_worker_result", None)
    assert callable(advance), "T139 archive-safe worker result transition is missing"
    parameters = signature(advance).parameters
    assert "organization_id" in parameters
    assert "delivery_id" in parameters
    assert "transaction" in parameters
    # Course/CourseRun archive state must not be an input that cancels an
    # already-durable intent; final authority comes from the delivery identity.
    assert "course_status" not in parameters
    assert "course_run_status" not in parameters


async def test_stale_publication_order_supersedes_before_provider_effect(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _mysql_ready(foundation_session_factory)
    delivery = _table("external_delivery")
    assert "superseded" in " ".join(
        str(constraint.sqltext)
        for constraint in delivery.constraints
        if hasattr(constraint, "sqltext")
    )

    module = _module(
        "review_platform.infrastructure.db.repositories.deliveries",
        task="T140",
        behavior="stale-publication guard",
    )
    repository_type = getattr(module, "SqlDeliveryRepository", None)
    assert isinstance(repository_type, type), "T140 SqlDeliveryRepository is missing"
    supersede = getattr(repository_type, "supersede_stale_publications", None)
    assert callable(supersede), "T140 stale publication supersession primitive is missing"
    parameters = signature(supersede).parameters
    assert {
        "organization_id",
        "course_run_id",
        "review_iteration_id",
        "publication_id",
        "publication_version",
        "transaction",
    } <= set(parameters)
