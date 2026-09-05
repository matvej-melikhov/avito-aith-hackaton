from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import func, select, text

from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.services.review_publication import (
    DestinationSnapshot,
    ExternalDeliveryIntent,
)
from review_platform.infrastructure.db.models.delivery import (
    DeliveryAttempt,
    DeliveryReconciliationObservation,
)
from review_platform.infrastructure.db.models.homework import (
    CourseRunHomework,
    CriterionSet,
    Homework,
    HomeworkVersion,
)
from review_platform.infrastructure.db.models.identity import (
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import (
    Course,
    CourseRun,
    DestinationBinding,
)
from review_platform.infrastructure.db.models.operations import (
    Operation,
    OperationAttempt,
    OutboxMessage,
)
from review_platform.infrastructure.db.models.publication import (
    ExternalDelivery,
    ReviewPublication,
)
from review_platform.infrastructure.db.models.review_case import ReviewCase, ReviewIteration
from review_platform.infrastructure.db.models.review_revision import ReviewRevision
from review_platform.infrastructure.db.models.submission import (
    ArtifactReference,
    ArtifactVersion,
    Submission,
    SubmissionVersion,
)
from review_platform.infrastructure.db.repositories.deliveries import SqlDeliveryRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.providers.mocks import (
    FixtureDeliveryProvider,
    FrozenFixtureStore,
)
from review_platform.infrastructure.tasks.deliveries import (
    DELIVERY_EVENT,
    RECONCILIATION_EVENT,
    DeliveryReconciliationTaskHandler,
    DeliveryTaskError,
    DeliveryTaskHandler,
    DeliveryTaskRuntime,
    SqlManualDeliveryScheduler,
    SqlReviewDeliveryScheduler,
)

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000000001")
DELIVERY_A = UUID("00000000-0000-7000-8000-000000000030")
DELIVERY_B = UUID("00000000-0000-7000-8000-000000018001")
OPERATION_A = UUID("00000000-0000-7000-8000-000000018002")
OPERATION_B = UUID("00000000-0000-7000-8000-000000018003")
PUBLICATION = UUID("00000000-0000-7000-8000-000000018004")
USER = UUID("00000000-0000-7000-8000-000000018005")
COURSE = UUID("00000000-0000-7000-8000-000000018006")
STEP_BINDING = UUID("00000000-0000-7000-8000-000000000031")
STEP_CREDENTIAL = UUID("00000000-0000-7000-8000-000000000032")
GITHUB_BINDING = UUID("00000000-0000-7000-8000-000000018007")
GITHUB_CREDENTIAL = UUID("00000000-0000-7000-8000-000000018008")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
HOMEWORK = UUID("00000000-0000-7000-8000-000000018010")
RELATION = UUID("00000000-0000-7000-8000-000000018011")
REFERENCE = UUID("00000000-0000-7000-8000-000000018012")
SUBMISSION = UUID("00000000-0000-7000-8000-000000018013")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000018009")
CAPTURE_OPERATION = UUID("00000000-0000-7000-8000-000000018014")


class IDs:
    def __init__(self, value: int = 20000) -> None:
        self.value = value

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


def _request() -> dict[str, Any]:
    return deepcopy(
        cast(
            dict[str, Any],
            FrozenFixtureStore().load("delivery-v1.1.0.json")["request"],
        )
    )


def _second_request() -> dict[str, Any]:
    request = _request()
    request["delivery_id"] = str(DELIVERY_B)
    request["delivery_key"] = "review:1:github:2"
    request["destination"] = {
        **request["destination"],
        "binding_id": str(GITHUB_BINDING),
        "credential_binding_id": str(GITHUB_CREDENTIAL),
        "kind": "github",
        "recipient_ref": "example/repository#review",
    }
    return request


def _intent(
    request: dict[str, Any],
    *,
    operation_id: UUID,
) -> ExternalDeliveryIntent:
    destination = cast(dict[str, Any], request["destination"])
    provenance = cast(dict[str, Any], request["provenance"])
    return ExternalDeliveryIntent(
        organization_id=ORG,
        delivery_id=UUID(request["delivery_id"]),
        operation_id=operation_id,
        publication_id=PUBLICATION,
        delivery_key=request["delivery_key"],
        destination=DestinationSnapshot(
            organization_id=ORG,
            course_run_id=UUID(provenance["course_run_id"]),
            destination_binding_id=UUID(destination["binding_id"]),
            binding_version=destination["binding_version"],
            credential_binding_id=UUID(destination["credential_binding_id"]),
            credential_binding_version=destination["credential_binding_version"],
            kind=destination["kind"],
            recipient_ref=destination["recipient_ref"],
            required=True,
            status="active",
        ),
        course_run_id=UUID(provenance["course_run_id"]),
        homework_version_id=UUID(provenance["homework_version_id"]),
        criterion_set_id=UUID(provenance["criterion_set_id"]),
        submission_version_id=UUID(provenance["submission_version_id"]),
        artifact_version_id=UUID(provenance["artifact_version_id"]),
        artifact_content_digest=provenance["artifact_content_digest"],
        review_iteration_id=UUID(provenance["review_iteration_id"]),
        review_revision_id=UUID(provenance["review_revision_id"]),
        contract_version="1.1.0",
        publication_fingerprint=request["publication_fingerprint"],
        payload_version="1.1.0",
        payload_digest=request["payload_digest"],
        request=request,
        requested_at=NOW,
    )


async def _seed_parents(factory: AsyncSessionFactory) -> None:
    request = _request()
    provenance = cast(dict[str, Any], request["provenance"])
    async with session_scope(factory) as session:
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        session.add_all(
            [
                User(id=USER, display_name="Delivery publisher"),
                OrganizationMembership(
                    id=UUID("00000000-0000-7000-8000-000000018015"),
                    organization_id=ORG,
                    user_id=USER,
                    roles=["methodologist"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                Course(
                    id=COURSE,
                    organization_id=ORG,
                    title="Delivery course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                CourseRun(
                    id=UUID(provenance["course_run_id"]),
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Delivery run",
                    timezone="UTC",
                    status="active",
                    revision=0,
                ),
                Homework(
                    id=HOMEWORK,
                    organization_id=ORG,
                    course_id=COURSE,
                    title="Delivery homework",
                    revision=0,
                ),
                HomeworkVersion(
                    id=UUID(provenance["homework_version_id"]),
                    organization_id=ORG,
                    homework_id=HOMEWORK,
                    version_number=1,
                    student_text="Delivery requirements",
                    max_score=10,
                    artifact_kinds=["github"],
                    estimated_review_minutes=30,
                    revision=0,
                ),
                CriterionSet(
                    id=UUID(provenance["criterion_set_id"]),
                    organization_id=ORG,
                    homework_version_id=UUID(provenance["homework_version_id"]),
                ),
                CourseRunHomework(
                    id=RELATION,
                    organization_id=ORG,
                    course_run_id=UUID(provenance["course_run_id"]),
                    homework_id=HOMEWORK,
                    current_publication_id=None,
                    status="active",
                    revision=0,
                ),
                ExternalCredential(
                    id=STEP_CREDENTIAL,
                    organization_id=ORG,
                    provider="stepik",
                    binding_version=1,
                    ciphertext="encrypted-stepik",
                    key_id="fixture-key",
                    status="active",
                ),
                ArtifactReference(
                    id=REFERENCE,
                    organization_id=ORG,
                    provider="github",
                    credential_binding_id=GITHUB_CREDENTIAL,
                    credential_binding_version=1,
                    original_url="https://github.com/example/repository",
                    locator={"external_id": "example/repository"},
                    read_capability="available",
                    feedback_capability="available",
                    last_checked_at=NOW,
                    revision=0,
                ),
                ArtifactVersion(
                    id=UUID(provenance["artifact_version_id"]),
                    organization_id=ORG,
                    artifact_reference_id=REFERENCE,
                    provider_version="commit:fixture",
                    content_digest=str(provenance["artifact_content_digest"]),
                    object_key=(
                        f"{ORG}/{provenance['artifact_version_id']}/artifact.bin"
                    ),
                    media_type="application/zip",
                    byte_size=128,
                    captured_at=NOW,
                    artifact_metadata={},
                ),
                Operation(
                    id=CAPTURE_OPERATION,
                    organization_id=ORG,
                    kind="artifact_capture",
                    input_version="delivery-parent-capture",
                    state="succeeded",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    finished_at=NOW,
                ),
                Submission(
                    id=SUBMISSION,
                    organization_id=ORG,
                    course_run_homework_id=RELATION,
                    course_run_id=UUID(provenance["course_run_id"]),
                    homework_id=HOMEWORK,
                    student_id=USER,
                    current_predeadline_version_id=None,
                    revision=0,
                ),
                SubmissionVersion(
                    id=UUID(provenance["submission_version_id"]),
                    organization_id=ORG,
                    submission_id=SUBMISSION,
                    course_run_id=UUID(provenance["course_run_id"]),
                    homework_id=HOMEWORK,
                    sequence=1,
                    homework_version_id=UUID(provenance["homework_version_id"]),
                    artifact_reference_id=REFERENCE,
                    artifact_version_id=UUID(provenance["artifact_version_id"]),
                    submitted_at=NOW,
                    effective_deadline=NOW,
                    phase="before_deadline",
                    status="ready",
                    capture_operation_id=CAPTURE_OPERATION,
                    revision=0,
                ),
                ReviewCase(
                    id=REVIEW_CASE,
                    organization_id=ORG,
                    course_run_id=UUID(provenance["course_run_id"]),
                    homework_id=HOMEWORK,
                    student_id=USER,
                    current_iteration_id=UUID(provenance["review_iteration_id"]),
                    revision=1,
                ),
                ExternalCredential(
                    id=GITHUB_CREDENTIAL,
                    organization_id=ORG,
                    provider="github",
                    binding_version=1,
                    ciphertext="encrypted-github",
                    key_id="fixture-key",
                    status="active",
                ),
                DestinationBinding(
                    id=STEP_BINDING,
                    organization_id=ORG,
                    course_run_id=UUID(provenance["course_run_id"]),
                    kind="stepik",
                    binding_version=1,
                    recipient_ref="submission-1",
                    credential_id=STEP_CREDENTIAL,
                    credential_binding_version=1,
                    required=True,
                    status="active",
                    revision=0,
                ),
                DestinationBinding(
                    id=GITHUB_BINDING,
                    organization_id=ORG,
                    course_run_id=UUID(provenance["course_run_id"]),
                    kind="github",
                    binding_version=1,
                    recipient_ref="example/repository#review",
                    credential_id=GITHUB_CREDENTIAL,
                    credential_binding_version=1,
                    required=True,
                    status="active",
                    revision=0,
                ),
                ReviewIteration(
                    id=UUID(provenance["review_iteration_id"]),
                    organization_id=ORG,
                    review_case_id=REVIEW_CASE,
                    course_run_id=UUID(provenance["course_run_id"]),
                    homework_id=HOMEWORK,
                    student_id=USER,
                    iteration_number=1,
                    submission_version_id=UUID(provenance["submission_version_id"]),
                    artifact_version_id=UUID(provenance["artifact_version_id"]),
                    homework_version_id=UUID(provenance["homework_version_id"]),
                    criterion_set_id=UUID(provenance["criterion_set_id"]),
                    effective_deadline=NOW,
                    status="published",
                    current_revision_id=UUID(provenance["review_revision_id"]),
                    origin="initial",
                    revision=1,
                ),
                ReviewRevision(
                    id=UUID(provenance["review_revision_id"]),
                    organization_id=ORG,
                    review_iteration_id=UUID(provenance["review_iteration_id"]),
                    revision_number=1,
                    author_user_id=USER,
                    base_revision_id=None,
                    feedback="Fixture feedback",
                    total_score=10,
                    created_at=NOW,
                ),
                ReviewPublication(
                    id=PUBLICATION,
                    organization_id=ORG,
                    review_iteration_id=UUID(provenance["review_iteration_id"]),
                    review_revision_id=UUID(provenance["review_revision_id"]),
                    publication_request_id=None,
                    publication_version=1,
                    published_by=USER,
                    published_at=NOW,
                    status="published",
                    revision=0,
                ),
            ]
        )
        await session.flush()
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))


async def _schedule(factory: AsyncSessionFactory, ids: IDs) -> UUID:
    async with session_scope(factory) as session:
        await SqlReviewDeliveryScheduler(id_factory=ids, clock=lambda: NOW).schedule(
            _intent(_request(), operation_id=OPERATION_A),
            transaction=session,
        )
    async with factory() as session:
        message = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.event_type == DELIVERY_EVENT)
        )
        assert message is not None
        return message.message_id


