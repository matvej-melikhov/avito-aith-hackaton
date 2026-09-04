"""Fail-closed access to the packaged frozen contract set."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from importlib import resources
from typing import Any, TypeVar

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator
from pydantic import BaseModel
from referencing import Registry, Resource

CONTRACT_VERSION = "1.1.0"
CONTRACT_STATUS = "frozen"
SCHEMA_PACKAGE = "review_platform.contracts.schemas"
MANIFEST_SHA256 = "3fa4d81cfb6bc390b97656168989b5acc563844130eaca9c1a16d32bfd6c547f"

JsonObject = dict[str, Any]
ModelT = TypeVar("ModelT", bound=BaseModel)


class ContractRegistryError(RuntimeError):
    """Base error for a contract registry that refuses ambiguous input."""


class UnsupportedContractVersion(ContractRegistryError):
    """The caller requested a contract version that is not packaged."""


class UnknownContractReference(ContractRegistryError):
    """A schema, definition, or JSON reference is not in the frozen set."""


class ContractIntegrityError(ContractRegistryError):
    """A packaged resource differs from its frozen manifest entry."""


class GeneratedSchemaConformanceError(ContractRegistryError):
    """A generated model schema is not bound to the requested frozen contract."""


class ContractRegistry:
    """Load and validate only the manifest-pinned 1.1.0 runtime schemas.

    The registry deliberately has no repository-path fallback. A wheel therefore
    validates exactly the resources that were built into that wheel.
    """

    def __init__(self, version: str = CONTRACT_VERSION) -> None:
        self._require_version(version)
        self._version = version
        manifest_bytes = self._read_resource_bytes("manifest.json")
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_digest != MANIFEST_SHA256:
            raise ContractIntegrityError(
                f"packaged manifest digest mismatch: {manifest_digest}"
            )
        self._manifest = self._decode_json(manifest_bytes, "manifest.json")
        self._schema_names = self._manifest_schema_names()
        self.verify_packaged_schemas()
        self._schemas = {name: self._read_json_resource(name) for name in self._schema_names}
        self._reference_registry = self._build_reference_registry()

    @property
    def version(self) -> str:
        return self._version

    @property
    def manifest(self) -> JsonObject:
        return deepcopy(self._manifest)

    @property
    def schema_names(self) -> tuple[str, ...]:
        return self._schema_names

    @classmethod
    def for_version(cls, version: str) -> ContractRegistry:
        return cls(version=version)

    def load_schema(self, name: str, *, version: str | None = None) -> JsonObject:
        self._require_version(version or self._version)
        normalized = self._normalize_schema_name(name)
        try:
            return deepcopy(self._schemas[normalized])
        except KeyError as error:
            raise UnknownContractReference(f"unknown contract schema: {name!r}") from error

    schema = load_schema

    def resolve_reference(
        self,
        reference: str,
        *,
        from_schema: str | None = None,
        version: str | None = None,
    ) -> Any:
        """Resolve a packaged file/URN reference and optional JSON Pointer."""

        self._require_version(version or self._version)
        document_ref, separator, fragment = reference.partition("#")
        if not document_ref:
            if from_schema is None:
                raise UnknownContractReference("a local reference requires from_schema")
            document = self.load_schema(from_schema)
        else:
            document = self._document_for_reference(document_ref)

        if not separator or fragment == "":
            return document
        if not fragment.startswith("/"):
            raise UnknownContractReference(f"unsupported contract fragment: #{fragment}")

        current: Any = document
        for encoded_part in fragment[1:].split("/"):
            part = encoded_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping) and part in current:
                current = current[part]
                continue
            if isinstance(current, list):
                try:
                    current = current[int(part)]
                    continue
                except (IndexError, ValueError):
                    pass
            raise UnknownContractReference(f"unknown contract reference: {reference!r}")
        return deepcopy(current)

    resolve_ref = resolve_reference

    def validator(
        self,
        schema_name: str,
        *,
        definition: str | None = None,
        version: str | None = None,
    ) -> Validator:
        schema = self.load_schema(schema_name, version=version)
        if definition is not None:
            definitions = schema.get("$defs")
            if not isinstance(definitions, Mapping) or definition not in definitions:
                raise UnknownContractReference(
                    f"unknown definition {definition!r} in {schema_name!r}"
                )
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str):
                raise ContractIntegrityError(f"schema {schema_name!r} has no $id")
            schema = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$ref": f"{schema_id}#/$defs/{definition}",
            }
        return Draft202012Validator(
            schema,
            registry=self._reference_registry,
            format_checker=FormatChecker(),
        )

    def validate(
        self,
        value: Any,
        schema_name: str,
        *,
        definition: str | None = None,
        version: str | None = None,
    ) -> None:
        self.validator(schema_name, definition=definition, version=version).validate(value)

    def validate_pydantic(
        self,
        value: BaseModel,
        schema_name: str,
        *,
        definition: str | None = None,
        version: str | None = None,
    ) -> None:
        """Validate a model's JSON representation against the canonical schema."""

        self.validate(
            value.model_dump(mode="json", exclude_unset=True),
            schema_name,
            definition=definition,
            version=version,
        )

    def assert_generated_schema_conforms(
        self,
        model: type[ModelT],
        schema_name: str,
        *,
        definition: str | None = None,
        samples: Iterable[Mapping[str, Any]] = (),
        version: str | None = None,
    ) -> JsonObject:
        """Check model binding and prove samples against model and canonical schema.

        Structural equality is intentionally not claimed: Pydantic and the
        hand-authored contract may encode equivalent constraints differently.
        Conformance is established by an explicit version/definition binding and
        shared vectors that both validators must accept.
        """

        selected_version = version or self._version
        self._require_version(selected_version)
        bound_schema = getattr(model, "__contract_schema__", None)
        bound_definition = getattr(model, "__contract_definition__", None)
        bound_version = getattr(model, "__contract_version__", None)
        if (bound_schema, bound_definition, bound_version) != (
            schema_name,
            definition,
            selected_version,
        ):
            raise GeneratedSchemaConformanceError(
                f"{model.__name__} is not bound to "
                f"{schema_name}#{definition or ''} at {selected_version}"
            )

        generated = model.model_json_schema(mode="validation")
        Draft202012Validator.check_schema(generated)
        for sample in samples:
            parsed = model.model_validate(sample)
            self.validate_pydantic(
                parsed,
                schema_name,
                definition=definition,
                version=selected_version,
            )
        return generated

    validate_generated_schema = assert_generated_schema_conforms

    def verify_packaged_schemas(self) -> None:
        if self._manifest.get("contractSetVersion") != CONTRACT_VERSION:
            raise ContractIntegrityError("packaged manifest has an unexpected contract version")
        if self._manifest.get("status") != CONTRACT_STATUS:
            raise ContractIntegrityError("packaged manifest is not frozen")

        packaged = resources.files(SCHEMA_PACKAGE)
        expected_names = set(self._manifest_schema_names())
        actual_names = {
            resource.name
            for resource in packaged.iterdir()
            if resource.is_file() and resource.name.endswith(".schema.json")
        }
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            unexpected = sorted(actual_names - expected_names)
            raise ContractIntegrityError(
                f"packaged schema inventory mismatch; missing={missing}, unexpected={unexpected}"
            )
        for entry in self._manifest.get("files", []):
            if not isinstance(entry, Mapping):
                raise ContractIntegrityError("manifest files must contain objects")
            path = entry.get("path")
            expected = entry.get("sha256")
            if (
                not isinstance(path, str)
                or not path.endswith(".schema.json")
                or path.startswith("../")
            ):
                continue
            if not isinstance(expected, str):
                raise ContractIntegrityError(f"missing digest for packaged schema {path!r}")
            try:
                data = packaged.joinpath(path).read_bytes()
            except FileNotFoundError as error:
                raise ContractIntegrityError(f"missing packaged schema {path!r}") from error
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                raise ContractIntegrityError(
                    f"packaged schema digest mismatch for {path!r}: {actual}"
                )
            schema = self._decode_json(data, path)
            if schema.get("x-contract-status") != CONTRACT_STATUS:
                raise ContractIntegrityError(f"packaged schema {path!r} is not frozen")
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id.endswith(f":{CONTRACT_VERSION}"):
                raise ContractIntegrityError(f"packaged schema {path!r} has wrong $id")
            Draft202012Validator.check_schema(schema)

    def _manifest_schema_names(self) -> tuple[str, ...]:
        names: list[str] = []
        entries = self._manifest.get("files")
        if not isinstance(entries, list):
            raise ContractIntegrityError("packaged manifest files must be a list")
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ContractIntegrityError("packaged manifest file entry must be an object")
            path = entry.get("path")
            if (
                isinstance(path, str)
                and path.endswith(".schema.json")
                and not path.startswith("../")
            ):
                names.append(path)
        if not names or len(names) != len(set(names)):
            raise ContractIntegrityError("packaged manifest schema paths are empty or duplicated")
        return tuple(sorted(names))

    def _build_reference_registry(self) -> Registry[Any]:
        registry: Registry[Any] = Registry()
        for name, schema in self._schemas.items():
            resource = Resource.from_contents(schema)
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str):
                raise ContractIntegrityError(f"schema {name!r} has no $id")
            registry = registry.with_resource(schema_id, resource)
            registry = registry.with_resource(name, resource)
        return registry

    def _document_for_reference(self, reference: str) -> JsonObject:
        if reference in self._schemas:
            return self.load_schema(reference)
        normalized = self._normalize_schema_name(reference)
        if normalized in self._schemas:
            return self.load_schema(normalized)
        for schema in self._schemas.values():
            if schema.get("$id") == reference:
                return deepcopy(schema)
        raise UnknownContractReference(f"unknown contract reference: {reference!r}")

    @staticmethod
    def _normalize_schema_name(name: str) -> str:
        if "/" in name or "\\" in name or name in {"", ".", ".."}:
            raise UnknownContractReference(f"invalid contract schema name: {name!r}")
        return name

    @staticmethod
    def _require_version(version: str) -> None:
        if version != CONTRACT_VERSION:
            raise UnsupportedContractVersion(
                f"unsupported contract version {version!r}; expected {CONTRACT_VERSION!r}"
            )

    @classmethod
    def _read_json_resource(cls, name: str) -> JsonObject:
        return cls._decode_json(cls._read_resource_bytes(name), name)

    @classmethod
    def _read_resource_bytes(cls, name: str) -> bytes:
        cls._normalize_schema_name(name)
        try:
            return resources.files(SCHEMA_PACKAGE).joinpath(name).read_bytes()
        except FileNotFoundError as error:
            raise ContractIntegrityError(f"missing packaged resource {name!r}") from error

    @staticmethod
    def _decode_json(data: bytes, name: str) -> JsonObject:
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ContractIntegrityError(f"invalid packaged JSON resource {name!r}") from error
        if not isinstance(value, dict):
            raise ContractIntegrityError(f"packaged resource {name!r} is not an object")
        return value


def load_schema(name: str, *, version: str = CONTRACT_VERSION) -> JsonObject:
    """Convenience loader using a verified short-lived registry."""

    return ContractRegistry(version=version).load_schema(name)
