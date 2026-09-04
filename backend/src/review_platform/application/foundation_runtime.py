"""Composition boundary exercised by the Foundation executable specifications.

This object may coordinate production components but must not own in-memory domain
state. T023--T044 replace the deliberate RED methods with adapters from the exact
modules asserted by the executable specifications.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn


class BoundaryViolation(ValueError):
    """A request crossed a declared command, actor, or tenant boundary."""


@dataclass(frozen=True, slots=True)
class OperationView:
    operation_id: str
    organization_id: str
    kind: str
    input_version: str
    state: str
    attempts: tuple[Mapping[str, Any], ...]
    error: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class Lease:
    message_id: str
    organization_id: str
    owner: str
    token: str
    expires_at: datetime


def _missing(capability: str) -> NoReturn:
    raise NotImplementedError(f"Foundation behavior not implemented: {capability}")


class FoundationRuntime:
    """Production composition boundary required by T017--T021."""

    def components(self) -> Mapping[str, object]:
        """Expose concrete composition for a no-test-double architecture assertion."""

        _missing("production Foundation component composition")

    def dispatch_rest(
        self,
        *,
        route_command: str,
        path_target_id: str | None,
        wire_command: Mapping[str, Any],
        authorization: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        _missing("REST command/target/actor binding")

    def dispatch_operator(
        self, *, command: Mapping[str, Any], operator_id: str, reason: str
    ) -> Mapping[str, Any]:
        _missing("local installation-operator command boundary")

    def reserve_command(
        self, *, organization_id: str, idempotency_key: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        _missing("tenant-scoped CommandReceipt idempotency")

    def create_operation(
        self, *, organization_id: str, kind: str, input_version: str
    ) -> OperationView:
        _missing("observable Operation creation")

    def transition_operation(
        self,
        *,
        organization_id: str,
        operation_id: str,
        state: str,
        error: Mapping[str, Any] | None = None,
    ) -> OperationView:
        _missing("legal Operation transition and terminal non-regression")

    def append_operation_attempt(
        self,
        *,
        organization_id: str,
        operation_id: str,
        outcome: str,
        error: Mapping[str, Any] | None = None,
    ) -> OperationView:
        _missing("ordered OperationAttempt history")

    async def enqueue_outbox(
        self,
        *,
        organization_id: str,
        message_id: str,
        payload: Mapping[str, Any],
        max_attempts: int,
    ) -> None:
        _missing("durable tenant-scoped OutboxMessage")

    async def lease_outbox(
        self,
        *,
        owner: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> Sequence[Lease]:
        _missing("multi-relay SKIP LOCKED leasing")

    async def complete_outbox(self, *, lease: Lease) -> None:
        _missing("lease-token compare-and-set completion")

    async def fail_outbox(
        self, *, lease: Lease, error: Mapping[str, Any], now: datetime
    ) -> Mapping[str, Any]:
        _missing("retry exhaustion and actionable poison-message visibility")

    def insert_tenant_row(
        self,
        *,
        table: str,
        organization_id: str,
        references: Mapping[str, tuple[str, str]],
    ) -> Mapping[str, Any]:
        _missing("composite organization foreign keys")

    def read_tenant_row(
        self, *, table: str, organization_id: str, row_id: str
    ) -> Mapping[str, Any] | None:
        _missing("tenant-scoped generic reads")

    def redis_key(self, *, organization_id: str, category: str, identity: str) -> str:
        _missing("tenant-prefixed Redis namespaces")

    def s3_key(self, *, organization_id: str, artifact_version_id: str) -> str:
        _missing("tenant-prefixed S3 object keys")

    def sign_artifact_read(
        self,
        *,
        organization_id: str,
        artifact_version_id: str,
        requested_by_organization_id: str,
    ) -> str:
        _missing("tenant-checked signed artifact URLs")

    def write_shared_sink(
        self, *, sink: str, organization_id: str, details: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        _missing(f"shared-sink redaction for {sink}")


def build_foundation_runtime() -> FoundationRuntime:
    """Build the production composition; deliberately RED before T023--T044."""

    return FoundationRuntime()