def _runtime(
    factory: AsyncSessionFactory,
    provider: FixtureDeliveryProvider,
    ids: IDs,
) -> DeliveryTaskRuntime:
    return DeliveryTaskRuntime(
        session_factory=factory,
        providers={"stepik": provider, "github": provider},
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
        lease_seconds=60,
        retry_seconds=30,
    )


async def test_scheduler_materializes_two_independent_intents_and_replays_exactly(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_parents(foundation_session_factory)
    ids = IDs()
    scheduler = SqlReviewDeliveryScheduler(id_factory=ids, clock=lambda: NOW)
    first = _intent(_request(), operation_id=OPERATION_A)
    second = _intent(_second_request(), operation_id=OPERATION_B)
    async with session_scope(foundation_session_factory) as session:
        await scheduler.schedule(first, transaction=session)
        await scheduler.schedule(second, transaction=session)
        await scheduler.schedule(first, transaction=session)

    async with foundation_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExternalDelivery)) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Operation)
                .where(Operation.kind == "external_delivery")
            )
            == 2
        )
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 2
        rows = (
            await session.scalars(
                select(ExternalDelivery).order_by(ExternalDelivery.delivery_key)
            )
        ).all()
        assert {row.operation_id for row in rows} == {OPERATION_A, OPERATION_B}
        assert {row.destination_kind for row in rows} == {"stepik", "github"}
        assert all(row.payload["contract_version"] == "1.1.0" for row in rows)


