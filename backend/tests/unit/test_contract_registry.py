from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from review_platform.contracts.commands import (
    AgentActor,
    ApplicationCommand,
    InstallationOperatorActor,
    UserActor,
    WireCommand,
)
from review_platform.contracts.registry import (
    CONTRACT_VERSION,
    MANIFEST_SHA256,
    ContractRegistry,
    UnknownContractReference,
    UnsupportedContractVersion,
)

ROOT = Path(__file__).resolve().parents[3]
CANONICAL = ROOT / "specs" / "001-backend-core" / "contracts"
UUID_1 = "00000000-0000-7000-8000-000000000001"
UUID_2 = "00000000-0000-7000-8000-000000000002"


def test_runtime_schemas_are_byte_identical_and_manifest_verified() -> None:
    registry = ContractRegistry()
    registry.verify_packaged_schemas()

    packaged = resources.files("review_platform.contracts.schemas")
    for path in CANONICAL.glob("*.schema.json"):
        assert packaged.joinpath(path.name).read_bytes() == path.read_bytes()
    assert packaged.joinpath("manifest.json").read_bytes() == (
        CANONICAL / "manifest.json"
    ).read_bytes()
    assert hashlib.sha256((CANONICAL / "manifest.json").read_bytes()).hexdigest() == MANIFEST_SHA256


def test_registry_is_fail_closed_for_versions_names_and_references() -> None:
    registry = ContractRegistry()

    assert registry.version == CONTRACT_VERSION == "1.1.0"
    with pytest.raises(UnsupportedContractVersion):
        registry.load_schema("command.schema.json", version="1.0.0")
    with pytest.raises(UnknownContractReference):
        registry.load_schema("missing.schema.json")
    with pytest.raises(UnknownContractReference):
        registry.resolve_reference("artifact.schema.json#/$defs/missing")


def test_registry_resolves_external_refs_and_validates_pydantic_values() -> None:
    registry = ContractRegistry()
    artifact = {
        "contract_version": "1.1.0",
        "organization_id": UUID_1,
        "artifact_reference_id": UUID_1,
        "artifact_version_id": UUID_2,
        "provider": "github",
        "provider_version": "commit:abc",
        "content_digest": "sha256:" + "0" * 64,
        "captured_at": "2026-09-04T12:00:00Z",
        "object": {"key": "tenant/artifact", "media_type": "text/plain", "byte_size": 1},
    }
    registry.validate(artifact, "artifact.schema.json")
    assert registry.resolve_reference("artifact.schema.json#/properties/object")["type"] == "object"

    command = WireCommand.model_validate(
        {
            "request_id": UUID_1,
            "idempotency_key": "archive-course-0001",
            "command_name": "archive_course",
            "revision_target": "course",
            "target_id": UUID_2,
            "expected_revision": 2,
            "payload": {"reason": "complete"},
        }
    )
    registry.validate_pydantic(command, "command.schema.json", definition="wire")
    generated = registry.assert_generated_schema_conforms(
        WireCommand,
        "command.schema.json",
        definition="wire",
        samples=[command.model_dump(mode="json")],
    )
    assert generated["type"] == "object"


def test_wire_and_application_commands_reject_unknown_fields_and_policy_violations() -> None:
    base = {
        "request_id": UUID_1,
        "idempotency_key": "publish-review-0001",
        "command_name": "publish_review",
        "revision_target": "review_iteration",
        "target_id": UUID_2,
        "expected_revision": 2,
        "payload": {"review_revision_id": UUID_1},
    }
    with pytest.raises(ValidationError):
        WireCommand.model_validate({**base, "actor": {"type": "user"}})

    with pytest.raises(ValidationError):
        ApplicationCommand.model_validate(
            {
                **base,
                "organization_id": UUID_1,
                "trace_id": UUID_2,
                "transport": "mcp",
                "actor": {
                    "type": "agent",
                    "user_id": UUID_1,
                    "membership_revision": 1,
                    "auth_epoch": 1,
                    "agent_id": UUID_2,
                    "agent_authorization_id": UUID_2,
                },
            }
        )

    actor = UserActor.model_validate(
        {"type": "user", "user_id": UUID_1, "membership_revision": 1, "auth_epoch": 2}
    )
    assert actor.type == "user"
    with pytest.raises(ValidationError):
        UserActor.model_validate(
            {"user_id": UUID_1, "membership_revision": 1, "auth_epoch": 2}
        )
    assert AgentActor.model_fields["type"].is_required()
    assert InstallationOperatorActor.model_fields["type"].is_required()
