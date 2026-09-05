from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import UTC, datetime
from types import ModuleType
from uuid import UUID

import pytest

from review_platform.application.services.homeworks import HomeworkRequirementsChanged
from review_platform.application.services.review_requirement_impacts import (
    AffectedReviewContext,
    RequirementsChangeContext,
    ReviewImpactRecord,
    ReviewRequirementImpactConflict,
    ReviewRequirementImpactService,
)
from review_platform.infrastructure.tasks.registry import (
    REGISTRY,
    REVIEW_IMPACT_HANDLER_FACTORY_ENV,
    handle_homework_requirements_changed,
)

pytestmark = pytest.mark.anyio

ORG = UUID("00000000-0000-7000-8000-000000000001")
COURSE_RUN = UUID("00000000-0000-7000-8000-000000016001")
RELATION = UUID("00000000-0000-7000-8000-000000016002")
HOMEWORK = UUID("00000000-0000-7000-8000-000000016003")
OLD_VERSION = UUID("00000000-0000-7000-8000-000000016004")
NEW_VERSION = UUID("00000000-0000-7000-8000-000000016005")
NEW_CRITERIA = UUID("00000000-0000-7000-8000-000000016006")
OLD_PUBLICATION = UUID("00000000-0000-7000-8000-000000016007")
NEW_PUBLICATION = UUID("00000000-0000-7000-8000-000000016008")
SOURCE_EVENT = UUID("00000000-0000-7000-8000-000000016009")
CASE_A = UUID("00000000-0000-7000-8000-000000016010")
CASE_B = UUID("00000000-0000-7000-8000-000000016011")
ITERATION_A = UUID("00000000-0000-7000-8000-000000016012")
ITERATION_B = UUID("00000000-0000-7000-8000-000000016013")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class IDs:
    def __init__(self, value: int = 17000) -> None:
        self.value = value

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"00000000-0000-7000-8000-{self.value:012d}")


class Repository:
    def __init__(self) -> None:
        self.context = _context()
        self.affected: tuple[AffectedReviewContext, ...] = (
            _affected(CASE_A, ITERATION_A, 1),
            _affected(CASE_B, ITERATION_B, 2),
        )
        self.impacts: dict[tuple[UUID, UUID], ReviewImpactRecord] = {}
        self.context_calls = 0

    async def lock_change_context(
        self,
        event: HomeworkRequirementsChanged,
        *,
        transaction: object,
    ) -> RequirementsChangeContext | None:
        assert event.organization_id == ORG and transaction is TRANSACTION
        self.context_calls += 1
        return self.context

    async def list_affected_reviews(
        self,
        context: RequirementsChangeContext,
        *,
        transaction: object,
    ) -> tuple[AffectedReviewContext, ...]:
        assert context == self.context and transaction is TRANSACTION
        return self.affected

    async def reserve_impact(
        self,
        candidate: ReviewImpactRecord,
        *,
        transaction: object,
    ) -> tuple[ReviewImpactRecord, bool]:
        assert transaction is TRANSACTION
        key = (candidate.review_iteration_id, candidate.current_homework_version_id)
        existing = self.impacts.get(key)
        if existing is not None:
            return existing, False
        self.impacts[key] = candidate
        return candidate, True


class Operations:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record_ingestion(self, **values: object) -> None:
        assert values["transaction"] is TRANSACTION
        self.calls.append(values)


class Audit:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def record_impacts(self, **values: object) -> None:
        assert values["transaction"] is TRANSACTION
        self.calls.append(values)


TRANSACTION = object()


def _event(
    *,
    previous_version: UUID | None = OLD_VERSION,
    previous_publication: UUID | None = OLD_PUBLICATION,
) -> HomeworkRequirementsChanged:
    return HomeworkRequirementsChanged(
        organization_id=ORG,
        course_run_id=COURSE_RUN,
        course_run_homework_id=RELATION,
        homework_id=HOMEWORK,
        previous_homework_version_id=previous_version,
        current_homework_version_id=NEW_VERSION,
        previous_publication_id=previous_publication,
        current_publication_id=NEW_PUBLICATION,
        publication_sequence=2,
    )


def _context() -> RequirementsChangeContext:
    return RequirementsChangeContext(
        organization_id=ORG,
        course_run_id=COURSE_RUN,
        course_run_homework_id=RELATION,
        homework_id=HOMEWORK,
        previous_homework_version_id=OLD_VERSION,
        current_homework_version_id=NEW_VERSION,
        current_criterion_set_id=NEW_CRITERIA,
        previous_publication_id=OLD_PUBLICATION,
        current_publication_id=NEW_PUBLICATION,
        publication_sequence=2,
    )