async def test_manual_scheduler_is_reconciliation_first_and_replays_exactly(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_parents(foundation_session_factory)
    ids = IDs(20500)
    await _schedule(foundation_session_factory, ids)
    async with session_scope(foundation_session_factory) as session:
        row = await session.get(ExternalDelivery, DELIVERY_A)
        assert row is not None
        row.state = "action_required"
        await session.flush([row])

    repository = SqlDeliveryRepository(
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
    )
    scheduler = SqlManualDeliveryScheduler(
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
    )
    async with session_scope(foundation_session_factory) as session:
        delivery = await repository.lock(ORG, DELIVERY_A, transaction=session)
        assert delivery is not None
        await scheduler.schedule(delivery, transaction=session)
        await scheduler.schedule(delivery, transaction=session)

    async with foundation_session_factory() as session:
        messages = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.organization_id == ORG,
                    OutboxMessage.aggregate_id == DELIVERY_A,
                    OutboxMessage.event_type == RECONCILIATION_EVENT,
                )
            )
        ).all()
        assert len(messages) == 1
        assert messages[0].payload["trigger"] == "manual_recovery"
        assert messages[0].payload["delivery_revision"] == 0
        assert messages[0].payload["request"] == _request()


async def test_manual_scheduler_retries_delivery_only_after_not_found_observation(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_parents(foundation_session_factory)
    ids = IDs(20700)
    message_id = await _schedule(foundation_session_factory, ids)
    provider = FixtureDeliveryProvider(
        deliver_outcome="unknown_result",
        reconcile_outcome="reconcile_not_found_result",
    )
    runtime = _runtime(foundation_session_factory, provider, ids)
    await DeliveryTaskHandler(runtime, worker_identity="delivery-worker")(
        organization_id=str(ORG),
        message_id=str(message_id),
    )
    async with foundation_session_factory() as session:
        reconciliation_id = await session.scalar(
            select(OutboxMessage.message_id).where(
                OutboxMessage.event_type == RECONCILIATION_EVENT
            )
        )
        assert reconciliation_id is not None
    await DeliveryReconciliationTaskHandler(
        runtime,
        worker_identity="delivery-worker",
    )(
        organization_id=str(ORG),
        message_id=str(reconciliation_id),
    )

    repository = SqlDeliveryRepository(
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
    )
    scheduler = SqlManualDeliveryScheduler(
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
    )
    async with session_scope(foundation_session_factory) as session:
        delivery = await repository.lock(ORG, DELIVERY_A, transaction=session)
        assert delivery is not None and delivery.state == "retryable_failed"
        expected_revision = delivery.revision
        await scheduler.schedule(delivery, transaction=session)
        await scheduler.schedule(delivery, transaction=session)

    async with foundation_session_factory() as session:
        manual_messages = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.organization_id == ORG,
                    OutboxMessage.aggregate_id == DELIVERY_A,
                    OutboxMessage.event_type == DELIVERY_EVENT,
                )
            )
        ).all()
        retries = [
            message
            for message in manual_messages
            if message.payload.get("trigger") == "manual_recovery"
        ]
        assert len(retries) == 1
        assert retries[0].payload["delivery_revision"] == expected_revision


