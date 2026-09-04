from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    FoundationRuntime,
)
from review_platform.application.request_context import RequestActor

pytestmark = [pytest.mark.behavioral, pytest.mark.anyio]

ORGANIZATION_ID = "00000000-0000-7000-8000-000000000001"
COURSE_ID = "00000000-0000-7000-8000-000000000002"
USER_ID = "00000000-0000-7000-8000-000000000003"


def _archive_course_command() -> dict[str, Any]:
    return {
        "request_id": "00000000-0000-7000-8000-000000000004",
        "idempotency_key": "archive-course-fixture-0001",
        "command_name": "archive_course",
        "revision_target": "course",
        "target_id": COURSE_ID,
        "expected_revision": 4,
        "payload": {"reason": "course completed"},
    }


TRACE_ID = UUID("00000000-0000-7000-8000-000000000009")


def _user_actor() -> RequestActor:
    return RequestActor.user(
        organization_id=UUID(ORGANIZATION_ID),
        user_id=UUID(USER_ID),
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
    )


async def test_foundation_runtime_is_composed_only_from_planned_production_modules(
    foundation_runtime: FoundationRuntime,
) -> None:
    runtime = foundation_runtime
    components = runtime.components()
    expected_modules = {
        "command_bus": "review_platform.application.command_bus",
        "operation_repository": "review_platform.infrastructure.db.repositories.operations",
        "outbox_repository": "review_platform.infrastructure.db.outbox",
        "object_storage": "review_platform.infrastructure.object_storage.s3",
        "redactor": "review_platform.api.middleware",
    }

    assert set(components) == set(expected_modules)
    for name, expected_module in expected_modules.items():
        assert type(components[name]).__module__ == expected_module
        assert "probe" not in type(components[name]).__name__.casefold()
        assert not any(key.startswith("_foundation_") for key in vars(components[name]))


async def test_rest_dispatch_binds_the_exact_route_command(
    foundation_runtime: FoundationRuntime,
) -> None:
    receipt = foundation_runtime.bind_rest_command(
        route_command="archive_course",
        path_target_id=COURSE_ID,
        wire_command=_archive_course_command(),
        actor=_user_actor(),
        trace_id=TRACE_ID,
    )

    assert receipt.command_name == "archive_course"
    assert str(receipt.target_id) == COURSE_ID


async def test_route_rejects_a_different_valid_command_variant(
    foundation_runtime: FoundationRuntime,
) -> None:
    command = _archive_course_command()
    command.update({"command_name": "restore_course", "payload": {}})

    with pytest.raises(BoundaryViolation, match="route command"):
        foundation_runtime.bind_rest_command(
            route_command="archive_course",
            path_target_id=COURSE_ID,
            wire_command=command,
            actor=_user_actor(),
            trace_id=TRACE_ID,
        )


async def test_path_identifier_must_equal_command_target_identifier(
    foundation_runtime: FoundationRuntime,
) -> None:
    with pytest.raises(BoundaryViolation, match="target"):
        foundation_runtime.bind_rest_command(
            route_command="archive_course",
            path_target_id="00000000-0000-7000-8000-000000000099",
            wire_command=_archive_course_command(),
            actor=_user_actor(),
            trace_id=TRACE_ID,
        )


async def test_client_cannot_supply_server_owned_actor_or_organization(
    foundation_runtime: FoundationRuntime,
) -> None:
    command = deepcopy(_archive_course_command())
    command.update({"organization_id": ORGANIZATION_ID, "actor": {"type": "user"}})

    with pytest.raises(BoundaryViolation, match="server-owned"):
        foundation_runtime.bind_rest_command(
            route_command="archive_course",
            path_target_id=COURSE_ID,
            wire_command=command,
            actor=_user_actor(),
            trace_id=TRACE_ID,
        )


async def test_bootstrap_and_recovery_are_local_operator_commands(
    foundation_runtime: FoundationRuntime,
) -> None:
    command = {
        "command_name": "recover_methodologist",
        "request_id": "00000000-0000-7000-8000-000000000004",
        "idempotency_key": "recovery-fixture-0001",
        "revision_target": "organization",
        "target_id": ORGANIZATION_ID,
        "expected_revision": 1,
        "payload": {
            "organization_id": ORGANIZATION_ID,
            "provider": "stepik",
            "issuer": "https://stepik.org",
            "subject": "selected-user",
            "reason": "restore the last methodologist",
        },
    }

    result = foundation_runtime.bind_operator_command(
        command=command,
        organization_id=UUID(ORGANIZATION_ID),
        operator_id="local-operator",
        reason="authorized recovery",
        trace_id=TRACE_ID,
    )
    assert result.actor.type == "installation_operator"


async def test_human_publish_cannot_use_agent_authorization(
    foundation_runtime: FoundationRuntime,
) -> None:
    command = _archive_course_command()
    command.update(
        {
            "command_name": "publish_review",
            "revision_target": "review_iteration",
            "payload": {"review_revision_id": COURSE_ID},
        }
    )
    agent_authorization = RequestActor.agent(
        organization_id=UUID(ORGANIZATION_ID),
        user_id=UUID(USER_ID),
        roles={"reviewer"},
        membership_revision=3,
        auth_epoch=2,
        agent_id=UUID(COURSE_ID),
        agent_authorization_id=UUID(COURSE_ID),
        agent_authorization_revision=1,
        scopes={"reviews:write"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(BoundaryViolation, match="interactive.*REST"):
        foundation_runtime.bind_rest_command(
            route_command="publish_review",
            path_target_id=COURSE_ID,
            wire_command=command,
            actor=agent_authorization,
            trace_id=TRACE_ID,
        )
