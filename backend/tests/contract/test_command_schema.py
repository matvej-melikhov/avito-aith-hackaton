from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from jsonschema import ValidationError
from tests.support.contracts import CONTRACT_ROOT, load_json, load_schema, validate_definition

UUID = "00000000-0000-7000-8000-000000000001"
UUID_2 = "00000000-0000-7000-8000-000000000002"
DATE = "2026-09-05T12:00:00Z"

PAYLOADS: dict[str, tuple[str, dict[str, Any]]] = {
    "activate_bootstrap": (
        "organization",
        {"external_identity": {"provider": "stepik", "issuer": "issuer", "subject": "one"}},
    ),
    "create_invitation": (
        "organization",
        {"email": "reviewer@example.com", "role": "reviewer", "expires_at": DATE},
    ),
    "revoke_invitation": ("invitation", {"reason": "withdrawn"}),
    "start_course_import": (
        "organization",
        {"provider": "stepik", "external_url": "https://stepik.org/course/1"},
    ),
    "archive_course": ("course", {"reason": "complete"}),
    "restore_course": ("course", {}),
    "archive_course_run": ("course_run", {"reason": "complete"}),
    "restore_course_run": ("course_run", {}),
    "change_membership_roles": ("membership", {"roles": ["reviewer"]}),
    "create_homework": ("course_run", {"title": "Homework"}),
    "create_homework_version": (
        "homework",
        {
            "student_text": "Do the work",
            "max_score": 10,
            "artifact_kinds": ["github"],
            "estimated_review_minutes": 30,
            "criteria": [
                {"key": "correct", "title": "Correct", "description": "Works", "max_points": 10}
            ],
        },
    ),
    "publish_homework_version": (
        "homework_version",
        {"course_run_id": UUID_2, "submission_deadline": DATE, "review_deadline": DATE},
    ),
    "set_reviewer_course_selection": ("membership", {"course_run_ids": [UUID_2]}),
    "set_reviewer_availability": (
        "membership",
        {"planned_minutes": 120, "until_at": DATE},
    ),
    "preflight_submission": (
        "course_run_homework",
        {"artifact_url": "https://github.com/example/repository"},
    ),
    "submit_work": ("submission", {"artifact_reference_id": UUID_2}),
    "open_review_iteration": ("review_case", {"submission_version_id": UUID_2}),
    "migrate_review_requirements": (
        "review_iteration",
        {"homework_version_id": UUID, "criterion_set_id": UUID_2},
    ),
    "create_review_correction": (
        "review_iteration",
        {"published_review_revision_id": UUID_2, "reason": "correct score"},
    ),
    "record_review_responsibility": ("review_iteration", {"action": "joined"}),
    "save_review_revision": (
        "review_iteration",
        {
            "feedback": "Useful feedback",
            "criterion_decisions": [
                {
                    "criterion_id": UUID_2,
                    "points": 5,
                    "decision": "manual",
                    "reason": "Checked",
                }
            ],
            "review_notes": [{"criterion_id": None, "text": "Overall note"}],
        },
    ),
    "request_review_publication": (
        "review_iteration",
        {"review_revision_id": UUID_2, "expires_at": DATE},
    ),
    "publish_review": (
        "review_iteration",
        {"review_revision_id": UUID_2, "publication_request_id": None},
    ),
    "start_ai_review": ("review_iteration", {}),
    "retry_delivery": ("external_delivery", {"reconcile_first": True}),
    "grant_agent_authorization": (
        "membership",
        {"agent_id": UUID_2, "scopes": ["reviews:read"], "expires_at": DATE},
    ),
    "revoke_agent_authorization": ("agent_authorization", {"reason": "no longer needed"}),
    "recover_methodologist": (
        "organization",
        {
            "organization_id": UUID,
            "provider": "stepik",
            "issuer": "https://stepik.org",
            "subject": "operator-selected-user",
            "reason": "recover access",
        },
    ),
}


def wire_command(command_name: str) -> dict[str, Any]:
    revision_target, payload = PAYLOADS[command_name]
    return {
        "request_id": UUID,
        "idempotency_key": f"fixture-{command_name}-0001",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": UUID_2,
        "expected_revision": 0,
        "payload": deepcopy(payload),
    }


@pytest.mark.parametrize("command_name", sorted(PAYLOADS))
def test_all_twenty_eight_wire_command_variants_validate(command_name: str) -> None:
    schema = load_schema("command.schema.json")
    assert len(PAYLOADS) == 28

    validate_definition(schema, command_name, wire_command(command_name))


@pytest.mark.parametrize("command_name", sorted(PAYLOADS))
def test_each_variant_rejects_wrong_revision_target(command_name: str) -> None:
    schema = load_schema("command.schema.json")
    command = wire_command(command_name)
    command["revision_target"] = "publication_request"
    if PAYLOADS[command_name][0] == "publication_request":
        command["revision_target"] = "organization"

    with pytest.raises(ValidationError):
        validate_definition(schema, command_name, command)


@pytest.mark.parametrize("command_name", sorted(PAYLOADS))
def test_conditional_payloads_are_closed(command_name: str) -> None:
    schema = load_schema("command.schema.json")
    command = wire_command(command_name)
    command["payload"]["unexpected"] = True

    with pytest.raises(ValidationError):
        validate_definition(schema, command_name, command)


def test_wire_command_rejects_server_owned_context_and_unknown_fields() -> None:
    schema = load_schema("command.schema.json")
    command = wire_command("create_invitation")
    command.update({"organization_id": UUID, "actor": {"type": "user"}})

    with pytest.raises(ValidationError):
        validate_definition(schema, "wire", command)


@pytest.mark.parametrize("command_name", ["activate_bootstrap", "recover_methodologist"])
def test_operator_commands_require_operator_actor_and_transport(command_name: str) -> None:
    schema = load_schema("command.schema.json")
    command = wire_command(command_name)
    command.update(
        {
            "organization_id": UUID,
            "actor": {
                "type": "installation_operator",
                "installation_operator_id": "local-operator",
                "reason": "authorized recovery",
            },
            "transport": "operator",
            "trace_id": UUID_2,
        }
    )
    validate_definition(schema, "application", command)

    command["transport"] = "rest"
    with pytest.raises(ValidationError):
        validate_definition(schema, "application", command)


@pytest.mark.parametrize(
    "command_name", ["publish_review", "grant_agent_authorization", "revoke_agent_authorization"]
)
def test_human_only_commands_require_user_actor_over_rest(command_name: str) -> None:
    schema = load_schema("command.schema.json")
    command = wire_command(command_name)
    command.update(
        {
            "organization_id": UUID,
            "actor": {
                "type": "user",
                "user_id": UUID,
                "membership_revision": 1,
                "auth_epoch": 2,
            },
            "transport": "rest",
            "trace_id": UUID_2,
        }
    )
    validate_definition(schema, "application", command)

    command["actor"] = {
        "type": "agent",
        "user_id": UUID,
        "membership_revision": 1,
        "auth_epoch": 2,
        "agent_id": UUID_2,
        "agent_authorization_id": UUID_2,
    }
    command["transport"] = "mcp"
    with pytest.raises(ValidationError):
        validate_definition(schema, "application", command)


def test_contract_declares_draft_2020_12_and_version_1_1_0() -> None:
    schema = load_schema("command.schema.json")
    manifest = load_json(CONTRACT_ROOT / "manifest.json")

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:review-platform:command:1.1.0"
    assert schema["x-contract-status"] == manifest["status"]
