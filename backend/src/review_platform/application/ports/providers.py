"""Typed application ports for versioned external-provider contracts.

The wire schemas remain the source of truth.  These protocols deliberately
transport JSON-shaped values: an adapter must validate the request and result
against the named frozen schema definition at its boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type ProviderPayload = Mapping[str, JsonValue]

CONTRACT_VERSION = "1.1.0"


class ProviderContractError(ValueError):
    """A provider request or result violated its frozen wire contract."""


@runtime_checkable
class ProviderPayloadValidator(Protocol):
    """Validate one payload against a definition in a frozen JSON Schema."""

    def validate(
        self,
        *,
        schema_name: str,
        definition: str,
        payload: ProviderPayload,
    ) -> None: ...


@runtime_checkable
class SchemaBackedProvider(Protocol):
    """Expose the exact frozen schema binding used by an adapter."""

    @property
    def contract_version(self) -> str: ...

    @property
    def schema_name(self) -> str: ...


@runtime_checkable
class IdentityProvider(SchemaBackedProvider, Protocol):
    """Verify an OAuth or magic-link response into a typed identity assertion."""

    async def verify_identity(self, request: ProviderPayload) -> ProviderPayload: ...


@runtime_checkable
class CourseImportProvider(SchemaBackedProvider, Protocol):
    """Read one bounded page of a provider course and its roster."""

    async def import_course_page(self, request: ProviderPayload) -> ProviderPayload: ...


@runtime_checkable
class ArtifactProvider(SchemaBackedProvider, Protocol):
    """Preflight and capture immutable external artifacts."""

    async def preflight(self, request: ProviderPayload) -> ProviderPayload: ...

    async def capture(self, request: ProviderPayload) -> ProviderPayload: ...


@runtime_checkable
class DeliveryProvider(SchemaBackedProvider, Protocol):
    """Deliver a publication or reconcile an outcome that may be ambiguous."""

    async def deliver(self, request: ProviderPayload) -> ProviderPayload: ...

    async def reconcile(self, request: ProviderPayload) -> ProviderPayload: ...


@runtime_checkable
class EmailProvider(SchemaBackedProvider, Protocol):
    """Send an email described by the frozen email contract."""

    async def send(self, request: ProviderPayload) -> ProviderPayload: ...


@runtime_checkable
class AIReviewProvider(SchemaBackedProvider, Protocol):
    """Start an AI review against an immutable, fully versioned input."""

    async def start_review(self, request: ProviderPayload) -> ProviderPayload: ...


__all__ = [
    "CONTRACT_VERSION",
    "AIReviewProvider",
    "ArtifactProvider",
    "CourseImportProvider",
    "DeliveryProvider",
    "EmailProvider",
    "IdentityProvider",
    "JsonScalar",
    "JsonValue",
    "ProviderContractError",
    "ProviderPayload",
    "ProviderPayloadValidator",
    "SchemaBackedProvider",
]