async def test_unknown_delivery_reconciles_found_with_exact_attempt_history(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_parents(foundation_session_factory)
    ids = IDs(21000)
    message_id = await _schedule(foundation_session_factory, ids)
    provider = FixtureDeliveryProvider(
        deliver_outcome="unknown_result",
        reconcile_outcome="reconcile_found_result",
    )
    runtime = _runtime(foundation_session_factory, provider, ids)

    delivered = await DeliveryTaskHandler(runtime, worker_identity="delivery-worker")(
        organization_id=str(ORG),
        message_id=str(message_id),
    )
    assert delivered["state"] == "unknown_outcome"
    async with foundation_session_factory() as session:
        reconciliation = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.event_type == RECONCILIATION_EVENT
            )
        )
        assert reconciliation is not None
        reconciliation_message = reconciliation.message_id

    reconciled = await DeliveryReconciliationTaskHandler(
        runtime,
        worker_identity="delivery-worker",
    )(
        organization_id=str(ORG),
        message_id=str(reconciliation_message),
    )

    assert reconciled["state"] == "succeeded"
    async with foundation_session_factory() as session:
        delivery = await session.get(ExternalDelivery, DELIVERY_A)
        attempts = (
            await session.scalars(
                select(OperationAttempt).order_by(OperationAttempt.attempt_number)
            )
        ).all()
        delivery_attempt = await session.scalar(select(DeliveryAttempt))
        observation = await session.scalar(select(DeliveryReconciliationObservation))
        assert delivery is not None and delivery.state == "succeeded"
        assert delivery.external_id == "external-result-1"
        assert [attempt.outcome for attempt in attempts] == [
            "unknown_outcome",
            "succeeded",
        ]
        assert delivery_attempt is not None
        assert str(STEP_CREDENTIAL) in delivery_attempt.worker_identity
        assert observation is not None and observation.outcome == "succeeded"


