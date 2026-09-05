"""T166 RED specifications for tenant-safe, history-preserving retention."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.util import find_spec
from types import ModuleType
from typing import Any, Literal
from uuid import UUID

import pytest
from sqlalchemy import ForeignKeyConstraint

from review_platform.infrastructure.db.models.publication import ReviewPublication
from review_platform.infrastructure.db.models.review_case import ReviewIteration
from review_platform.infrastructure.db.models.submission import ArtifactVersion
from review_platform.infrastructure.tasks.registry import HANDLER_MODULES, load_handler_modules
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
ORG_A = UUID("00000000-0000-7000-8000-000000166001")
ORG_B = UUID("00000000-0000-7000-8000-000000166002")
ARTIFACT = UUID("00000000-0000-7000-8000-000000166003")
OPERATION = UUID("00000000-0000-7000-8000-000000166004")
REVIEW_REVISION = UUID("00000000-0000-7000-8000-000000166005")
PUBLICATION = UUID("00000000-0000-7000-8000-000000166006")
PREDECESSOR = UUID("00000000-0000-7000-8000-000000166007")
SUCCESSOR = UUID("00000000-0000-7000-8000-000000166008")
DIGEST = "sha256:" + "a" * 64

type RetentionKind = Literal["artifact_bytes", "personal_fields", "operation"]


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: UUID
    organization_id: UUID
    kind: RetentionKind
    eligible_at: datetime
    object_key: str | None = None
    content_digest: str | None = None
    live_promotion: bool = False
    active_course_run: bool = False
    preserved: Mapping[str, object] = field(default_factory=dict)


class RecordingRepository:
    def __init__(self, candidates: Sequence[Candidate]) -> None:
        self.candidates = tuple(candidates)
        self.scans: list[tuple[UUID, datetime, datetime, datetime, int]] = []
        self.claims: list[tuple[UUID, UUID]] = []
        self.tombstones: list[tuple[Candidate, Mapping[str, object]]] = []
        self.deleted_rows: list[Candidate] = []
        self.no_dangling_checks: list[UUID] = []

    async def scan_candidates(
        self,
        organization_id: UUID,
        *,
        artifact_before: datetime,
        history_before: datetime,
        operation_before: datetime,
        limit: int,
    ) -> Sequence[Candidate]:
        self.scans.append(
            (
                organization_id,
                artifact_before,
                history_before,
                operation_before,
                limit,
            )
        )
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.organization_id == organization_id
        )

    async def claim(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> Candidate | None:
        self.claims.append((organization_id, candidate_id))
        return next(
            (
                candidate
                for candidate in self.candidates
                if candidate.organization_id == organization_id
                and candidate.candidate_id == candidate_id
            ),
            None,
        )

    async def write_tombstone(
        self,
        candidate: Candidate,
        tombstone: Mapping[str, object],
    ) -> None:
        self.tombstones.append((candidate, deepcopy(dict(tombstone))))

    async def delete_row(self, candidate: Candidate) -> None:
        self.deleted_rows.append(candidate)

    async def assert_no_dangling_references(self, organization_id: UUID) -> None:
        self.no_dangling_checks.append(organization_id)


class RecordingObjects:
    def __init__(self) -> None:
        self.deleted: list[tuple[UUID, UUID, str]] = []

    def delete(
        self,
        *,
        organization_id: UUID,
        artifact_version_id: UUID,
        key: str,
    ) -> None:
        expected_prefix = f"{organization_id}/{artifact_version_id}/"
        if not key.startswith(expected_prefix):
            raise AssertionError("retention attempted cross-tenant object deletion")
        self.deleted.append((organization_id, artifact_version_id, key))


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    async def record_purge(self, event: Mapping[str, object]) -> None:
        self.events.append(deepcopy(dict(event)))


def _handler(
    candidates: Sequence[Candidate],
) -> tuple[Any, RecordingRepository, RecordingObjects, RecordingAudit]:
    module = _retention_module()
    handler_type = getattr(module, "RetentionTaskHandler", None)
    assert isinstance(handler_type, type), (
        "T167 must expose RetentionTaskHandler for the registered retention worker"
    )
    repository = RecordingRepository(candidates)
    objects = RecordingObjects()
    audit = RecordingAudit()
    handler = handler_type(
        repository=repository,
        objects=objects,
        audit=audit,
        settings=Settings(
            artifact_bytes_retention_days=90,
            history_retention_days=365,
            operation_retention_days=90,
        ),
        clock=lambda: NOW,
    )
    return handler, repository, objects, audit


def test_retention_windows_and_immutable_history_columns_are_explicit() -> None:
    settings = Settings()
    assert settings.artifact_bytes_retention_days == 90
    assert settings.history_retention_days == 365
    assert settings.operation_retention_days == 90
    assert settings.log_retention_days == 30
    assert {
        "id",
        "organization_id",
        "provider_version",
        "content_digest",
        "object_key",
        "metadata",
    } <= set(ArtifactVersion.__table__.c.keys())
    assert {
        "review_iteration_id",
        "review_revision_id",
        "publication_version",
        "published_at",
    } <= set(ReviewPublication.__table__.c.keys())
    successor_fks = [
        constraint
        for constraint in ReviewIteration.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and any(
            element.target_fullname == "review_iteration.id"
            for element in constraint.elements
        )
    ]
    assert successor_fks
    assert all(constraint.ondelete == "RESTRICT" for constraint in successor_fks)


async def test_dry_run_reports_eligible_and_protected_without_any_mutation() -> None:
    eligible = _artifact_candidate(ORG_A)
    protected = _artifact_candidate(
        ORG_A,
        identity=UUID("00000000-0000-7000-8000-000000166010"),
        live_promotion=True,
    )
    foreign = _artifact_candidate(
        ORG_B,
        identity=UUID("00000000-0000-7000-8000-000000166011"),
    )
    handler, repository, objects, audit = _handler((eligible, protected, foreign))

    result = await handler.run(organization_id=ORG_A, dry_run=True, limit=100)

    assert result.organization_id == ORG_A
    assert result.dry_run is True
    assert result.scanned == 2
    assert result.eligible == 1
    assert result.protected == 1
    assert result.claimed == result.tombstoned == result.objects_deleted == 0
    assert repository.claims == repository.tombstones == repository.deleted_rows == []
    assert repository.no_dangling_checks == []
    assert objects.deleted == []
    assert audit.events == []
    assert repository.scans == [
        (
            ORG_A,
            NOW - timedelta(days=90),
            NOW - timedelta(days=365),
            NOW - timedelta(days=90),
            100,
        )
    ]


async def test_byte_purge_writes_explicit_tombstone_and_preserves_digest_provenance() -> None:
    candidate = _artifact_candidate(ORG_A)
    preserved_before = deepcopy(dict(candidate.preserved))
    handler, repository, objects, audit = _handler((candidate,))

    result = await handler.run(organization_id=ORG_A, dry_run=False, limit=10)

    assert result.tombstoned == result.objects_deleted == 1
    assert result.rows_deleted == 0
    assert repository.claims == [(ORG_A, ARTIFACT)]
    assert objects.deleted == [(ORG_A, ARTIFACT, candidate.object_key)]
    assert len(repository.tombstones) == 1
    tombstoned_candidate, tombstone = repository.tombstones[0]
    assert tombstoned_candidate is candidate
    assert tombstone["kind"] == "artifact_bytes_removed"
    assert tombstone["removed_at"] == NOW.isoformat()
    assert tombstone["content_digest"] == DIGEST
    assert tombstone["provenance"] == preserved_before
    assert candidate.content_digest == DIGEST
    assert dict(candidate.preserved) == preserved_before
    assert repository.no_dangling_checks == [ORG_A]
    _assert_audited(audit, result)


async def test_history_expiry_tombstones_personal_fields_but_keeps_successor_chain() -> None:
    preserved = {
        "artifact_version_id": str(ARTIFACT),
        "content_digest": DIGEST,
        "review_revision_id": str(REVIEW_REVISION),
        "review_publication_id": str(PUBLICATION),
        "predecessor_iteration_id": str(PREDECESSOR),
        "successor_iteration_id": str(SUCCESSOR),
        "contract_version": "1.1.0",
    }
    candidate = Candidate(
        candidate_id=REVIEW_REVISION,
        organization_id=ORG_A,
        kind="personal_fields",
        eligible_at=NOW - timedelta(days=366),
        preserved=preserved,
    )
    handler, repository, objects, audit = _handler((candidate,))

    result = await handler.run(organization_id=ORG_A, dry_run=False, limit=10)

    assert result.tombstoned == 1
    assert result.rows_deleted == result.objects_deleted == 0
    assert objects.deleted == []
    assert repository.deleted_rows == []
    _, tombstone = repository.tombstones[0]
    assert tombstone["kind"] == "personal_fields_removed"
    assert tombstone["provenance"] == preserved
    tombstone_provenance = tombstone["provenance"]
    assert isinstance(tombstone_provenance, Mapping)
    assert set(tombstone_provenance) == set(preserved)
    assert repository.no_dangling_checks == [ORG_A]
    _assert_audited(audit, result)


async def test_operational_purge_deletes_only_claimed_unreferenced_tenant_rows() -> None:
    own = Candidate(
        candidate_id=OPERATION,
        organization_id=ORG_A,
        kind="operation",
        eligible_at=NOW - timedelta(days=91),
    )
    foreign = Candidate(
        candidate_id=UUID("00000000-0000-7000-8000-000000166012"),
        organization_id=ORG_B,
        kind="operation",
        eligible_at=NOW - timedelta(days=91),
    )
    handler, repository, objects, audit = _handler((own, foreign))

    result = await handler.run(organization_id=ORG_A, dry_run=False, limit=10)

    assert result.scanned == result.eligible == result.claimed == 1
    assert result.rows_deleted == 1
    assert result.tombstoned == result.objects_deleted == 0
    assert repository.claims == [(ORG_A, OPERATION)]
    assert repository.deleted_rows == [own]
    assert all(candidate.organization_id == ORG_A for candidate in repository.deleted_rows)
    assert repository.no_dangling_checks == [ORG_A]
    assert objects.deleted == []
    _assert_audited(audit, result)


async def test_active_run_and_live_promotion_are_protected_and_registry_is_concrete() -> None:
    active = _artifact_candidate(
        ORG_A,
        identity=UUID("00000000-0000-7000-8000-000000166013"),
        active_course_run=True,
    )
    handler, repository, objects, audit = _handler((active,))

    result = await handler.run(organization_id=ORG_A, dry_run=False, limit=10)

    assert result.protected == 1
    assert result.claimed == result.tombstoned == result.objects_deleted == 0
    assert repository.claims == repository.tombstones == []
    assert objects.deleted == []
    assert audit.events == []
    assert "review_platform.infrastructure.tasks.retention" in HANDLER_MODULES
    registry = load_handler_modules()
    retention_specs = [
        spec
        for spec in registry
        if spec.handler.__module__ == "review_platform.infrastructure.tasks.retention"
    ]
    assert len(retention_specs) == 1
    assert retention_specs[0].requires_auth_revalidation is False


def _retention_module() -> ModuleType:
    name = "review_platform.infrastructure.tasks.retention"
    specification = find_spec(name)
    assert specification is not None, (
        "T167 missing: tenant-safe retention task behavior is not implemented"
    )
    return import_module(name)


def _artifact_candidate(
    organization_id: UUID,
    *,
    identity: UUID = ARTIFACT,
    live_promotion: bool = False,
    active_course_run: bool = False,
) -> Candidate:
    return Candidate(
        candidate_id=identity,
        organization_id=organization_id,
        kind="artifact_bytes",
        eligible_at=NOW - timedelta(days=91),
        object_key=f"{organization_id}/{identity}/artifact.bin",
        content_digest=DIGEST,
        live_promotion=live_promotion,
        active_course_run=active_course_run,
        preserved={
            "artifact_version_id": str(identity),
            "content_digest": DIGEST,
            "submission_version_id": "00000000-0000-7000-8000-000000166020",
            "review_revision_id": str(REVIEW_REVISION),
            "review_publication_id": str(PUBLICATION),
            "contract_version": "1.1.0",
        },
    )


def _assert_audited(audit: RecordingAudit, result: Any) -> None:
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event["organization_id"] == ORG_A
    assert event["action"] == "retention_purge"
    assert event["dry_run"] is False
    assert event["scanned"] == result.scanned
    assert event["claimed"] == result.claimed
    assert event["tombstoned"] == result.tombstoned
    assert event["objects_deleted"] == result.objects_deleted
    assert event["rows_deleted"] == result.rows_deleted
