from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from tests.support.contracts import load_fixture

from review_platform.application.audit import AuditEvent, AuditRecorder
from review_platform.application.authorization import AuthorizationDenied, Authorizer
from review_platform.application.request_context import (
    AuthVersionSnapshot,
    RequestActor,
)
from review_platform.application.services.ai_review_start import (
    AIComponentCredentialBinding,
    AIReviewCredentialMismatch,
    AIReviewDispatch,
    AIReviewInputSnapshot,
    AIReviewSignedGrantError,
    AIReviewStartArchived,
    AIReviewStartConflict,
    AIReviewStartError,
    AIReviewStartService,
    AIReviewStartStale,
    SignedArtifactGrant,
    StartAIReviewCommand,
)
from review_platform.contracts.ai_review import (
    AIArtifactEnvelope,
    AICriteria,
    AIHomework,
)
from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.db.models.ai_review import AIReviewAttempt, AIReviewRun

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000099")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000000002")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000000003")
ITERATION = UUID("00000000-0000-7000-8000-000000000004")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000000005")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000000006")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000000007")
CRITERION = UUID("00000000-0000-7000-8000-000000000063")
RUN = UUID("00000000-0000-7000-8000-000000000060")
ATTEMPT = UUID("00000000-0000-7000-8000-000000000061")
USER = UUID("00000000-0000-7000-8000-000000000070")
CREDENTIAL_A = UUID("00000000-0000-7000-8000-000000000080")
CREDENTIAL_B = UUID("00000000-0000-7000-8000-000000000081")
HUMAN_REVISION = UUID("00000000-0000-7000-8000-000000000090")
REQUEST_ID = UUID("00000000-0000-7000-8000-000000000091")
TRACE_ID = UUID("00000000-0000-7000-8000-000000000092")
TRANSACTION = object()


class FakeInputRepository:
    def __init__(self, snapshot: AIReviewInputSnapshot | None) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[UUID, UUID, int, object]] = []

    async def lock_input_snapshot(
        self,
        organization_id: UUID,
        review_iteration_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> AIReviewInputSnapshot | None:
        self.calls.append(
            (organization_id, review_iteration_id, expected_revision, transaction)
        )
        return self.snapshot


class FakeRunRepository:
    def __init__(self) -> None:
        self.runs: dict[str, AIReviewRun] = {}
        self.attempts: dict[tuple[UUID, int], AIReviewAttempt] = {}

    async def create_or_get_run(
        self,
        candidate: AIReviewRun,
    ) -> tuple[AIReviewRun, bool]:
        existing = self.runs.get(candidate.input_fingerprint)
        if existing is not None:
            return existing, False
        self.runs[candidate.input_fingerprint] = candidate
        return candidate, True

    async def history(
        self,
        organization_id: UUID,
        run_id: UUID,
    ) -> FakeHistory | None:
        assert organization_id == ORG
        run = next((item for item in self.runs.values() if item.id == run_id), None)
        if run is None:
            return None
        attempts = tuple(
            item
            for (candidate_run_id, _), item in self.attempts.items()
            if candidate_run_id == run_id
        )
        return FakeHistory(run=run, attempts=attempts)

    async def add_attempt(
        self,
        attempt: AIReviewAttempt,
    ) -> AIReviewAttempt:
        key = (attempt.ai_review_run_id, attempt.attempt_number)
        if key in self.attempts:
            raise AIReviewStartConflict("duplicate attempt")
        self.attempts[key] = attempt
        return attempt

    async def compare_and_set_current_attempt(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        expected_revision: int,
        expected_current_attempt_no: int,
        new_attempt_no: int,
    ) -> bool:
        for fingerprint, run in self.runs.items():
            if run.organization_id == organization_id and run.id == run_id:
                if (
                    run.revision != expected_revision
                    or run.current_attempt_no != expected_current_attempt_no
                ):
                    return False
                run.revision += 1
                run.current_attempt_no = new_attempt_no
                run.status = "running"
                self.runs[fingerprint] = run
                return True
        return False

    def set_status(self, fingerprint: str, status: str) -> None:
        current = self.runs[fingerprint]
        current.status = status
        current.revision += 1


@dataclass(frozen=True, slots=True)
class FakeHistory:
    run: AIReviewRun
    attempts: tuple[AIReviewAttempt, ...]


class FakeCredentialRepository:
    def __init__(self, *bindings: AIComponentCredentialBinding) -> None:
        self.bindings = {
            (
                binding.organization_id,
                binding.credential_binding_id,
                binding.credential_binding_version,
            ): binding
            for binding in bindings
        }
        self.return_override: AIComponentCredentialBinding | None = None

    async def require_exact_active(
        self,
        binding: AIComponentCredentialBinding,
        *,
        transaction: object,
    ) -> AIComponentCredentialBinding:
        assert transaction is TRANSACTION
        if self.return_override is not None:
            return self.return_override
        try:
            return self.bindings[
                (
                    binding.organization_id,
                    binding.credential_binding_id,
                    binding.credential_binding_version,
                )
            ]
        except KeyError as error:
            raise AIReviewCredentialMismatch(
                "exact active AI component credential was not found"
            ) from error


class FakeArchivedGuard:
    def __init__(self) -> None:
        self.archived = False

    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        transaction: object,
    ) -> None:
        assert organization_id == ORG
        assert course_run_id == COURSE_RUN
        assert transaction is TRANSACTION
        if self.archived:
            raise AIReviewStartArchived("CourseRun is archived")


class FakeGrantSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, str, int, datetime]] = []
        self.organization_id = ORG
        self.extra_ttl_seconds = 0

    def sign_read(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        object_key: str,
        expires_in_seconds: int,
        now: datetime,
    ) -> SignedArtifactGrant:
        self.calls.append(
            (
                organization_id,
                artifact_version_id,
                object_key,
                expires_in_seconds,
                now,
            )
        )
        return SignedArtifactGrant(
            organization_id=self.organization_id,
            artifact_version_id=artifact_version_id,
            object_key=object_key,
            url=(
                "https://objects.example.test/"
                f"{organization_id}/{artifact_version_id}/artifact.zip?signature=opaque"
            ),
            expires_at=now
            + timedelta(seconds=expires_in_seconds + self.extra_ttl_seconds),
        )


class FakeScheduler:
    def __init__(self) -> None:
        self.dispatches: list[AIReviewDispatch] = []
        self.operations: dict[UUID, str] = {}

    async def schedule(
        self,
        dispatch: AIReviewDispatch,
        *,
        transaction: object,
    ) -> None:
        assert transaction is TRANSACTION
        if any(
            row.run_id == dispatch.run_id
            and row.attempt_number == dispatch.attempt_number
            for row in self.dispatches
        ):
            raise AIReviewStartConflict("duplicate AIReviewRequested")
        existing = self.operations.get(dispatch.operation_id)
        if existing is not None and existing != dispatch.input_version:
            raise AIReviewStartConflict("Operation input provenance changed")
        self.operations[dispatch.operation_id] = dispatch.input_version
        self.dispatches.append(dispatch)


class FakeAuditRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def append(self, event: AuditEvent, *, transaction: object) -> None:
        assert transaction is TRANSACTION
        self.events.append(event)


class FakeAuthGuard:
    def __init__(self, actor: RequestActor) -> None:
        self.actor = actor
        self.early_calls = 0
        self.commit_calls = 0

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        self.early_calls += 1
        return self._snapshot(actor)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert transaction is TRANSACTION
        self.commit_calls += 1
        return self._snapshot(actor)

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
            agent_authorization_id=actor.agent_authorization_id,
            agent_authorization_revision=actor.agent_authorization_revision,
            agent_scopes=actor.scopes,
            agent_expires_at=actor.expires_at,
            agent_active=True,
        )


