from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import anyio
import pytest

from review_platform.application.audit import AuditEvent, AuditRecorder
from review_platform.application.authorization import AuthorizationDenied, Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.publication_requests import PublicationRequestRecord
from review_platform.application.services.review_publication import (
    DestinationSnapshot,
    ExistingReviewPublication,
    ExternalDeliveryIntent,
    PublicationCriterion,
    PublishReviewCommand,
    ReviewPublicationArchived,
    ReviewPublicationConflict,
    ReviewPublicationContext,
    ReviewPublicationRecord,
    ReviewPublicationService,
)
from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.db.repositories.review_revisions import (
    ReviewDecisionRecord,
    ReviewNoteRecord,
    ReviewRevisionRecord,
)

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000000001")
REVIEWER = UUID("00000000-0000-7000-8000-000000015001")
CASE = UUID("00000000-0000-7000-8000-000000015002")
ITERATION = UUID("00000000-0000-7000-8000-000000015003")
REVISION = UUID("00000000-0000-7000-8000-000000015004")
COURSE = UUID("00000000-0000-7000-8000-000000015005")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000015006")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000015007")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000015008")
CRITERION_A = UUID("00000000-0000-7000-8000-000000015009")
CRITERION_B = UUID("00000000-0000-7000-8000-000000015010")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000015011")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000015012")
BINDING_A = UUID("00000000-0000-7000-8000-000000015013")
BINDING_B = UUID("00000000-0000-7000-8000-000000015014")
CREDENTIAL_A = UUID("00000000-0000-7000-8000-000000015015")
CREDENTIAL_B = UUID("00000000-0000-7000-8000-000000015016")
PUBLICATION_REQUEST = UUID("00000000-0000-7000-8000-000000015017")
REQUEST_ID = UUID("00000000-0000-7000-8000-000000015018")
TRACE_ID = UUID("00000000-0000-7000-8000-000000015019")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


class IDs:
    def __init__(self, value: int = 16000) -> None:
        self.value = value

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


class Transaction:
    def __init__(self, name: str) -> None:
        self.name = name
        self.publication: ReviewPublicationRecord | None = None
        self.intents: list[ExternalDeliveryIntent] = []
        self.confirmed_request = False


class Guard:
    def __init__(self) -> None:
        self.early = 0
        self.final = 0

    @staticmethod
    def _snapshot(actor: RequestActor) -> AuthVersionSnapshot:
        assert actor.user_id is not None
        assert actor.membership_revision is not None
        assert actor.auth_epoch is not None
        return AuthVersionSnapshot(
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            roles=actor.roles,
            membership_revision=actor.membership_revision,
            auth_epoch=actor.auth_epoch,
            active=True,
        )

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.early += 1
        return self._snapshot(actor)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert isinstance(transaction, Transaction)
        self.final += 1
        return self._snapshot(actor)


class AuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        assert isinstance(transaction, Transaction)
        self.events.append(event)


class Scheduler:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.calls = 0

    async def schedule(
        self,
        intent: ExternalDeliveryIntent,
        *,
        transaction: object,
    ) -> None:
        assert isinstance(transaction, Transaction)
        self.calls += 1
        if self.fail_at == self.calls:
            raise RuntimeError("injected delivery scheduler failure")
        transaction.intents.append(intent)


