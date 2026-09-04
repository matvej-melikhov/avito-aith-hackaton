from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    build_foundation_runtime,
)

pytestmark = pytest.mark.behavioral

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


def _user_authorization() -> dict[str, Any]:
    return {
        "organization_id": ORGANIZATION_ID,
        "actor": {"type": "user", "user_id": USER_ID, "membership_revision": 3, "auth_epoch": 2},
        "transport": "rest",
    }


def test_foundation_runtime_is_composed_only_from_planned_production_modules() -> None:
    runtime = build_foundation_runtime()
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


def test_rest_dispatch_binds_the_exact_route_command() -> None:
    runtime = build_foundation_runtime()
    receipt = runtime.dispatch_rest(
        route_command="archive_course",
        path_target_id=COURSE_ID,
        wire_command=_archive_course_command(),
        authorization=_user_authorization(),
    )

    assert receipt["command_name"] == "archive_course"
    assert receipt["target_id"] == COURSE_ID


def test_route_rejects_a_different_valid_command_variant() -> None:
    runtime = build_foundation_runtime()
    command = _archive_course_command()
    command.update({"command_name": "restore_course", "payload": {}})

    with pytest.raises(BoundaryViolation, match="route command"):
        runtime.dispatch_rest(
            route_command="archive_course",
            path_target_id=COURSE_ID,
            wire_command=command,
            authorization=_user_authorization(),
        )


def test_path_identifier_must_equal_command_target_identifier() -> None:
    runtime = build_foundation_runtime()

    with pytest.raises(BoundaryViolation, match="target"):
        runtime.dispatch_rest(
            route_command="archive_course",
            path_target_id="00000000-0000-7000-8000-000000000099",
            wire_command=_archive_course_command(),
            authorization=_user_authorization(),
        )


def test_client_cannot_supply_server_owned_actor_or_organization() -> None:
    runtime = build_foundation_runtime()
    command = deepcopy(_archive_course_command())
    command.update({"organization_id": ORGANIZATION_ID, "actor": {"type": "user"}})

    with pytest.raises(BoundaryViolation, match="server-owned"):
        runtime.dispatch_rest(
            route_command="archive_course",
            path_target_id=COURSE_ID,
            wire_command=command,
            authorization=_user_authorization(),
        )


def test_bootstrap_and_recovery_are_local_operator_commands() -> None:
    runtime = build_foundation_runtime()
    command = {
        "command_name": "recover_methodologist",
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

    result = runtime.dispatch_operator(
        command=command, operator_id="local-operator", reason="authorized recovery"
    )
    assert result["actor"]["type"] == "installation_operator"


def test_human_publish_cannot_use_agent_authorization() -> None:
    runtime = build_foundation_runtime()
    command = _archive_course_command()
    command.update(
        {
            "command_name": "publish_review",
            "revision_target": "review_iteration",
            "payload": {"review_revision_id": COURSE_ID},
        }
    )
    agent_authorization = {
        "organization_id": ORGANIZATION_ID,
        "transport": "mcp",
        "actor": {
            "type": "agent",
            "user_id": USER_ID,
            "agent_id": COURSE_ID,
            "agent_authorization_id": COURSE_ID,
        },
    }

    with pytest.raises(BoundaryViolation, match="interactive.*REST"):
        runtime.dispatch_rest(
            route_command="publish_review",
            path_target_id=COURSE_ID,
            wire_command=command,
            authorization=agent_authorization,
        )
