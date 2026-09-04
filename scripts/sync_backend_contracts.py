#!/usr/bin/env python3
"""Verify or mechanically freeze the backend-core canonical contract set."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path.cwd().resolve()
FEATURE_ROOT = ROOT / "specs" / "001-backend-core"
CONTRACT_ROOT = FEATURE_ROOT / "contracts"
MANIFEST_PATH = CONTRACT_ROOT / "manifest.json"
CONSTITUTION_PATH = ROOT / ".specify" / "memory" / "constitution.md"
SNAPSHOT_PATH = FEATURE_ROOT / "constitution-snapshot.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one {old!r} in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def manifest() -> dict[str, Any]:
    return load_json(MANIFEST_PATH)


def verify() -> None:
    current = manifest()
    status = current["status"]
    if status not in {"candidate", "frozen"}:
        raise RuntimeError(f"unsupported contract lifecycle status: {status!r}")
    if current["contractSetVersion"] != "1.1.0":
        raise RuntimeError("T022 may freeze only contract set 1.1.0")

    entries = current["files"]
    paths = [entry["path"] for entry in entries]
    if len(entries) != 19 or len(set(paths)) != 19:
        raise RuntimeError("manifest must contain exactly 19 unique artifacts")

    for entry in entries:
        artifact = (CONTRACT_ROOT / entry["path"]).resolve()
        if not artifact.is_file():
            raise FileNotFoundError(artifact)
        actual = sha256(artifact)
        if actual != entry["sha256"]:
            raise RuntimeError(f"hash mismatch for {entry['path']}: {actual}")

    for schema_path in sorted(CONTRACT_ROOT.glob("*.schema.json")):
        if load_json(schema_path)["x-contract-status"] != status:
            raise RuntimeError(f"lifecycle status mismatch: {schema_path}")
    if load_json(CONTRACT_ROOT / "mcp-tools.json")["contractStatus"] != status:
        raise RuntimeError("lifecycle status mismatch: mcp-tools.json")

    openapi_text = (CONTRACT_ROOT / "openapi.yaml").read_text(encoding="utf-8")
    if f"x-contract-status: {status}" not in openapi_text:
        raise RuntimeError("lifecycle status mismatch: openapi.yaml")

    if CONSTITUTION_PATH.read_bytes() != SNAPSHOT_PATH.read_bytes():
        raise RuntimeError("constitution snapshot differs from authority")
    if sha256(CONSTITUTION_PATH) != current["authoritativeConstitutionSha256"]:
        raise RuntimeError("authoritative constitution hash mismatch")


def update_manifest_hashes() -> None:
    current = manifest()
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    for entry in current["files"]:
        artifact = (CONTRACT_ROOT / entry["path"]).resolve()
        actual = sha256(artifact)
        pattern = re.compile(
            rf'("path": {re.escape(json.dumps(entry["path"]))}, "sha256": ")[0-9a-f]{{64}}(")'
        )
        text, count = pattern.subn(rf"\g<1>{actual}\2", text, count=1)
        if count != 1:
            raise RuntimeError(f"could not update manifest hash for {entry['path']}")
    MANIFEST_PATH.write_text(text, encoding="utf-8")


def freeze() -> None:
    verify()
    current = manifest()
    if current["status"] == "frozen":
        print("backend contract set 1.1.0 is already frozen and verified")
        return

    for schema_path in sorted(CONTRACT_ROOT.glob("*.schema.json")):
        replace_once(
            schema_path,
            '"x-contract-status": "candidate"',
            '"x-contract-status": "frozen"',
        )
    replace_once(
        CONTRACT_ROOT / "mcp-tools.json",
        '"contractStatus": "candidate"',
        '"contractStatus": "frozen"',
    )
    replace_once(
        CONTRACT_ROOT / "openapi.yaml",
        "x-contract-status: candidate",
        "x-contract-status: frozen",
    )
    replace_once(MANIFEST_PATH, '"status": "candidate"', '"status": "frozen"')
    update_manifest_hashes()
    verify()
    print("backend contract set 1.1.0 frozen; 19 artifact hashes verified")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true", help="freeze candidate 1.1.0")
    parser.add_argument("--verify", action="store_true", help="verify hashes and statuses")
    args = parser.parse_args()
    if args.freeze == args.verify:
        parser.error("choose exactly one of --freeze or --verify")
    return args


def main() -> None:
    if not (ROOT / ".git").exists() or not MANIFEST_PATH.is_file():
        raise RuntimeError("run this command from the repository root")
    args = parse_args()
    if args.freeze:
        freeze()
    else:
        verify()
        print("backend contract set 1.1.0 verified")


if __name__ == "__main__":
    main()