class Repository:
    def __init__(self, context: ReviewPublicationContext) -> None:
        self.context = context
        self.existing: ExistingReviewPublication | None = None
        self.publication_request = _publication_request()
        self.committed: list[ExistingReviewPublication] = []
        self.current_iteration_revision = 3
        self.race_barrier = False
        self.archive_on_cas = False
        self._find_calls = 0
        self._both_find = anyio.Event()
        self._cas_lock = anyio.Lock()

    async def find_existing_publication(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        review_revision_id: UUID,
        *,
        transaction: object,
    ) -> ExistingReviewPublication | None:
        assert organization_id == ORG
        assert review_iteration_id == ITERATION and review_revision_id == REVISION
        assert isinstance(transaction, Transaction)
        if self.race_barrier and self._find_calls < 2:
            self._find_calls += 1
            if self._find_calls == 2:
                self._both_find.set()
            await self._both_find.wait()
            return None
        return self.existing

    async def lock_publication_context(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID,
        transaction: object,
    ) -> ReviewPublicationContext | None:
        assert organization_id == ORG and review_iteration_id == ITERATION
        assert expected_current_revision_id == REVISION
        assert isinstance(transaction, Transaction)
        if expected_iteration_revision != self.current_iteration_revision:
            return None
        return self.context

    async def next_publication_version(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        transaction: object,
    ) -> int:
        assert organization_id == ORG and review_iteration_id == ITERATION
        assert isinstance(transaction, Transaction)
        return 1

    async def reserve_publication(
        self,
        publication: ReviewPublicationRecord,
        *,
        transaction: object,
    ) -> tuple[ExistingReviewPublication, bool]:
        assert isinstance(transaction, Transaction)
        if self.existing is not None:
            return self.existing, False
        transaction.publication = publication
        return ExistingReviewPublication(publication, 4, ()), True

    async def lock_publication_request(
        self,
        organization_id: UUID,
        publication_request_id: UUID,
        *,
        transaction: object,
    ) -> PublicationRequestRecord | None:
        assert organization_id == ORG and publication_request_id == PUBLICATION_REQUEST
        assert isinstance(transaction, Transaction)
        return self.publication_request

    async def confirm_publication_request(
        self,
        organization_id: UUID,
        publication_request_id: UUID,
        *,
        expected_revision: int,
        confirmed_by: UUID,
        confirmed_at: datetime,
        transaction: object,
    ) -> bool:
        assert organization_id == ORG and publication_request_id == PUBLICATION_REQUEST
        assert expected_revision == 0 and confirmed_by == REVIEWER and confirmed_at == NOW
        assert isinstance(transaction, Transaction)
        transaction.confirmed_request = True
        return True

    async def compare_and_set_published(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_iteration_revision: int,
        expected_current_revision_id: UUID,
        transaction: object,
    ) -> bool:
        assert organization_id == ORG and review_iteration_id == ITERATION
        assert expected_current_revision_id == REVISION
        assert isinstance(transaction, Transaction)
        async with self._cas_lock:
            if (
                self.archive_on_cas
                or self.current_iteration_revision != expected_iteration_revision
            ):
                return False
            assert transaction.publication is not None
            self.current_iteration_revision += 1
            stored = ExistingReviewPublication(
                transaction.publication,
                expected_iteration_revision + 1,
                tuple(transaction.intents),
            )
            self.existing = stored
            self.committed.append(stored)
            return True


def _revision() -> ReviewRevisionRecord:
    return ReviewRevisionRecord(
        organization_id=ORG,
        review_revision_id=REVISION,
        review_iteration_id=ITERATION,
        revision_number=2,
        author_user_id=REVIEWER,
        base_revision_id=None,
        feedback="Complete human feedback",
        total_score=Decimal("8"),
        created_at=NOW - timedelta(minutes=1),
        decisions=(
            ReviewDecisionRecord(
                UUID("00000000-0000-7000-8000-000000015101"),
                CRITERION_A,
                "correctness",
                0,
                Decimal("5"),
                "manual",
                "Correct result",
                (),
                None,
            ),
            ReviewDecisionRecord(
                UUID("00000000-0000-7000-8000-000000015102"),
                CRITERION_B,
                "quality",
                1,
                Decimal("3"),
                "changed",
                "Clear enough",
                (),
                None,
            ),
        ),
        notes=(
            ReviewNoteRecord(
                UUID("00000000-0000-7000-8000-000000015103"),
                None,
                "Global note",
                REVIEWER,
                0,
            ),
        ),
    )


