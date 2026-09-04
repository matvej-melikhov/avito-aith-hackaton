from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from review_platform.application.audit import AuditRecorder
from review_platform.application.authorization import Authorizer
from review_platform.application.request_context import AuthVersionSnapshot, RequestActor
from review_platform.application.services.submissions import (
    ArtifactCaptureRequest,
    ArtifactReferenceRecord,
    ArtifactReferenceUnavailable,
    EffectiveHomeworkPublication,
    SubmissionRecord,
    SubmissionRevisionConflict,
    SubmissionScopeDenied,
    SubmissionService,
    SubmissionVersionRecord,
)

ORG = UUID("00000000-0000-7000-8000-000000000001")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000000002")
USER = UUID("00000000-0000-7000-8000-000000001001")
SUBMISSION = UUID("00000000-0000-7000-8000-000000001002")
RUN = UUID("00000000-0000-7000-8000-000000001003")
HOMEWORK = UUID("00000000-0000-7000-8000-000000001004")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000001005")
PUBLICATION = UUID("00000000-0000-7000-8000-000000001006")
CRH = UUID("00000000-0000-7000-8000-000000001007")
REFERENCE = UUID("00000000-0000-7000-8000-000000001008")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class Guard:
    async def revalidate(self, *, actor: RequestActor) -> AuthVersionSnapshot:
        return self.snapshot(actor)

    async def lock_and_revalidate(
        self, *, actor: RequestActor, transaction: object
    ) -> AuthVersionSnapshot:
        return self.snapshot(actor)

    @staticmethod
    def snapshot(actor: RequestActor) -> AuthVersionSnapshot:
        assert actor.user_id is not None
        assert actor.membership_revision is not None
        assert actor.auth_epoch is not None
        return AuthVersionSnapshot(
            actor.organization_id,
            actor.user_id,
            actor.roles,
            actor.membership_revision,
            actor.auth_epoch,
            True,
        )


class AuditRepo:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, event: object, *, transaction: object) -> None:
        self.events.append(event)


class Scope:
    async def require_owner_and_enrollment(
        self,
        *,
        actor: RequestActor,
        submission: SubmissionRecord,
        publication: EffectiveHomeworkPublication,
        transaction: object,
    ) -> None:
        if actor.user_id != submission.student_id or publication.course_run_id != RUN:
            raise SubmissionScopeDenied("not owning enrolled student")


class Capture:
    def __init__(self) -> None:
        self.requests: list[ArtifactCaptureRequest] = []

    async def schedule(self, request: ArtifactCaptureRequest, *, transaction: object) -> None:
        self.requests.append(request)


class Repository:
    def __init__(self) -> None:
        self.submission = SubmissionRecord(ORG, SUBMISSION, RUN, HOMEWORK, USER, None, 0)
        self.publication = EffectiveHomeworkPublication(
            ORG,
            CRH,
            PUBLICATION,
            RUN,
            HOMEWORK,
            HOMEWORK_VERSION,
            NOW + timedelta(days=1),
        )
        self.reference = ArtifactReferenceRecord(ORG, REFERENCE, "github", True)
        self.versions: list[SubmissionVersionRecord] = []
        self.pending: SubmissionVersionRecord | None = None
        self.fail_cas = False

    async def lock_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        expected_revision: int,
        transaction: object,
    ) -> SubmissionRecord | None:
        row = self.submission
        if (
            row.organization_id != organization_id
            or row.submission_id != submission_id
            or row.revision != expected_revision
        ):
            return None
        return row

    async def lock_current_publication(
        self, organization_id: UUID, course_run_id: UUID, homework_id: UUID, *, transaction: object
    ) -> EffectiveHomeworkPublication | None:
        row = self.publication
        return (
            row
            if (row.organization_id, row.course_run_id, row.homework_id)
            == (organization_id, course_run_id, homework_id)
            else None
        )

    async def get_artifact_reference(
        self, organization_id: UUID, artifact_reference_id: UUID, *, transaction: object
    ) -> ArtifactReferenceRecord | None:
        return (
            self.reference
            if self.reference.artifact_reference_id == artifact_reference_id
            else None
        )

    async def next_version_sequence(
        self, organization_id: UUID, submission_id: UUID, *, transaction: object
    ) -> int:
        return len(self.versions) + 1

    async def append_version(
        self, version: SubmissionVersionRecord, *, transaction: object
    ) -> None:
        self.pending = version

    async def mark_superseded(
        self, organization_id: UUID, version_id: UUID, *, transaction: object
    ) -> None:
        self.versions = [
            replace(version, status="superseded") if version.version_id == version_id else version
            for version in self.versions
        ]

    async def compare_and_set_submission(
        self,
        organization_id: UUID,
        submission_id: UUID,
        *,
        expected_revision: int,
        current_predeadline_version_id: UUID | None,
        transaction: object,
    ) -> bool:
        if self.fail_cas:
            self.pending = None
            return False
        if self.pending is None:
            return False
        self.versions.append(self.pending)
        self.pending = None
        self.submission = replace(
            self.submission,
            current_predeadline_version_id=current_predeadline_version_id,
            revision=expected_revision + 1,
        )
        return True


