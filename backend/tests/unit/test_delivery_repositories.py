"""MySQL concurrency and history tests for the delivery repository."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import anyio
import pytest
from sqlalchemy import func, select
from tests.unit.test_publication_repositories import (
    ARTIFACT,
    CREDENTIAL,
    DESTINATION,
    ITERATION,
    ORG,
    OTHER_ORG,
    REVIEWER,
    REVISION,
    RUN,
    SET_ONE,
    SUBMISSION_VERSION,
    VERSION_ONE,
    _seed,
)

from review_platform.infrastructure.db.models import (
    CourseRun,
    DeliveryAttempt,
    DeliveryReconciliationObservation,
    DestinationBinding,
    ExternalDelivery,
    Operation,
    ReviewPublication,
    ReviewRevision,
)
from review_platform.infrastructure.db.repositories.deliveries import (
    DeliveryPersistenceConflict,
    ReconciliationObservationDraft,
    SqlDeliveryRepository,
)
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
PUBLICATION_OLD = UUID("00000000-0000-7000-8000-000000140001")
PUBLICATION_NEW = UUID("00000000-0000-7000-8000-000000140002")
REVISION_NEW = UUID("00000000-0000-7000-8000-000000140003")
DESTINATION_TWO = UUID("00000000-0000-7000-8000-000000140004")
DELIVERY_OLD = UUID("00000000-0000-7000-8000-000000140011")
DELIVERY_ONE = UUID("00000000-0000-7000-8000-000000140012")
DELIVERY_TWO = UUID("00000000-0000-7000-8000-000000140013")
OPERATION_OLD = UUID("00000000-0000-7000-8000-000000140021")
OPERATION_ONE = UUID("00000000-0000-7000-8000-000000140022")
OPERATION_TWO = UUID("00000000-0000-7000-8000-000000140023")


async def _seed_deliveries(factory: AsyncSessionFactory) -> None:
    await _seed(factory)
    async with session_scope(factory) as session:
        session.add_all(
            [
                DestinationBinding(
                    id=DESTINATION_TWO,
                    organization_id=ORG,
                    course_run_id=RUN,
                    kind="github",
                    binding_version=1,
                    recipient_ref="example/publication-two",
                    credential_id=CREDENTIAL,
                    credential_binding_version=1,
                    required=True,
                    status="active",
                    revision=0,
                ),
                ReviewRevision(
                    id=REVISION_NEW,
                    organization_id=ORG,
                    review_iteration_id=ITERATION,
                    revision_number=2,
                    author_user_id=REVIEWER,
                    base_revision_id=REVISION,
                    feedback="Newer publication",
                    total_score=0,
                    created_at=NOW,
                ),
                Operation(
                    id=OPERATION_OLD,
                    organization_id=ORG,
                    kind="external_delivery",
                    input_version="delivery:old",
                    state="pending",
                    revision=0,
                ),
                Operation(
                    id=OPERATION_ONE,
                    organization_id=ORG,
                    kind="external_delivery",
                    input_version="delivery:new-one",
                    state="pending",
                    revision=0,
                ),
                Operation(
                    id=OPERATION_TWO,
                    organization_id=ORG,
                    kind="external_delivery",
                    input_version="delivery:new-two",
                    state="pending",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ReviewPublication(
                    id=PUBLICATION_OLD,
                    organization_id=ORG,
                    review_iteration_id=ITERATION,
                    review_revision_id=REVISION,
                    publication_request_id=None,
                    publication_version=1,
                    published_by=REVIEWER,
                    published_at=NOW - timedelta(minutes=1),
                    status="published",
                    revision=0,
                ),
                ReviewPublication(
                    id=PUBLICATION_NEW,
                    organization_id=ORG,
                    review_iteration_id=ITERATION,
                    review_revision_id=REVISION_NEW,
                    publication_request_id=None,
                    publication_version=2,
                    published_by=REVIEWER,
                    published_at=NOW,
                    status="published",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _delivery(
                    DELIVERY_OLD,
                    OPERATION_OLD,
                    PUBLICATION_OLD,
                    REVISION,
                    DESTINATION,
                    "example/publication",
                    "old",
                ),
                _delivery(
                    DELIVERY_ONE,
                    OPERATION_ONE,
                    PUBLICATION_NEW,
                    REVISION_NEW,
                    DESTINATION,
                    "example/publication",
                    "new-one",
                ),
                _delivery(
                    DELIVERY_TWO,
                    OPERATION_TWO,
                    PUBLICATION_NEW,
                    REVISION_NEW,
                    DESTINATION_TWO,
                    "example/publication-two",
                    "new-two",
                ),
            ]
        )


def _delivery(
    identity: UUID,
    operation_id: UUID,
    publication_id: UUID,
    review_revision_id: UUID,
    destination_id: UUID,
    recipient_ref: str,
    suffix: str,
) -> ExternalDelivery:
    return ExternalDelivery(
        id=identity,
        organization_id=ORG,
        publication_id=publication_id,
        operation_id=operation_id,
        delivery_key=f"review:{publication_id}:{destination_id}:1",
        destination_binding_id=destination_id,
        binding_version=1,
        credential_binding_id=CREDENTIAL,
        credential_binding_version=1,
        destination_kind="github",
        recipient_ref=recipient_ref,
        course_run_id=RUN,
        homework_version_id=VERSION_ONE,
        criterion_set_id=SET_ONE,
        submission_version_id=SUBMISSION_VERSION,
        artifact_version_id=ARTIFACT,
        artifact_content_digest="sha256:" + "a" * 64,
        review_iteration_id=ITERATION,
        review_revision_id=review_revision_id,
        contract_version="1.1.0",
        publication_fingerprint="sha256:" + "b" * 64,
        payload_version="1.1.0",
        payload_digest="sha256:" + "c" * 64,
        payload={"suffix": suffix},
        state="pending",
        attempt_count=0,
        revision=0,
    )


def _ids() -> Callable[[], UUID]:
    values = iter(
        UUID(f"00000000-0000-7000-8000-{number:012d}") for number in range(140100, 140200)
    )
    return values.__next__


async def test_claim_batch_is_tenant_scoped_skip_locked_and_supersedes_stale(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_deliveries(foundation_session_factory)
    repository = SqlDeliveryRepository(
        id_factory=_ids(),
        clock=lambda: NOW + timedelta(seconds=5),
    )
    claimed: list[tuple[str, UUID]] = []

    async def claim(worker: str) -> None:
        async with session_scope(foundation_session_factory) as session:
            rows = await repository.claim_batch(
                ORG,
                "github",
                worker_identity=worker,
                now=NOW,
                lease_for=timedelta(minutes=5),
                limit=10,
                transaction=session,
            )
            claimed.extend((worker, row.delivery.delivery_id) for row in rows)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claim, "worker-a")
        tasks.start_soon(claim, "worker-b")

    assert {identity for _, identity in claimed} == {DELIVERY_ONE, DELIVERY_TWO}
    assert len(claimed) == 2
    async with foundation_session_factory() as session:
        attempts = (
            await session.scalars(
                select(DeliveryAttempt).order_by(
                    DeliveryAttempt.delivery_id,
                    DeliveryAttempt.attempt_number,
                )
            )
        ).all()
        assert len(attempts) == 2
        assert len({attempt.claim_token for attempt in attempts}) == 2
        stale = await session.get(ExternalDelivery, DELIVERY_OLD)
        assert stale is not None and stale.state == "superseded"
        assert (
            await repository.claim_batch(
                OTHER_ORG,
                "github",
                worker_identity="foreign-worker",
                now=NOW,
                lease_for=timedelta(minutes=5),
                limit=10,
                transaction=session,
            )
            == ()
        )


async def test_archive_does_not_block_cas_and_history_observation_is_exact(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_deliveries(foundation_session_factory)
    repository = SqlDeliveryRepository(
        id_factory=_ids(),
        clock=lambda: NOW + timedelta(seconds=5),
    )
    async with session_scope(foundation_session_factory) as session:
        claims = await repository.claim_batch(
            ORG,
            "github",
            worker_identity="worker",
            now=NOW,
            lease_for=timedelta(minutes=5),
            limit=10,
            transaction=session,
        )
        claim = next(item for item in claims if item.delivery.delivery_id == DELIVERY_ONE)

    async with session_scope(foundation_session_factory) as session:
        run = await session.get(CourseRun, RUN)
        assert run is not None
        run.status = "archived"

    async with session_scope(foundation_session_factory) as session:
        locked = await repository.lock(ORG, DELIVERY_ONE, transaction=session)
        assert locked is not None and locked.state == "processing"
        succeeded = await repository.transition(
            locked,
            target_state="succeeded",
            next_attempt_at=None,
            error=None,
            transaction=session,
        )
        assert succeeded.state == "succeeded"
        with pytest.raises(DeliveryPersistenceConflict, match="CAS"):
            await repository.transition(
                locked,
                target_state="retryable_failed",
                next_attempt_at=NOW + timedelta(minutes=1),
                error={"code": "stale", "message": "stale"},
                transaction=session,
            )
        observation, created = await repository.append_reconciliation_observation(
            ReconciliationObservationDraft(
                observation_id=UUID("00000000-0000-7000-8000-000000140201"),
                organization_id=ORG,
                delivery_id=DELIVERY_ONE,
                delivery_attempt_id=claim.attempt_id,
                attempt_number=1,
                operation_id=OPERATION_ONE,
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                request_payload_version="1.1.0",
                request_digest="sha256:" + "d" * 64,
                result_payload_version="1.1.0",
                result_digest="sha256:" + "e" * 64,
                observed_at=NOW + timedelta(seconds=5),
                outcome="succeeded",
                external_id="provider-result",
                external_url="https://provider.example/result",
            ),
            transaction=session,
        )
        assert created
        replay, replay_created = await repository.append_reconciliation_observation(
            ReconciliationObservationDraft(
                observation_id=UUID("00000000-0000-7000-8000-000000140202"),
                organization_id=ORG,
                delivery_id=DELIVERY_ONE,
                delivery_attempt_id=claim.attempt_id,
                attempt_number=1,
                operation_id=OPERATION_ONE,
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                request_payload_version="1.1.0",
                request_digest="sha256:" + "d" * 64,
                result_payload_version="1.1.0",
                result_digest="sha256:" + "e" * 64,
                observed_at=NOW + timedelta(seconds=5),
                outcome="succeeded",
                external_id="provider-result",
                external_url="https://provider.example/result",
            ),
            transaction=session,
        )
        assert not replay_created and replay.id == observation.id
        history = await repository.history(ORG, DELIVERY_ONE, transaction=session)
        assert history is not None
        assert [item.attempt_number for item in history.attempts] == [1]
        assert [item.id for item in history.observations] == [observation.id]
        assert (
            await session.scalar(
                select(func.count()).select_from(DeliveryReconciliationObservation)
            )
            == 1
        )
