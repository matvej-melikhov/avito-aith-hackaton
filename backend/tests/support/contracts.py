"""Narrow loaders and JSON-Schema helpers shared by contract tests."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator

BACKEND_ROOT = Path.cwd().resolve()
if BACKEND_ROOT.name != "backend":
    raise RuntimeError(
        "contract tests must run with the documented `uv run --directory backend` command"
    )
REPOSITORY_ROOT = BACKEND_ROOT.parent
FEATURE_ROOT = REPOSITORY_ROOT / "specs" / "001-backend-core"
CONTRACT_ROOT = FEATURE_ROOT / "contracts"
FIXTURE_ROOT = FEATURE_ROOT / "contract-fixtures"
CONSTITUTION = REPOSITORY_ROOT / ".specify" / "memory" / "constitution.md"

JsonObject = dict[str, Any]


def load_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected object in {path}")
    return value


def load_schema(name: str) -> JsonObject:
    return load_json(CONTRACT_ROOT / name)


def load_fixture(name: str) -> JsonObject:
    return load_json(FIXTURE_ROOT / name)


def load_openapi() -> JsonObject:
    value = yaml.safe_load((CONTRACT_ROOT / "openapi.yaml").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("OpenAPI document must be an object")
    return value


def validator_for(schema: Mapping[str, Any]) -> Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_definition(schema: JsonObject, definition: str, instance: Any) -> None:
    wrapper: JsonObject = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/$defs/{definition}",
        "$defs": schema["$defs"],
    }
    validator_for(wrapper).validate(instance)


def walk_json(value: object) -> Iterator[object]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def external_refs(value: object) -> Iterable[str]:
    for node in walk_json(value):
        if isinstance(node, dict) and isinstance(node.get("$ref"), str):
            reference = node["$ref"]
            if not reference.startswith("#"):
                yield reference


def resolve_fragment(document: object, fragment: str) -> object:
    if not fragment or fragment == "#":
        return document
    if not fragment.startswith("#/"):
        raise ValueError(f"unsupported JSON pointer: {fragment}")
    current = document
    for escaped in fragment[2:].split("/"):
        token = escaped.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            raise KeyError(fragment)
        current = current[token]
    return current


def operation_index(openapi: JsonObject) -> dict[str, tuple[str, str, JsonObject]]:
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    result: dict[str, tuple[str, str, JsonObject]] = {}
    for path, item in openapi["paths"].items():
        for method, operation in item.items():
            if method in methods:
                result[operation["operationId"]] = (method, path, operation)
    return result