def _context() -> ReviewPublicationContext:
    return ReviewPublicationContext(
        organization_id=ORG,
        review_case_id=CASE,
        current_iteration_id=ITERATION,
        review_iteration_id=ITERATION,
        iteration_revision=3,
        current_review_revision_id=REVISION,
        iteration_status="ready_to_publish",
        course_id=COURSE,
        course_status="active",
        course_run_id=COURSE_RUN,
        course_run_status="active",
        homework_version_id=HOMEWORK_VERSION,
        homework_max_score=Decimal("10"),
        criterion_set_id=CRITERION_SET,
        submission_version_id=SUBMISSION_VERSION,
        artifact_version_id=ARTIFACT_VERSION,
        artifact_content_digest=DIGEST,
        current_revision=_revision(),
        criteria=(
            PublicationCriterion(CRITERION_A, 0, Decimal("5")),
            PublicationCriterion(CRITERION_B, 1, Decimal("5")),
        ),
        destinations=(
            DestinationSnapshot(
                ORG,
                COURSE_RUN,
                BINDING_A,
                1,
                CREDENTIAL_A,
                2,
                "stepik",
                "stepik-submission-42",
                True,
                "active",
            ),
            DestinationSnapshot(
                ORG,
                COURSE_RUN,
                BINDING_B,
                3,
                CREDENTIAL_B,
                4,
                "github",
                "example/repository#review",
                True,
                "active",
            ),
        ),
    )


def _publication_request() -> PublicationRequestRecord:
    return PublicationRequestRecord(
        organization_id=ORG,
        publication_request_id=PUBLICATION_REQUEST,
        review_iteration_id=ITERATION,
        review_revision_id=REVISION,
        requested_by_user_id=REVIEWER,
        agent_id=UUID("00000000-0000-7000-8000-000000015104"),
        agent_authorization_id=UUID("00000000-0000-7000-8000-000000015105"),
        idempotency_key="agent-publication-request-0001",
        status="pending",
        expires_at=NOW + timedelta(hours=1),
        revision=0,
    )


def _actor() -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
    )


def _agent() -> RequestActor:
    return RequestActor.agent(
        organization_id=ORG,
        user_id=REVIEWER,
        roles={"reviewer"},
        membership_revision=0,
        auth_epoch=0,
        agent_id=UUID("00000000-0000-7000-8000-000000015106"),
        agent_authorization_id=UUID("00000000-0000-7000-8000-000000015107"),
        agent_authorization_revision=0,
        scopes={"publication_requests:write"},
    )


def _command(*, request: bool = True) -> PublishReviewCommand:
    return PublishReviewCommand(
        organization_id=ORG,
        review_iteration_id=ITERATION,
        expected_iteration_revision=3,
        expected_current_revision_id=REVISION,
        publication_request_id=PUBLICATION_REQUEST if request else None,
        request_id=REQUEST_ID,
        trace_id=TRACE_ID,
    )


def _service(
    repository: Repository,
    scheduler: Scheduler,
    ids: IDs,
) -> tuple[ReviewPublicationService, Guard, AuditRepository]:
    guard = Guard()
    audit = AuditRepository()
    return (
        ReviewPublicationService(
            repository=repository,
            scheduler=scheduler,
            authorizer=Authorizer(guard, clock=lambda: NOW),
            audit=AuditRecorder(
                audit,
                event_id_factory=ids,
                clock=lambda: NOW,
            ),
            id_factory=ids,
            clock=lambda: NOW,
        ),
        guard,
        audit,
    )


