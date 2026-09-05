"""MySQL-backed repository tests for AI run, attempt, event, and output history."""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import getsource, signature
from uuid import UUID

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from testcontainers.mysql import MySqlContainer

from review_platform.application.projections.review_detail import project_ai_review
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AICriterionSuggestion,
    AIReviewAttempt,
    AIReviewEventReceipt,
    AIReviewRun,
    AISignal,
    ExternalCredential,
    Organization,
)
from review_platform.infrastructure.db.repositories.ai_reviews import (
    AIReviewCollision,
    AIReviewRepository,
    AIReviewStateConflict,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
    create_session_factory,
    session_scope,
)

pytestmark = [pytest.mark.infrastructure, pytest.mark.anyio]

ORG = UUID("00000000-0000-7000-8000-000000001101")
OTHER_ORG = UUID("00000000-0000-7000-8000-000000001102")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000001103")
OTHER_CREDENTIAL = UUID("00000000-0000-7000-8000-000000001104")
FINGERPRINT = "sha256:" + "8" * 64
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _run(identity: UUID) -> AIReviewRun:
    return AIReviewRun(
        id=identity,
        organization_id=ORG,
        review_iteration_id=UUID("00000000-0000-7000-8000-000000001110"),
        course_run_id=UUID("00000000-0000-7000-8000-000000001111"),
        submission_version_id=UUID("00000000-0000-7000-8000-000000001112"),
        artifact_version_id=UUID("00000000-0000-7000-8000-000000001113"),
        content_digest="sha256:" + "5" * 64,
        homework_version_id=UUID("00000000-0000-7000-8000-000000001114"),
        homework_digest="sha256:" + "6" * 64,
        criterion_set_id=UUID("00000000-0000-7000-8000-000000001115"),
        criteria_digest="sha256:" + "7" * 64,
        contract_version="1.1.0",
        fingerprint_algorithm="jcs-sha256-v1",
        input_fingerprint=FINGERPRINT,
        status="pending",
        current_attempt_no=0,
        revision=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _event(
    event_id: UUID,
    run_id: UUID,
    attempt_id: UUID,
    *,
    digest: str = "sha256:" + "9" * 64,
    sequence: int = 1,
) -> AIReviewEventReceipt:
    return AIReviewEventReceipt(
        event_id=event_id,
        organization_id=ORG,
        ai_review_run_id=run_id,
        attempt_id=attempt_id,
        attempt_number=1,
        sequence=sequence,
        payload_digest=digest,
        status="partial",
        is_final=False,
        received_at=NOW,
    )


def test_public_tenant_lookups_require_organization_and_repository_does_not_commit() -> None:
    for method in (
        AIReviewRepository.get_run,
        AIReviewRepository.get_by_fingerprint,
        AIReviewRepository.get_attempt,
        AIReviewRepository.compare_and_set_current_attempt,
        AIReviewRepository.transition_attempt,
        AIReviewRepository.transition_run,
        AIReviewRepository.history,
    ):
        parameter = signature(method).parameters["organization_id"]
        assert parameter.default is parameter.empty
    source = getsource(AIReviewRepository)
    assert ".commit(" not in source
    assert "ReviewRevision" not in source


async def test_mysql_unique_race_event_collision_old_attempt_and_terminal_cas(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    factory = create_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_scope(factory) as session:
        session.add_all(
            [
                Organization(id=ORG, slug="ai-repository-org", name="AI Repository Org"),
                Organization(
                    id=OTHER_ORG,
                    slug="ai-repository-other",
                    name="AI Repository Other",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ExternalCredential(
                    id=CREDENTIAL,
                    organization_id=ORG,
                    provider="ai_component",
                    binding_version=1,
                    ciphertext="cipher",
                    key_id="key",
                ),
                ExternalCredential(
                    id=OTHER_CREDENTIAL,
                    organization_id=OTHER_ORG,
                    provider="ai_component",
                    binding_version=1,
                    ciphertext="cipher",
                    key_id="key",
                ),
            ]
        )

    results: list[tuple[UUID, bool]] = []

    async def reserve(identity: UUID) -> None:
        async with session_scope(factory) as session:
            await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            stored, created = await AIReviewRepository(session).create_or_get_run(_run(identity))
            results.append((stored.id, created))
            await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reserve, UUID("00000000-0000-7000-8000-000000001120"))
        tasks.start_soon(reserve, UUID("00000000-0000-7000-8000-000000001121"))
    assert sorted(created for _, created in results) == [False, True]
    assert len({identity for identity, _ in results}) == 1
    run_id = results[0][0]

    attempt1_id = UUID("00000000-0000-7000-8000-000000001122")
    attempt2_id = UUID("00000000-0000-7000-8000-000000001123")
    event_id = UUID("00000000-0000-7000-8000-000000001124")
    later_event_id = UUID("00000000-0000-7000-8000-000000001100")
    async with session_scope(factory) as session:
        repository = AIReviewRepository(session)
        await repository.add_attempt(
            AIReviewAttempt(
                id=attempt1_id,
                organization_id=ORG,
                ai_review_run_id=run_id,
                attempt_number=1,
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                status="running",
                last_sequence=0,
                started_at=NOW,
            )
        )
        assert await repository.compare_and_set_current_attempt(
            ORG,
            run_id,
            expected_revision=0,
            expected_current_attempt_no=0,
            new_attempt_no=1,
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await repository.add_attempt(
                    AIReviewAttempt(
                        id=UUID("00000000-0000-7000-8000-000000001125"),
                        organization_id=ORG,
                        ai_review_run_id=run_id,
                        attempt_number=2,
                        credential_binding_id=OTHER_CREDENTIAL,
                        credential_binding_version=1,
                        status="pending",
                        last_sequence=0,
                        started_at=NOW,
                    )
                )
        stored_event, created = await repository.reserve_event(
            _event(event_id, run_id, attempt1_id)
        )
        assert created
        replay, created = await repository.reserve_event(_event(event_id, run_id, attempt1_id))
        assert not created and replay.event_id == stored_event.event_id
        with pytest.raises(AIReviewCollision, match="global event ID"):
            await repository.reserve_event(
                _event(event_id, run_id, attempt1_id, digest="sha256:" + "a" * 64)
            )
        later_event, created = await repository.reserve_event(
            _event(
                later_event_id,
                run_id,
                attempt1_id,
                digest="sha256:" + "b" * 64,
                sequence=2,
            )
        )
        assert created and later_event.sequence == 2
        with pytest.raises(AIReviewStateConflict, match="sequence"):
            await repository.transition_attempt(
                ORG,
                attempt1_id,
                expected_status="running",
                expected_last_sequence=0,
                new_status="partial",
                new_sequence=0,
            )
        assert await repository.transition_attempt(
            ORG,
            attempt1_id,
            expected_status="running",
            expected_last_sequence=0,
            new_status="retryable_failed",
            new_sequence=2,
            error={"code": "provider_failed", "message": "Bearer secret", "action": "retry"},
        )
        await repository.add_attempt(
            AIReviewAttempt(
                id=attempt2_id,
                organization_id=ORG,
                ai_review_run_id=run_id,
                attempt_number=2,
                credential_binding_id=CREDENTIAL,
                credential_binding_version=1,
                status="running",
                last_sequence=0,
                started_at=NOW,
            )
        )
        assert await repository.compare_and_set_current_attempt(
            ORG,
            run_id,
            expected_revision=1,
            expected_current_attempt_no=1,
            new_attempt_no=2,
        )
        assert not await repository.transition_run(
            ORG,
            run_id,
            expected_revision=2,
            expected_status="running",
            current_attempt_no=1,
            new_status="partial",
        )
        assert await repository.transition_run(
            ORG,
            run_id,
            expected_revision=2,
            expected_status="running",
            current_attempt_no=2,
            new_status="succeeded",
        )
        with pytest.raises(AIReviewStateConflict, match="terminal"):
            await repository.transition_run(
                ORG,
                run_id,
                expected_revision=3,
                expected_status="succeeded",
                current_attempt_no=2,
                new_status="running",
            )
        await session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        suggestion = AICriterionSuggestion(
            id=UUID("00000000-0000-7000-8000-000000001126"),
            organization_id=ORG,
            ai_review_run_id=run_id,
            event_id=event_id,
            criterion_id=UUID("00000000-0000-7000-8000-000000001127"),
            status="suggested",
            proposed_points=None,
            reason="Needs review",
            evidence=[],
            confidence="medium",
            flags=[],
        )
        signal = AISignal(
            id=UUID("00000000-0000-7000-8000-000000001128"),
            organization_id=ORG,
            ai_review_run_id=run_id,
            event_id=event_id,
            level="low",
            evidence=[],
            limitations=[],
            questions=[],
        )
        later_suggestion = AICriterionSuggestion(
            id=UUID("00000000-0000-7000-8000-000000001129"),
            organization_id=ORG,
            ai_review_run_id=run_id,
            event_id=later_event_id,
            criterion_id=UUID("00000000-0000-7000-8000-000000001130"),
            status="suggested",
            proposed_points=None,
            reason="Later sequence",
            evidence=[],
            confidence="high",
            flags=[],
        )
        later_signal = AISignal(
            id=UUID("00000000-0000-7000-8000-000000001131"),
            organization_id=ORG,
            ai_review_run_id=run_id,
            event_id=later_event_id,
            level="high",
            evidence=[],
            limitations=[],
            questions=[],
        )
        await repository.append_outputs(
            [suggestion, later_suggestion],
            signal,
        )
        await repository.append_outputs([], later_signal)
        await session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        history = await repository.history(ORG, run_id)
        assert history is not None
        assert [item.attempt_number for item in history.attempts] == [1, 2]
        assert [item.sequence for item in history.events] == [1, 2]
        assert [item.event_id for item in history.suggestions] == [
            event_id,
            later_event_id,
        ]
        assert [item.event_id for item in history.signals] == [
            event_id,
            later_event_id,
        ]
        projected = project_ai_review(history)
        assert projected is not None
        assert [item["reason"] for item in projected["suggestions"]] == [
            "Needs review",
            "Later sequence",
        ]
        assert projected["signal"]["level"] == "high"
        attempt1 = await repository.get_attempt(ORG, attempt1_id)
        assert attempt1 is not None
        assert "secret" not in str(attempt1.sanitized_error).lower()
        assert history.run.current_attempt_no == 2
        assert history.run.status == "succeeded"
    await engine.dispose()