def _snapshot(**changes: Any) -> AIReviewInputSnapshot:
    vector = load_fixture("ai-fingerprint-v1.1.0.json")
    immutable = vector["input"]
    artifact = AIArtifactEnvelope.model_validate(
        {
            "contract_version": "1.1.0",
            "organization_id": immutable["organization_id"],
            "artifact_reference_id": "00000000-0000-7000-8000-000000000008",
            "artifact_version_id": immutable["artifact_version_id"],
            "provider": "github",
            "provider_version": "commit:" + "a" * 40,
            "content_digest": immutable["artifact_content_digest"],
            "captured_at": "2026-09-05T11:55:00Z",
            "object": {
                "key": f"{ORG}/{ARTIFACT_VERSION}/artifact.zip",
                "media_type": "application/zip",
                "byte_size": 128,
            },
            "metadata": {"source": "offline-fixture"},
        }
    )
    homework = AIHomework.model_validate(
        {
            "version_id": immutable["homework_version_id"],
            "title": "Contract homework",
            "student_text": "Submit the solution",
            "digest": immutable["homework_digest"],
        }
    )
    criteria = AICriteria.model_validate(
        {
            "set_id": immutable["criterion_set_id"],
            "digest": immutable["criterion_set_digest"],
            "items": [
                {
                    "id": CRITERION,
                    "key": "correctness",
                    "title": "Correctness",
                    "description": "The solution is correct",
                    "max_points": 5,
                }
            ],
        }
    )
    values: dict[str, Any] = {
        "organization_id": ORG,
        "review_iteration_id": ITERATION,
        "review_iteration_revision": 3,
        "is_current": True,
        "course_run_id": COURSE_RUN,
        "submission_version_id": SUBMISSION_VERSION,
        "artifact": artifact,
        "homework": homework,
        "criteria": criteria,
        "current_human_revision_id": HUMAN_REVISION,
    }
    values.update(changes)
    return AIReviewInputSnapshot(**values)


def _binding(identity: UUID = CREDENTIAL_A, version: int = 1) -> AIComponentCredentialBinding:
    return AIComponentCredentialBinding(
        organization_id=ORG,
        credential_binding_id=identity,
        credential_binding_version=version,
    )


def _actor(*, role: str = "reviewer") -> RequestActor:
    return RequestActor.user(
        organization_id=ORG,
        user_id=USER,
        roles=[role],  # type: ignore[list-item]
        membership_revision=4,
        auth_epoch=2,
    )


def _command() -> StartAIReviewCommand:
    return StartAIReviewCommand(
        organization_id=ORG,
        review_iteration_id=ITERATION,
        expected_review_iteration_revision=3,
        request_id=REQUEST_ID,
        trace_id=TRACE_ID,
    )


def _ids() -> Callable[[], UUID]:
    values = iter(
        [
            RUN,
            ATTEMPT,
            UUID("00000000-0000-7000-8000-000000000062"),
            UUID("00000000-0000-7000-8000-000000000064"),
            UUID("00000000-0000-7000-8000-000000000065"),
        ]
    )
    return lambda: next(values)


def _service(
    *,
    snapshot: AIReviewInputSnapshot | None = None,
    bindings: tuple[AIComponentCredentialBinding, ...] | None = None,
    actor: RequestActor | None = None,
) -> tuple[
    AIReviewStartService,
    FakeRunRepository,
    FakeCredentialRepository,
    FakeArchivedGuard,
    FakeGrantSigner,
    FakeScheduler,
    FakeAuditRepository,
    FakeAuthGuard,
]:
    selected_actor = actor or _actor()
    input_repository = FakeInputRepository(snapshot or _snapshot())
    run_repository = FakeRunRepository()
    selected_bindings = bindings or (_binding(),)
    credential_repository = FakeCredentialRepository(*selected_bindings)
    archived_guard = FakeArchivedGuard()
    signer = FakeGrantSigner()
    scheduler = FakeScheduler()
    audit_repository = FakeAuditRepository()
    auth_guard = FakeAuthGuard(selected_actor)
    service = AIReviewStartService(
        input_repository=input_repository,
        run_repository=run_repository,
        credential_repository=credential_repository,
        archived_guard=archived_guard,
        grant_signer=signer,
        scheduler=scheduler,
        authorizer=Authorizer(auth_guard, clock=lambda: NOW),
        audit=AuditRecorder(
            audit_repository,
            event_id_factory=lambda: UUID(
                "00000000-0000-7000-8000-000000000099"
            ),
            clock=lambda: NOW,
        ),
        signed_url_ttl_seconds=300,
        id_factory=_ids(),
        clock=lambda: NOW,
    )
    return (
        service,
        run_repository,
        credential_repository,
        archived_guard,
        signer,
        scheduler,
        audit_repository,
        auth_guard,
    )