async def test_human_publication_snapshots_all_required_destinations_atomically() -> None:
    context = _context()
    revision_bytes = asdict(context.current_revision)
    repository = Repository(context)
    scheduler = Scheduler()
    service, guard, audit = _service(repository, scheduler, IDs())
    transaction = Transaction("publish")

    result = await service.publish(_command(), actor=_actor(), transaction=transaction)

    assert len(result.delivery_ids) == len(result.operation_ids) == 2
    assert len(set(result.delivery_ids)) == len(set(result.operation_ids)) == 2
    assert transaction.confirmed_request
    assert len(repository.committed) == 1
    intents = repository.committed[0].deliveries
    assert [intent.destination.destination_binding_id for intent in intents] == [
        BINDING_A,
        BINDING_B,
    ]
    assert len({intent.delivery_key for intent in intents}) == 2
    assert len({intent.publication_fingerprint for intent in intents}) == 1
    assert len({intent.payload_digest for intent in intents}) == 1
    for intent in intents:
        ContractRegistry().validate(
            intent.request,
            "delivery.schema.json",
            definition="deliver_request",
        )
        assert intent.operation_id in result.operation_ids
        assert intent.destination.credential_binding_version in {2, 4}
    assert asdict(context.current_revision) == revision_bytes
    assert guard.early == guard.final == 1
    assert len(audit.events) == 1
    assert audit.events[0].sanitized_details["destination_count"] == 2


async def test_agent_is_forbidden_and_archive_races_roll_back_bundle() -> None:
    repository = Repository(_context())
    service, guard, audit = _service(repository, Scheduler(), IDs(17000))
    with pytest.raises(AuthorizationDenied):
        await service.publish(
            _command(),
            actor=_agent(),
            transaction=Transaction("agent"),
        )
    assert guard.early == 0
    assert repository.committed == []

    archived = replace(_context(), course_run_status="archived")
    repository = Repository(archived)
    service, _, _ = _service(repository, Scheduler(), IDs(18000))
    with pytest.raises(ReviewPublicationArchived):
        await service.publish(
            _command(),
            actor=_actor(),
            transaction=Transaction("archived"),
        )
    assert repository.committed == []

    repository = Repository(_context())
    repository.archive_on_cas = True
    transaction = Transaction("archive-race")
    service, _, race_audit = _service(repository, Scheduler(), IDs(19000))
    with pytest.raises(ReviewPublicationConflict, match="publication CAS"):
        await service.publish(_command(), actor=_actor(), transaction=transaction)
    assert repository.committed == []
    assert race_audit.events == []


async def test_scheduler_failure_leaves_no_publication_delivery_request_or_audit() -> None:
    repository = Repository(_context())
    scheduler = Scheduler(fail_at=2)
    transaction = Transaction("scheduler-failure")
    service, _, audit = _service(repository, scheduler, IDs(20000))

    with pytest.raises(RuntimeError, match="injected"):
        await service.publish(_command(), actor=_actor(), transaction=transaction)

    assert repository.committed == []
    assert not transaction.confirmed_request
    assert audit.events == []


async def test_sequential_replay_returns_exact_publication_without_rescheduling() -> None:
    repository = Repository(_context())
    scheduler = Scheduler()
    service, guard, audit = _service(repository, scheduler, IDs(21000))
    first = await service.publish(
        _command(request=False),
        actor=_actor(),
        transaction=Transaction("first"),
    )

    replay = await service.publish(
        _command(request=False),
        actor=_actor(),
        transaction=Transaction("replay"),
    )

    assert replay.replayed
    assert replay.publication_id == first.publication_id
    assert replay.delivery_ids == first.delivery_ids
    assert scheduler.calls == 2
    assert len(repository.committed) == len(audit.events) == 1
    assert guard.early == guard.final == 2


async def test_concurrent_publish_has_one_cas_winner_and_distinct_operations() -> None:
    repository = Repository(_context())
    repository.race_barrier = True
    scheduler = Scheduler()
    service, _, audit = _service(repository, scheduler, IDs(22000))
    results: list[Any] = []
    conflicts: list[ReviewPublicationConflict] = []

    async def publish(name: str) -> None:
        try:
            results.append(
                await service.publish(
                    _command(request=False),
                    actor=_actor(),
                    transaction=Transaction(name),
                )
            )
        except ReviewPublicationConflict as error:
            conflicts.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(publish, "a")
        tasks.start_soon(publish, "b")

    assert len(results) == len(conflicts) == 1
    assert len(repository.committed) == len(audit.events) == 1
    intents = repository.committed[0].deliveries
    assert len({intent.operation_id for intent in intents}) == len(intents) == 2