class RevokingProvider(FixtureDeliveryProvider):
    def __init__(self, factory: AsyncSessionFactory) -> None:
        super().__init__()
        self._factory = factory

    async def deliver(self, request: ProviderPayload) -> ProviderPayload:
        result = await super().deliver(request)
        async with session_scope(self._factory) as session:
            credential = await session.scalar(
                select(ExternalCredential).where(
                    ExternalCredential.organization_id == ORG,
                    ExternalCredential.id == STEP_CREDENTIAL,
                    ExternalCredential.binding_version == 1,
                )
            )
            assert credential is not None
            credential.status = "revoked"
        return result


class TimeoutProvider:
    contract_version = "1.1.0"
    schema_name = "delivery.schema.json"

    async def deliver(self, request: ProviderPayload) -> ProviderPayload:
        del request
        raise TimeoutError("Bearer timeout-secret after send")

    async def reconcile(self, request: ProviderPayload) -> ProviderPayload:
        del request
        raise AssertionError("timeout delivery must enqueue reconciliation first")


async def test_transport_timeout_becomes_sanitized_unknown_outcome(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_parents(foundation_session_factory)
    ids = IDs(21500)
    message_id = await _schedule(foundation_session_factory, ids)
    provider = TimeoutProvider()
    runtime = DeliveryTaskRuntime(
        session_factory=foundation_session_factory,
        providers={"stepik": provider, "github": provider},
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
        lease_seconds=60,
        retry_seconds=30,
    )

    result = await DeliveryTaskHandler(runtime, worker_identity="delivery-worker")(
        organization_id=str(ORG),
        message_id=str(message_id),
    )

    assert result["state"] == "unknown_outcome"
    async with foundation_session_factory() as session:
        delivery = await session.get(ExternalDelivery, DELIVERY_A)
        followup = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.event_type == RECONCILIATION_EVENT
            )
        )
        assert delivery is not None
        assert delivery.state == "unknown_outcome"
        assert "timeout-secret" not in str(delivery.sanitized_error)
        assert followup is not None


async def test_final_credential_revalidation_rejects_revoked_binding(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    await _seed_parents(foundation_session_factory)
    ids = IDs(22000)
    message_id = await _schedule(foundation_session_factory, ids)
    provider = RevokingProvider(foundation_session_factory)
    runtime = DeliveryTaskRuntime(
        session_factory=foundation_session_factory,
        providers={"stepik": provider, "github": provider},
        id_factory=ids,
        clock=lambda: NOW,
        max_attempts=3,
        lease_seconds=60,
        retry_seconds=30,
    )

    with pytest.raises(DeliveryTaskError, match="exact active"):
        await DeliveryTaskHandler(runtime, worker_identity="delivery-worker")(
            organization_id=str(ORG),
            message_id=str(message_id),
        )

    async with foundation_session_factory() as session:
        delivery = await session.get(ExternalDelivery, DELIVERY_A)
        attempt = await session.scalar(select(DeliveryAttempt))
        assert delivery is not None and delivery.state == "processing"
        assert attempt is not None and attempt.state == "processing"