@pytest.mark.anyio
async def test_start_builds_golden_request_operation_and_atomic_outbox_intent() -> None:
    service, runs, _, _, signer, scheduler, audit, auth = _service()

    result = await service.start(
        _command(),
        actor=_actor(),
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )

    expected = load_fixture("ai-fingerprint-v1.1.0.json")["expected_fingerprint"]
    assert result.input_fingerprint == expected
    assert result.run_id == result.operation_id == RUN
    assert result.attempt_id == ATTEMPT
    assert result.attempt_number == 1
    assert result.status == "running"
    assert not result.replayed
    assert result.request is not None
    assert len(runs.runs) == len(runs.attempts) == 1
    assert len(scheduler.operations) == len(scheduler.dispatches) == 1
    dispatch = scheduler.dispatches[0]
    assert dispatch.event_type == "AIReviewRequested"
    assert dispatch.payload_version == "1.1.0"
    assert dispatch.operation_id == dispatch.run_id == RUN
    assert dispatch.attempt_id == ATTEMPT
    assert dispatch.input_version == f"ai-review:1.1.0:{expected}"
    ContractRegistry().validate_pydantic(
        dispatch.request,
        "ai-review.schema.json",
        definition="request",
    )
    request = dispatch.request.model_dump(mode="json")
    assert request["credential_binding_id"] == str(CREDENTIAL_A)
    assert request["credential_binding_version"] == 1
    assert request["artifact"]["artifact_version_id"] == str(ARTIFACT_VERSION)
    assert request["homework"]["version_id"] == str(HOMEWORK_VERSION)
    assert request["criteria"]["set_id"] == str(CRITERION_SET)
    assert str(ORG) in request["artifact_download"]["url"]
    assert str(ARTIFACT_VERSION) in request["artifact_download"]["url"]
    assert signer.calls[0][3] == 300
    assert result.request.artifact_download.expires_at == NOW + timedelta(seconds=300)
    assert len(audit.events) == 1
    assert "signed_url" not in audit.events[0].sanitized_details
    assert (audit.events[0].before_revision, audit.events[0].after_revision) == (0, 1)
    assert auth.early_calls == auth.commit_calls == 1


@pytest.mark.anyio
async def test_same_input_replays_current_run_without_duplicate_attempt_or_outbox() -> None:
    service, runs, _, _, _, scheduler, audit, auth = _service()
    first = await service.start(
        _command(),
        actor=_actor(),
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )
    second = await service.start(
        _command(),
        actor=_actor(),
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )

    assert second.replayed
    assert second.run_id == first.run_id
    assert second.operation_id == first.operation_id
    assert second.attempt_id == first.attempt_id
    assert second.request is None
    assert len(runs.runs) == len(runs.attempts) == 1
    assert len(scheduler.dispatches) == len(audit.events) == 1
    assert auth.early_calls == auth.commit_calls == 2


@pytest.mark.anyio
async def test_controlled_retry_can_use_a_second_explicit_exact_binding() -> None:
    binding_b = _binding(CREDENTIAL_B, version=4)
    service, runs, _, _, _, scheduler, audit, _ = _service(
        bindings=(_binding(), binding_b)
    )
    first = await service.start(
        _command(),
        actor=_actor(),
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )
    runs.set_status(first.input_fingerprint, "retryable_failed")

    retry = await service.start(
        _command(),
        actor=_actor(),
        credential_binding=binding_b,
        transaction=TRANSACTION,
    )

    assert retry.run_id == first.run_id
    assert retry.operation_id == first.operation_id
    assert retry.attempt_number == 2
    assert retry.credential_binding_id == CREDENTIAL_B
    assert retry.credential_binding_version == 4
    assert retry.request is not None
    assert retry.request.credential_binding_id == CREDENTIAL_B
    assert len(runs.runs) == 1
    assert len(runs.attempts) == len(scheduler.dispatches) == len(audit.events) == 2
    assert (audit.events[1].before_revision, audit.events[1].after_revision) == (2, 3)


@pytest.mark.anyio
async def test_active_run_never_silently_switches_between_two_bindings() -> None:
    binding_b = _binding(CREDENTIAL_B, version=1)
    service, runs, _, _, _, scheduler, _, _ = _service(
        bindings=(_binding(), binding_b)
    )
    await service.start(
        _command(),
        actor=_actor(),
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )

    with pytest.raises(AIReviewCredentialMismatch, match="different exact"):
        await service.start(
            _command(),
            actor=_actor(),
            credential_binding=binding_b,
            transaction=TRANSACTION,
        )
    assert len(runs.attempts) == len(scheduler.dispatches) == 1


