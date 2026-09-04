from __future__ import annotations

import hashlib
from pathlib import Path

from tests.support.contracts import (
    CONSTITUTION,
    CONTRACT_ROOT,
    FEATURE_ROOT,
    load_json,
    load_openapi,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_pins_all_nineteen_artifacts_for_the_declared_lifecycle_state() -> None:
    manifest = load_json(CONTRACT_ROOT / "manifest.json")

    assert manifest["contractSetVersion"] == "1.1.0"
    assert manifest["status"] in {"candidate", "frozen"}
    assert manifest["sourceAuthority"] == "specs/001-backend-core/contracts"
    assert len(manifest["files"]) == 19
    assert len({item["path"] for item in manifest["files"]}) == 19

    for item in manifest["files"]:
        artifact = (CONTRACT_ROOT / item["path"]).resolve()
        assert artifact.is_file(), item["path"]
        assert _sha256(artifact) == item["sha256"], item["path"]

    for schema_path in CONTRACT_ROOT.glob("*.schema.json"):
        assert load_json(schema_path)["x-contract-status"] == manifest["status"], schema_path.name
    assert load_openapi()["x-contract-status"] == manifest["status"]
    assert load_json(CONTRACT_ROOT / "mcp-tools.json")["contractStatus"] == manifest["status"]


def test_constitution_snapshot_is_byte_identical_to_authority() -> None:
    manifest = load_json(CONTRACT_ROOT / "manifest.json")
    snapshot = FEATURE_ROOT / "constitution-snapshot.md"

    assert snapshot.read_bytes() == CONSTITUTION.read_bytes()
    assert _sha256(CONSTITUTION) == manifest["authoritativeConstitutionSha256"]
    assert manifest["authoritativeConstitutionPath"] == ".specify/memory/constitution.md"


def test_compatibility_notes_describe_breaking_candidate_and_migration_rule() -> None:
    notes = (FEATURE_ROOT / "contract-compatibility.md").read_text(encoding="utf-8")

    assert "# Contract compatibility: 1.0.0 → 1.1.0" in notes
    assert "## Breaking wire changes" in notes
    assert "## Migration rule" in notes
    assert "unsupported_contract_version" in notes
    assert "no persisted runtime data migration" in notes
