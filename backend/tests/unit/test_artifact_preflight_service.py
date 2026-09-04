"""Focused service tests for CourseRunHomework-addressed artifact preflight."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from review_platform.application.authorization import Authorizer
from review_platform.application.ports.providers import ArtifactProvider, ProviderPayload
from review_platform.application.request_context import (
    AuthVersionSnapshot,
    RequestActor,
)
from review_platform.application.services.artifact_preflight import (
    ArchivedPreflightDenied,
    ArtifactCredentialBinding,
    ArtifactKindNotAllowed,
    ArtifactPreflightService,
    ArtifactProviderContractViolation,
    ArtifactProviderName,
    ArtifactReferenceRecord,
    CourseRunHomeworkPreflightContext,
    HomeworkNotPublished,
    InvalidArtifactCredentialBinding,
    PreflightTargetNotFound,
    StudentNotEnrolled,
    SubmissionRecord,
    provider_for_artifact_url,
)
from review_platform.contracts.registry import ContractRegistry
from review_platform.infrastructure.providers.mocks import FixtureArtifactProvider

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
STUDENT = UUID("00000000-0000-7000-8000-000000000101")
RELATION_A = UUID("00000000-0000-7000-8000-000000000102")
RELATION_B = UUID("00000000-0000-7000-8000-000000000103")
RUN_A = UUID("00000000-0000-7000-8000-000000000104")
RUN_B = UUID("00000000-0000-7000-8000-000000000105")
HOMEWORK = UUID("00000000-0000-7000-8000-000000000106")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000000107")
PUBLICATION_A = UUID("00000000-0000-7000-8000-000000000108")
PUBLICATION_B = UUID("00000000-0000-7000-8000-000000000109")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000000021")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
URL = "https://github.com/example/repository"


class IdFactory:
    def __init__(self) -> None:
        self._next = 200

    def __call__(self) -> UUID:
        value = UUID(f"00000000-0000-7000-8000-{self._next:012d}")
        self._next += 1
        return value


class Guard:
    def __init__(self, actor: RequestActor) -> None:
        self.actor = actor
        self.revalidated = 0

    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        assert actor == self.actor
        return _snapshot(actor)

    async def lock_and_revalidate(
        self,
        *,
        actor: RequestActor,
        transaction: object,
    ) -> AuthVersionSnapshot:
        assert actor == self.actor
        assert transaction is TRANSACTION
        self.revalidated += 1
        return _snapshot(actor)


class Repository:
    def __init__(self, contexts: tuple[CourseRunHomeworkPreflightContext, ...]) -> None:
        self.contexts = {
            (context.organization_id, context.course_run_homework_id): context
            for context in contexts
        }
        self.submissions: dict[tuple[UUID, UUID, UUID, UUID], SubmissionRecord] = {}
        self.references: dict[tuple[UUID, str, str], ArtifactReferenceRecord] = {}

    async def lock_context(
        self,
        organization_id: UUID,
        course_run_homework_id: UUID,
        student_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> CourseRunHomeworkPreflightContext | None:
        assert transaction is TRANSACTION
        context = self.contexts.get((organization_id, course_run_homework_id))
        if context is None or context.revision != expected_revision:
            return None
        return context

    async def get_or_create_submission(
        self,
        context: CourseRunHomeworkPreflightContext,
        *,
        student_id: UUID,
        new_submission_id: UUID,
        transaction: object,
    ) -> SubmissionRecord:
        assert transaction is TRANSACTION
        key = (
            context.organization_id,
            context.course_run_id,
            context.homework_id,
            student_id,
        )
        existing = self.submissions.get(key)
        if existing is not None:
            return existing
        created = SubmissionRecord(
            organization_id=context.organization_id,
            submission_id=new_submission_id,
            course_run_homework_id=context.course_run_homework_id,
            course_run_id=context.course_run_id,
            homework_id=context.homework_id,
            student_id=student_id,
            current_revision=0,
        )
        self.submissions[key] = created
        return created

    async def upsert_available_reference(
        self,
        candidate: ArtifactReferenceRecord,
        *,
        transaction: object,
    ) -> ArtifactReferenceRecord:
        assert transaction is TRANSACTION
        key = (candidate.organization_id, candidate.provider, candidate.original_url)
        existing = self.references.get(key)
        if existing is not None:
            updated = replace(
                candidate,
                artifact_reference_id=existing.artifact_reference_id,
                revision=existing.revision + 1,
            )
            self.references[key] = updated
            return updated
        self.references[key] = candidate
        return candidate


class Credentials:
    def __init__(self) -> None:
        self.calls = 0

    async def require_exact_active(
        self,
        organization_id: UUID,
        binding: ArtifactCredentialBinding,
        *,
        transaction: object,
    ) -> None:
        assert transaction is TRANSACTION
        self.calls += 1
        if (
            organization_id != ORG
            or binding.credential_binding_id != CREDENTIAL
            or binding.credential_binding_version != 1
            or binding.provider != "github"
        ):
            raise InvalidArtifactCredentialBinding("exact binding mismatch")


class ArchiveGuard:
    def __init__(self, *, archived: bool = False) -> None:
        self.archived = archived
        self.calls: list[UUID] = []

    async def require_active(
        self,
        *,
        organization_id: UUID,
        course_run_id: UUID,
        transaction: object,
    ) -> None:
        assert organization_id == ORG
        assert transaction is TRANSACTION
        self.calls.append(course_run_id)
        if self.archived:
            raise ArchivedPreflightDenied("CourseRun is archived")


class CountingProvider:
    def __init__(self, delegate: ArtifactProvider) -> None:
        self.delegate = delegate
        self.calls = 0

    @property
    def contract_version(self) -> str:
        return self.delegate.contract_version

    @property
    def schema_name(self) -> str:
        return self.delegate.schema_name

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        self.calls += 1
        return await self.delegate.preflight(request)

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        return await self.delegate.capture(request)


class UnavailableProvider:
    contract_version = "1.1.0"
    schema_name = "artifact-provider.schema.json"

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        ContractRegistry().validate(
            request,
            self.schema_name,
            definition="preflight_request",
        )
        return {
            "contract_version": "1.1.0",
            "organization_id": request["organization_id"],
            "provider": request["provider"],
            "read_capability": "requires_action",
            "feedback_capability": "requires_action",
            "locator": None,
            "error": {
                "code": "access_denied",
                "message": "Artifact is unavailable",
                "retryable": False,
                "action": "grant_read_access",
            },
        }

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        raise AssertionError("preflight must not capture")


class WrongTenantProvider(UnavailableProvider):
    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        result = dict(await super().preflight(request))
        result["organization_id"] = str(OTHER_ORG)
        return cast(ProviderPayload, result)


TRANSACTION = object()


def _actor(*, organization_id: UUID = ORG) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=STUDENT,
        roles={"student"},
        membership_revision=2,
        auth_epoch=3,
    )


def _snapshot(actor: RequestActor) -> AuthVersionSnapshot:
    assert actor.user_id is not None
    return AuthVersionSnapshot(
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        roles=actor.roles,
        membership_revision=cast(int, actor.membership_revision),
        auth_epoch=cast(int, actor.auth_epoch),
        active=True,
    )


def _context(
    relation_id: UUID,
    run_id: UUID,
    publication_id: UUID,
    *,
    organization_id: UUID = ORG,
    allowed: tuple[str, ...] = ("github",),
    status: str = "active",
    enrolled: bool = True,
) -> CourseRunHomeworkPreflightContext:
    return CourseRunHomeworkPreflightContext(
        organization_id=organization_id,
        course_run_homework_id=relation_id,
        course_run_id=run_id,
        homework_id=HOMEWORK,
        current_publication_id=publication_id,
        current_homework_version_id=HOMEWORK_VERSION,
        allowed_artifact_kinds=cast(tuple[ArtifactProviderName, ...], allowed),
        revision=1,
        status=status,
        student_enrolled=enrolled,
    )


def _service(
    repository: Repository,
    *,
    actor: RequestActor,
    provider: ArtifactProvider | None = None,
    credentials: Credentials | None = None,
    archive: ArchiveGuard | None = None,
) -> tuple[ArtifactPreflightService, Guard, Credentials, ArchiveGuard]:
    guard = Guard(actor)
    credential_port = credentials or Credentials()
    archive_guard = archive or ArchiveGuard()
    return (
        ArtifactPreflightService(
            repository=repository,
            credential_bindings=credential_port,
            provider=provider or FixtureArtifactProvider(),
            archived_guard=archive_guard,
            authorizer=Authorizer(guard, clock=lambda: NOW),
            id_factory=IdFactory(),
            clock=lambda: NOW,
        ),
        guard,
        credential_port,
        archive_guard,
    )


def _binding(*, credential_id: UUID = CREDENTIAL) -> ArtifactCredentialBinding:
    return ArtifactCredentialBinding(
        credential_binding_id=credential_id,
        credential_binding_version=1,
        provider="github",
    )


@pytest.mark.anyio
async def test_available_fixture_replays_submission_and_reference_but_isolates_runs() -> None:
    repository = Repository(
        (
            _context(RELATION_A, RUN_A, PUBLICATION_A),
            _context(RELATION_B, RUN_B, PUBLICATION_B),
        )
    )
    actor = _actor()
    provider = CountingProvider(FixtureArtifactProvider())
    service, guard, credentials, archive = _service(
        repository,
        actor=actor,
        provider=provider,
    )

    first = await service.preflight(
        transaction=TRANSACTION,
        organization_id=ORG,
        course_run_homework_id=RELATION_A,
        expected_revision=1,
        artifact_url=URL,
        credential_binding=_binding(),
        actor=actor,
    )
    replay = await service.preflight(
        transaction=TRANSACTION,
        organization_id=ORG,
        course_run_homework_id=RELATION_A,
        expected_revision=1,
        artifact_url=URL,
        credential_binding=_binding(),
        actor=actor,
    )
    other_run = await service.preflight(
        transaction=TRANSACTION,
        organization_id=ORG,
        course_run_homework_id=RELATION_B,
        expected_revision=1,
        artifact_url=URL,
        credential_binding=_binding(),
        actor=actor,
    )

    assert first.submission_id == replay.submission_id
    assert first.artifact_reference_id == replay.artifact_reference_id
    assert other_run.submission_id != first.submission_id
    assert len(repository.submissions) == 2
    assert len(repository.references) == 1
    assert first.read_capability == "available" and first.error is None
    assert provider.calls == credentials.calls == guard.revalidated == 3
    assert archive.calls == [RUN_A, RUN_A, RUN_B]


@pytest.mark.anyio
async def test_unavailable_result_creates_submission_but_never_a_reference() -> None:
    repository = Repository((_context(RELATION_A, RUN_A, PUBLICATION_A),))
    actor = _actor()
    service, guard, credentials, _ = _service(
        repository,
        actor=actor,
        provider=UnavailableProvider(),
    )

    result = await service.preflight(
        transaction=TRANSACTION,
        organization_id=ORG,
        course_run_homework_id=RELATION_A,
        expected_revision=1,
        artifact_url=URL,
        credential_binding=_binding(),
        actor=actor,
    )

    assert result.submission_revision == 0
    assert result.read_capability == "requires_action"
    assert result.artifact_reference_id is None
    assert result.error == {
        "code": "access_denied",
        "message": "Artifact is unavailable",
        "retryable": False,
        "action": "grant_read_access",
    }
    assert len(repository.submissions) == 1
    assert repository.references == {}
    assert guard.revalidated == credentials.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("context", "organization_id", "actor", "archive", "binding", "error"),
    [
        (
            _context(RELATION_A, RUN_A, PUBLICATION_A, allowed=("google_docs",)),
            ORG,
            _actor(),
            ArchiveGuard(),
            _binding(),
            ArtifactKindNotAllowed,
        ),
        (
            _context(RELATION_A, RUN_A, PUBLICATION_A, enrolled=False),
            ORG,
            _actor(),
            ArchiveGuard(),
            _binding(),
            StudentNotEnrolled,
        ),
        (
            replace(
                _context(RELATION_A, RUN_A, PUBLICATION_A),
                current_publication_id=None,
                current_homework_version_id=None,
            ),
            ORG,
            _actor(),
            ArchiveGuard(),
            _binding(),
            HomeworkNotPublished,
        ),
        (
            _context(RELATION_A, RUN_A, PUBLICATION_A),
            ORG,
            _actor(),
            ArchiveGuard(archived=True),
            _binding(),
            ArchivedPreflightDenied,
        ),
        (
            _context(RELATION_A, RUN_A, PUBLICATION_A),
            ORG,
            _actor(),
            ArchiveGuard(),
            _binding(credential_id=UUID("00000000-0000-7000-8000-000000000999")),
            InvalidArtifactCredentialBinding,
        ),
        (
            _context(RELATION_A, RUN_A, PUBLICATION_A),
            OTHER_ORG,
            _actor(organization_id=OTHER_ORG),
            ArchiveGuard(),
            _binding(),
            PreflightTargetNotFound,
        ),
    ],
)
async def test_preflight_fails_closed_for_kind_archive_binding_and_tenant(
    context: CourseRunHomeworkPreflightContext,
    organization_id: UUID,
    actor: RequestActor,
    archive: ArchiveGuard,
    binding: ArtifactCredentialBinding,
    error: type[Exception],
) -> None:
    repository = Repository((context,))
    provider = CountingProvider(FixtureArtifactProvider())
    service, _, _, _ = _service(
        repository,
        actor=actor,
        provider=provider,
        archive=archive,
    )

    with pytest.raises(error):
        await service.preflight(
            transaction=TRANSACTION,
            organization_id=organization_id,
            course_run_homework_id=RELATION_A,
            expected_revision=1,
            artifact_url=URL,
            credential_binding=binding,
            actor=actor,
        )
    assert repository.submissions == {}
    assert repository.references == {}
    assert provider.calls == 0


@pytest.mark.anyio
async def test_provider_provenance_mismatch_is_rejected_without_reference() -> None:
    repository = Repository((_context(RELATION_A, RUN_A, PUBLICATION_A),))
    actor = _actor()
    service, _, _, _ = _service(
        repository,
        actor=actor,
        provider=WrongTenantProvider(),
    )

    with pytest.raises(ArtifactProviderContractViolation, match="organization"):
        await service.preflight(
            transaction=TRANSACTION,
            organization_id=ORG,
            course_run_homework_id=RELATION_A,
            expected_revision=1,
            artifact_url=URL,
            credential_binding=_binding(),
            actor=actor,
        )
    assert repository.references == {}


def test_provider_url_classification_is_closed() -> None:
    assert provider_for_artifact_url(URL) == "github"
    assert (
        provider_for_artifact_url("https://docs.google.com/document/d/offline-fixture")
        == "google_docs"
    )
    with pytest.raises(ArtifactKindNotAllowed):
        provider_for_artifact_url("https://example.com/untrusted")