def _affected(case_id: UUID, iteration_id: UUID, number: int) -> AffectedReviewContext:
    return AffectedReviewContext(
        organization_id=ORG,
        review_case_id=case_id,
        review_iteration_id=iteration_id,
        course_run_id=COURSE_RUN,
        course_run_homework_id=RELATION,
        homework_id=HOMEWORK,
        effective_homework_version_id=OLD_VERSION,
        iteration_number=number,
        iteration_status="published" if number == 1 else "in_review",
    )


def _service(
    repository: Repository,
) -> tuple[ReviewRequirementImpactService, Operations, Audit]:
    operations = Operations()
    audit = Audit()
    return (
        ReviewRequirementImpactService(
            repository=repository,
            operations=operations,
            audit=audit,
            id_factory=IDs(),
            clock=lambda: NOW,
        ),
        operations,
        audit,
    )


async def test_consumption_is_idempotent_and_projects_deterministic_successors() -> None:
    repository = Repository()
    service, operations, audit = _service(repository)
    predecessor_bytes = tuple(asdict(review) for review in repository.affected)

    first = await service.consume(
        source_event_id=SOURCE_EVENT,
        event=_event(),
        transaction=TRANSACTION,
    )
    replay = await service.consume(
        source_event_id=SOURCE_EVENT,
        event=_event(),
        transaction=TRANSACTION,
    )

    assert len(first.created_impact_ids) == 2
    assert first.replayed_impact_ids == ()
    assert replay.created_impact_ids == ()
    assert len(replay.replayed_impact_ids) == 2
    assert len(repository.impacts) == 2
    assert [item.predecessor_iteration_id for item in first.proposals] == [
        ITERATION_A,
        ITERATION_B,
    ]
    assert all(item.target_homework_version_id == NEW_VERSION for item in first.proposals)
    assert all(item.target_criterion_set_id == NEW_CRITERIA for item in first.proposals)
    assert tuple(asdict(review) for review in repository.affected) == predecessor_bytes
    assert [(call["created_count"], call["replayed_count"]) for call in operations.calls] == [
        (2, 0),
        (0, 2),
    ]
    assert len(audit.calls) == 1


async def test_first_publication_has_no_impacts_and_does_not_query_reviews() -> None:
    repository = Repository()
    service, operations, audit = _service(repository)

    result = await service.consume(
        source_event_id=SOURCE_EVENT,
        event=_event(previous_version=None, previous_publication=None),
        transaction=TRANSACTION,
    )

    assert result.created_impact_ids == result.replayed_impact_ids == ()
    assert result.proposals == ()
    assert repository.context_calls == 0
    assert len(operations.calls) == 1
    assert audit.calls == []


async def test_cross_tenant_or_current_requirements_projection_fails_closed() -> None:
    repository = Repository()
    repository.context = RequirementsChangeContext(
        **{**asdict(repository.context), "organization_id": UUID(int=2)}
    )
    service, _, _ = _service(repository)
    with pytest.raises(ReviewRequirementImpactConflict, match="provenance"):
        await service.consume(
            source_event_id=SOURCE_EVENT,
            event=_event(),
            transaction=TRANSACTION,
        )

    repository = Repository()
    repository.affected = (
        AffectedReviewContext(
            **{
                **asdict(repository.affected[0]),
                "effective_homework_version_id": NEW_VERSION,
            }
        ),
    )
    service, _, _ = _service(repository)
    with pytest.raises(ReviewRequirementImpactConflict, match="current requirements"):
        await service.consume(
            source_event_id=SOURCE_EVENT,
            event=_event(),
            transaction=TRANSACTION,
        )


async def test_registry_handler_uses_only_explicit_offline_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def handler(*, organization_id: str, message_id: str) -> dict[str, str]:
        calls.append((organization_id, message_id))
        return {"organization_id": organization_id, "message_id": message_id}

    module = ModuleType("review_impact_fixture_factory")
    module.build = lambda: handler  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv(
        REVIEW_IMPACT_HANDLER_FACTORY_ENV,
        f"{module.__name__}:build",
    )

    result = await handle_homework_requirements_changed(
        organization_id=str(ORG),
        message_id=str(SOURCE_EVENT),
    )

    assert result == {"organization_id": str(ORG), "message_id": str(SOURCE_EVENT)}
    assert calls == [(str(ORG), str(SOURCE_EVENT))]
    spec = REGISTRY.resolve_event("HomeworkRequirementsChanged")
    assert spec.name == "review_platform.review_requirement_impacts"
    assert spec.kind == "course_import"
    assert spec.requires_auth_revalidation is False