@pytest.mark.anyio
async def test_exact_credential_lookup_rejects_missing_or_substituted_binding() -> None:
    service, _, credentials, _, _, scheduler, _, _ = _service()
    with pytest.raises(AIReviewCredentialMismatch, match="not found"):
        await service.start(
            _command(),
            actor=_actor(),
            credential_binding=_binding(CREDENTIAL_A, version=2),
            transaction=TRANSACTION,
        )
    credentials.return_override = _binding(CREDENTIAL_B, version=1)
    with pytest.raises(AIReviewCredentialMismatch, match="exact requested"):
        await service.start(
            _command(),
            actor=_actor(),
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )
    assert not scheduler.dispatches


@pytest.mark.anyio
@pytest.mark.parametrize("role", ["reviewer", "methodologist"])
async def test_reviewer_and_methodologist_are_authorized(role: str) -> None:
    actor = _actor(role=role)
    service, *_ = _service(actor=actor)
    result = await service.start(
        _command(),
        actor=actor,
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )
    assert result.status == "running"


@pytest.mark.anyio
async def test_agent_requires_exact_ai_start_scope() -> None:
    allowed = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles=["reviewer"],
        membership_revision=4,
        auth_epoch=2,
        agent_id=UUID("00000000-0000-7000-8000-000000000071"),
        agent_authorization_id=UUID("00000000-0000-7000-8000-000000000072"),
        agent_authorization_revision=1,
        scopes=["ai_reviews:start"],
        expires_at=NOW + timedelta(hours=1),
    )
    service, *_ = _service(actor=allowed)
    assert (
        await service.start(
            _command(),
            actor=allowed,
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )
    ).status == "running"

    denied = replace(allowed, scopes=frozenset({"reviews:read"}))
    denied_service, *_ = _service(actor=denied)
    with pytest.raises(AuthorizationDenied, match="scope"):
        await denied_service.start(
            _command(),
            actor=denied,
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )


@pytest.mark.anyio
async def test_student_stale_archived_and_cross_tenant_inputs_fail_closed() -> None:
    student = _actor(role="student")
    student_service, *_ = _service(actor=student)
    with pytest.raises(AuthorizationDenied, match="role"):
        await student_service.start(
            _command(),
            actor=student,
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )

    stale_service, *_ = _service(
        snapshot=_snapshot(review_iteration_revision=4)
    )
    with pytest.raises(AIReviewStartStale, match="revision"):
        await stale_service.start(
            _command(),
            actor=_actor(),
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )

    archived_service, _, _, archived, _, _, _, _ = _service()
    archived.archived = True
    with pytest.raises(AIReviewStartArchived):
        await archived_service.start(
            _command(),
            actor=_actor(),
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )

    cross_tenant_service, *_ = _service(
        snapshot=_snapshot(organization_id=OTHER_ORG)
    )
    with pytest.raises(AIReviewStartError):
        await cross_tenant_service.start(
            _command(),
            actor=_actor(),
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["tenant", "expiry"])
async def test_signed_grant_must_be_tenant_scoped_and_within_configured_ttl(
    failure: str,
) -> None:
    service, _, _, _, signer, scheduler, audit, _ = _service()
    if failure == "tenant":
        signer.organization_id = OTHER_ORG
    else:
        signer.extra_ttl_seconds = 1

    with pytest.raises(AIReviewSignedGrantError):
        await service.start(
            _command(),
            actor=_actor(),
            credential_binding=_binding(),
            transaction=TRANSACTION,
        )
    assert not scheduler.dispatches
    assert not audit.events


@pytest.mark.anyio
async def test_start_never_mutates_existing_human_revision_pointer() -> None:
    snapshot = _snapshot()
    service, *_ = _service(snapshot=snapshot)
    before = snapshot.current_human_revision_id

    await service.start(
        _command(),
        actor=_actor(),
        credential_binding=_binding(),
        transaction=TRANSACTION,
    )

    assert snapshot.current_human_revision_id == before == HUMAN_REVISION