def _actor(*, organization_id: UUID = ORG) -> RequestActor:
    return RequestActor.user(
        organization_id=organization_id,
        user_id=USER,
        roles={"student"},
        membership_revision=2,
        auth_epoch=3,
    )


def _service(
    repository: Repository,
    capture: Capture,
    now: datetime,
    *,
    id_start: int = 1100,
) -> SubmissionService:
    ids = iter(
        UUID(f"00000000-0000-7000-8000-{value:012d}") for value in range(id_start, id_start + 100)
    )
    return SubmissionService(
        repository=repository,
        scope_authorization=Scope(),
        capture_scheduler=capture,
        authorizer=Authorizer(Guard(), clock=lambda: now),
        audit=AuditRecorder(AuditRepo(), event_id_factory=lambda: next(ids), clock=lambda: now),
        id_factory=lambda: next(ids),
        clock=lambda: now,
    )


@pytest.mark.anyio
async def test_predeadline_first_and_replacement_snapshot_and_supersede() -> None:
    repository, capture = Repository(), Capture()
    first = await _service(repository, capture, NOW).submit_work(
        transaction=object(),
        organization_id=ORG,
        submission_id=SUBMISSION,
        expected_submission_revision=0,
        artifact_reference_id=REFERENCE,
        actor=_actor(),
        request_id=UUID(int=1),
        trace_id=UUID(int=2),
    )
    second = await _service(
        repository, capture, NOW + timedelta(hours=1), id_start=1200
    ).submit_work(
        transaction=object(),
        organization_id=ORG,
        submission_id=SUBMISSION,
        expected_submission_revision=1,
        artifact_reference_id=REFERENCE,
        actor=_actor(),
        request_id=UUID(int=3),
        trace_id=UUID(int=4),
    )

    assert (first.version.phase, first.version.status) == ("before_deadline", "validating")
    assert repository.versions[0].status == "superseded"
    assert repository.submission.current_predeadline_version_id == second.version.version_id
    assert second.version.homework_version_id == HOMEWORK_VERSION
    assert second.version.effective_deadline == repository.publication.submission_deadline
    assert [version.sequence for version in repository.versions] == [1, 2]
    assert len(capture.requests) == 2
    assert capture.requests[-1].operation_id == second.version.capture_operation_id
    assert capture.requests[-1].homework_publication_id == PUBLICATION


@pytest.mark.anyio
async def test_late_version_is_pending_and_does_not_open_or_replace_review() -> None:
    repository, capture = Repository(), Capture()
    current = UUID("00000000-0000-7000-8000-000000001099")
    repository.submission = replace(repository.submission, current_predeadline_version_id=current)
    result = await _service(repository, capture, NOW + timedelta(days=2)).submit_work(
        transaction=object(),
        organization_id=ORG,
        submission_id=SUBMISSION,
        expected_submission_revision=0,
        artifact_reference_id=REFERENCE,
        actor=_actor(),
        request_id=UUID(int=5),
        trace_id=UUID(int=6),
    )

    assert (result.version.phase, result.version.status) == ("revision", "pending_review")
    assert repository.submission.current_predeadline_version_id == current
    assert not hasattr(result.version, "review_iteration_id")
    assert not hasattr(result.version, "current_review_id")


@pytest.mark.anyio
async def test_stale_cas_and_wrong_tenant_reference_fail_before_capture() -> None:
    repository, capture = Repository(), Capture()
    repository.fail_cas = True
    with pytest.raises(SubmissionRevisionConflict):
        await _service(repository, capture, NOW).submit_work(
            transaction=object(),
            organization_id=ORG,
            submission_id=SUBMISSION,
            expected_submission_revision=0,
            artifact_reference_id=REFERENCE,
            actor=_actor(),
            request_id=UUID(int=7),
            trace_id=UUID(int=8),
        )
    assert repository.versions == []
    assert capture.requests == []

    repository.fail_cas = False
    repository.reference = replace(repository.reference, organization_id=OTHER_ORG)
    with pytest.raises(ArtifactReferenceUnavailable):
        await _service(repository, capture, NOW).submit_work(
            transaction=object(),
            organization_id=ORG,
            submission_id=SUBMISSION,
            expected_submission_revision=0,
            artifact_reference_id=REFERENCE,
            actor=_actor(),
            request_id=UUID(int=9),
            trace_id=UUID(int=10),
        )
    assert capture.requests == []
