"""Offline provider adapters backed only by the frozen shared fixtures.

The adapters never perform network I/O.  They validate both the incoming
request and the selected fixture response before returning a defensive copy.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from review_platform.application.ports.providers import (
    ProviderContractError,
    ProviderPayload,
    ProviderPayloadValidator,
)

if TYPE_CHECKING:
    from review_platform.contracts.registry import ContractRegistry

_FIXTURE_DIRECTORY: Final = "contract-fixtures"


def _repository_fixture_root() -> Path:
    """Locate source fixtures without making the path a production side channel."""

    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "specs" / "001-backend-core" / _FIXTURE_DIRECTORY
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "frozen provider fixtures are unavailable; pass fixture_root explicitly"
    )


@dataclass(frozen=True, slots=True)
class JsonSchemaPayloadValidator:
    """Draft 2020-12 validator for packaged, frozen provider schemas."""

    registry: ContractRegistry = field(default_factory=lambda: _contract_registry())

    def validate(
        self,
        *,
        schema_name: str,
        definition: str,
        payload: ProviderPayload,
    ) -> None:
        try:
            self.registry.validate(payload, schema_name, definition=definition)
        except Exception as exc:
            raise ProviderContractError(
                f"payload violates {schema_name}#/$defs/{definition}"
            ) from exc


def _contract_registry() -> ContractRegistry:
    # Keep mock-only JSON Schema tooling out of production adapter imports.
    from review_platform.contracts.registry import ContractRegistry

    return ContractRegistry()


@dataclass(frozen=True, slots=True)
class FrozenFixtureStore:
    """Load named JSON objects only from the frozen fixture directory."""

    root: Path = field(default_factory=_repository_fixture_root)

    def load(self, fixture_name: str) -> dict[str, ProviderPayload]:
        path = (self.root / fixture_name).resolve()
        if path.parent != self.root.resolve() or path.suffix != ".json":
            raise ProviderContractError("fixture name escapes the frozen fixture directory")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not all(isinstance(value, dict) for value in raw.values()):
            raise ProviderContractError(f"fixture {fixture_name} is not an object map")
        return cast(dict[str, ProviderPayload], raw)


@dataclass(slots=True)
class _FixtureAdapter:
    fixture_name: str
    schema_name: str
    validator: ProviderPayloadValidator = field(default_factory=JsonSchemaPayloadValidator)
    store: FrozenFixtureStore = field(default_factory=FrozenFixtureStore)
    outcomes: Mapping[str, str] = field(default_factory=dict)

    @property
    def contract_version(self) -> str:
        return "1.1.0"

    def _exchange(
        self,
        *,
        request: ProviderPayload,
        request_key: str,
        request_definition: str,
        default_result_key: str,
        result_definition: str,
    ) -> ProviderPayload:
        fixture = self.store.load(self.fixture_name)
        self.validator.validate(
            schema_name=self.schema_name,
            definition=request_definition,
            payload=request,
        )
        fixture_request = fixture[request_key]
        self.validator.validate(
            schema_name=self.schema_name,
            definition=request_definition,
            payload=fixture_request,
        )
        if dict(request) != dict(fixture_request):
            raise ProviderContractError(
                f"offline mock accepts only frozen request {self.fixture_name}:{request_key}"
            )
        result_key = self.outcomes.get(request_key, default_result_key)
        try:
            result = fixture[result_key]
        except KeyError as exc:
            raise ProviderContractError(
                f"unknown fixture outcome {self.fixture_name}:{result_key}"
            ) from exc
        self.validator.validate(
            schema_name=self.schema_name,
            definition=result_definition,
            payload=result,
        )
        return cast(ProviderPayload, deepcopy(dict(result)))


class FixtureIdentityProvider(_FixtureAdapter):
    def __init__(
        self,
        *,
        validator: ProviderPayloadValidator | None = None,
        store: FrozenFixtureStore | None = None,
        outcome: str = "success_assertion",
    ) -> None:
        super().__init__(
            "identity-provider-v1.1.0.json",
            "identity-provider.schema.json",
            validator or JsonSchemaPayloadValidator(),
            store or FrozenFixtureStore(),
            {"request": outcome},
        )

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload:
        definition = "failure" if self.outcomes["request"] == "failure" else "identity_assertion"
        return self._exchange(
            request=request,
            request_key="request",
            request_definition="verification_request",
            default_result_key="success_assertion",
            result_definition=definition,
        )


class FixtureCourseImportProvider(_FixtureAdapter):
    def __init__(
        self,
        *,
        validator: ProviderPayloadValidator | None = None,
        store: FrozenFixtureStore | None = None,
        outcome: str = "success_result",
    ) -> None:
        super().__init__(
            "course-import-v1.1.0.json",
            "course-import.schema.json",
            validator or JsonSchemaPayloadValidator(),
            store or FrozenFixtureStore(),
            {"request": outcome},
        )

    async def import_course_page(self, request: ProviderPayload) -> ProviderPayload:
        return self._exchange(
            request=request,
            request_key="request",
            request_definition="request",
            default_result_key="success_result",
            result_definition="result",
        )


class FixtureArtifactProvider(_FixtureAdapter):
    def __init__(
        self,
        *,
        validator: ProviderPayloadValidator | None = None,
        store: FrozenFixtureStore | None = None,
        preflight_outcome: str = "available_result",
        capture_outcome: str = "success_result",
    ) -> None:
        super().__init__(
            "artifact-provider-v1.1.0.json",
            "artifact-provider.schema.json",
            validator or JsonSchemaPayloadValidator(),
            store or FrozenFixtureStore(),
            {"preflight_request": preflight_outcome, "capture_request": capture_outcome},
        )

    async def preflight(self, request: ProviderPayload) -> ProviderPayload:
        return self._exchange(
            request=request,
            request_key="preflight_request",
            request_definition="preflight_request",
            default_result_key="available_result",
            result_definition="preflight_result",
        )

    async def capture(self, request: ProviderPayload) -> ProviderPayload:
        return self._exchange(
            request=request,
            request_key="capture_request",
            request_definition="capture_request",
            default_result_key="success_result",
            result_definition="capture_result",
        )


class FixtureDeliveryProvider(_FixtureAdapter):
    def __init__(
        self,
        *,
        validator: ProviderPayloadValidator | None = None,
        store: FrozenFixtureStore | None = None,
        deliver_outcome: str = "success_result",
        reconcile_outcome: str = "reconcile_found_result",
    ) -> None:
        super().__init__(
            "delivery-v1.1.0.json",
            "delivery.schema.json",
            validator or JsonSchemaPayloadValidator(),
            store or FrozenFixtureStore(),
            {"request": deliver_outcome, "reconcile_request": reconcile_outcome},
        )

    async def deliver(self, request: ProviderPayload) -> ProviderPayload:
        return self._exchange(
            request=request,
            request_key="request",
            request_definition="deliver_request",
            default_result_key="success_result",
            result_definition="result",
        )

    async def reconcile(self, request: ProviderPayload) -> ProviderPayload:
        return self._exchange(
            request=request,
            request_key="reconcile_request",
            request_definition="reconcile_request",
            default_result_key="reconcile_found_result",
            result_definition="result",
        )


class FixtureEmailProvider(_FixtureAdapter):
    def __init__(
        self,
        *,
        validator: ProviderPayloadValidator | None = None,
        store: FrozenFixtureStore | None = None,
        outcome: str = "success_result",
    ) -> None:
        super().__init__(
            "email-v1.1.0.json",
            "email.schema.json",
            validator or JsonSchemaPayloadValidator(),
            store or FrozenFixtureStore(),
            {"request": outcome},
        )

    async def send(self, request: ProviderPayload) -> ProviderPayload:
        return self._exchange(
            request=request,
            request_key="request",
            request_definition="send_request",
            default_result_key="success_result",
            result_definition="send_result",
        )


@dataclass(frozen=True, slots=True)
class FixtureAIEventSource:
    """Validated deterministic AI events for backend ingestion tests."""

    validator: ProviderPayloadValidator = field(default_factory=JsonSchemaPayloadValidator)
    store: FrozenFixtureStore = field(default_factory=FrozenFixtureStore)

    def event(self, outcome: str = "succeeded") -> ProviderPayload:
        fixture = self.store.load("ai-events-v1.1.0.json")
        try:
            event = fixture[outcome]
        except KeyError as exc:
            raise ProviderContractError(f"unknown AI fixture outcome: {outcome}") from exc
        self.validator.validate(
            schema_name="ai-review.schema.json",
            definition="event",
            payload=event,
        )
        return cast(ProviderPayload, deepcopy(dict(event)))

    def events(self) -> tuple[ProviderPayload, ...]:
        fixture = self.store.load("ai-events-v1.1.0.json")
        return tuple(self.event(outcome) for outcome in fixture)


# Friendly aliases for callers that use "mock" rather than "fixture" terminology.
MockIdentityProvider = FixtureIdentityProvider
MockCourseImportProvider = FixtureCourseImportProvider
MockArtifactProvider = FixtureArtifactProvider
MockDeliveryProvider = FixtureDeliveryProvider
MockEmailProvider = FixtureEmailProvider

__all__ = [
    "FixtureAIEventSource",
    "FixtureArtifactProvider",
    "FixtureCourseImportProvider",
    "FixtureDeliveryProvider",
    "FixtureEmailProvider",
    "FixtureIdentityProvider",
    "FrozenFixtureStore",
    "JsonSchemaPayloadValidator",
    "MockArtifactProvider",
    "MockCourseImportProvider",
    "MockDeliveryProvider",
    "MockEmailProvider",
    "MockIdentityProvider",
]
