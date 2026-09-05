"""Accumulated two-tenant release gate for every backend execution plane."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, cast
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis
from sqlalchemy import ForeignKeyConstraint, Table, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from taskiq import TaskiqMessage
from testcontainers.core.container import DockerContainer

from review_platform.application.foundation_runtime import BoundaryViolation, FoundationRuntime
from review_platform.application.ports.providers import ProviderPayload
from review_platform.application.request_context import RequestActor
from review_platform.domain.primitives import require_utc, sha256_digest
from review_platform.infrastructure.auth.agent_tokens import issue_agent_token
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.identity import (
    AgentAuthorization,
    ExternalCredential,
    OrganizationMembership,
    User,
)
from review_platform.infrastructure.db.models.learning import Course
from review_platform.infrastructure.db.models.operations import (
    AuditEvent,
    Operation,
    OperationAttempt,
)
from review_platform.infrastructure.db.repositories.deliveries import SqlDeliveryRepository
from review_platform.infrastructure.db.repositories.identity import ExternalCredentialRepository
from review_platform.infrastructure.db.repositories.operations import AuditEventRepository
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope
from review_platform.infrastructure.object_storage.promotions import (
    ArtifactOrphanCleaner,
    S3PromotionObjects,
    StagedObjectCandidate,
)
from review_platform.infrastructure.object_storage.s3 import (
    S3ObjectStorage,
    TenantObjectBoundaryError,
)
from review_platform.infrastructure.providers.github_artifacts import (
    ArtifactContentHandoff,
    CredentialBindingMismatch,
    CredentialMaterial,
    GitHubArtifactProvider,
    GitHubCaptureLimits,
    GitHubLocator,
    GitHubRepositorySnapshot,
    HandoffContent,
    ProviderClientError,
)
from review_platform.infrastructure.tasks.broker import (
    BrokerPolicy,
    KindConcurrencyMiddleware,
    TenantCorrelationMiddleware,
)
from review_platform.infrastructure.tasks.outbox_relay import (
    OutboxSignal,
    RedisOutboxPublisher,
    redis_outbox_key,
)
from review_platform.main import create_app
from review_platform.mcp.server import create_mcp_app
from review_platform.settings import Settings

pytestmark = [pytest.mark.behavioral, pytest.mark.infrastructure, pytest.mark.anyio]

ORG_A = UUID("00000000-0000-7000-8000-000000000001")
ORG_B = UUID("00000000-0000-7000-8000-000000000002")
USER_A = UUID("00000000-0000-7000-8000-000000162001")
USER_B = UUID("00000000-0000-7000-8000-000000162002")
MEMBERSHIP_A = UUID("00000000-0000-7000-8000-000000162003")
MEMBERSHIP_B = UUID("00000000-0000-7000-8000-000000162004")
AGENT_A = UUID("00000000-0000-7000-8000-000000162005")
AUTHORIZATION_A = UUID("00000000-0000-7000-8000-000000162006")
CREDENTIAL_A = UUID("00000000-0000-7000-8000-000000162007")
CREDENTIAL_B = UUID("00000000-0000-7000-8000-000000162008")
COURSE_A = UUID("00000000-0000-7000-8000-000000162009")
COURSE_B = UUID("00000000-0000-7000-8000-000000162010")
OPERATION_A = UUID("00000000-0000-7000-8000-000000162011")
OPERATION_B = UUID("00000000-0000-7000-8000-000000162012")
MESSAGE_A = UUID("00000000-0000-7000-8000-000000162013")
MESSAGE_B = UUID("00000000-0000-7000-8000-000000162014")
ARTIFACT_A = UUID("00000000-0000-7000-8000-000000162015")
ARTIFACT_B = UUID("00000000-0000-7000-8000-000000162016")
AUDIT_ENTITY = UUID("00000000-0000-7000-8000-000000162017")


@dataclass(frozen=True, slots=True)
class MatrixHarness:
    runtime: FoundationRuntime
    factory: AsyncSessionFactory
    user_actor: RequestActor
    agent_token: str


@pytest.fixture
async def matrix_harness(
    foundation_runtime: FoundationRuntime,
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[MatrixHarness]:
    now = require_utc(foundation_runtime.clock())
    token = issue_agent_token(random_bytes=lambda size: b"t" * size)
    async with session_scope(foundation_session_factory) as session:
        session.add_all(
            [
                User(id=USER_A, display_name="Tenant A", status="active"),
                User(id=USER_B, display_name="Tenant B", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                OrganizationMembership(
                    id=MEMBERSHIP_A,
                    organization_id=ORG_A,
                    user_id=USER_A,
                    roles=["methodologist", "reviewer"],
                    status="active",
                    revision=2,
                    auth_epoch=1,
                ),
                OrganizationMembership(
                    id=MEMBERSHIP_B,
                    organization_id=ORG_B,
                    user_id=USER_B,
                    roles=["reviewer"],
                    status="active",
                    revision=0,
                    auth_epoch=0,
                ),
                ExternalCredential(
                    id=CREDENTIAL_A,
                    organization_id=ORG_A,
                    provider="github",
                    binding_version=1,
                    ciphertext="sealed-a",
                    key_id="fixture-key",
                    status="active",
                ),
                ExternalCredential(
                    id=CREDENTIAL_B,
                    organization_id=ORG_B,
                    provider="github",
                    binding_version=1,
                    ciphertext="sealed-b",
                    key_id="fixture-key",
                    status="active",
                ),
                Course(
                    id=COURSE_A,
                    organization_id=ORG_A,
                    title="Tenant A course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                Course(
                    id=COURSE_B,
                    organization_id=ORG_B,
                    title="Tenant B course",
                    description="",
                    source_kind="standalone",
                    status="active",
                    revision=0,
                ),
                Operation(
                    id=OPERATION_A,
                    organization_id=ORG_A,
                    kind="course_import",
                    input_version="tenant-a",
                    state="succeeded",
                    revision=1,
                    created_at=now,
                    updated_at=now,
                    finished_at=now,
                ),
                Operation(
                    id=OPERATION_B,
                    organization_id=ORG_B,
                    kind="course_import",
                    input_version="tenant-b",
                    state="succeeded",
                    revision=1,
                    created_at=now,
                    updated_at=now,
                    finished_at=now,
                ),
            ]
        )
        await session.flush()
        session.add(
            AgentAuthorization(
                id=AUTHORIZATION_A,
                organization_id=ORG_A,
                user_id=USER_A,
                agent_id=AGENT_A,
                scopes=[
                    "courses:read",
                    "review_preferences:write",
                    "operations:read",
                ],
                membership_revision=2,
                auth_epoch=1,
                token_digest=token.token_digest,
                status="active",
                expires_at=now + timedelta(hours=1),
                revision=3,
            )
        )
    yield MatrixHarness(
        runtime=foundation_runtime,
        factory=foundation_session_factory,
        user_actor=RequestActor.user(
            organization_id=ORG_A,
            user_id=USER_A,
            roles={"methodologist", "reviewer"},
            membership_revision=2,
            auth_epoch=1,
        ),
        agent_token=token.access_token.reveal(),
    )


@asynccontextmanager
async def _api_client(
    app: FastAPI,
    actor: RequestActor,
) -> AsyncIterator[httpx.AsyncClient]:
    @app.middleware("http")
    async def server_owned_actor(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_actor = actor
        return await call_next(request)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://tenant-matrix.test",
    ) as client:
        yield client


async def test_rest_reads_writes_and_body_limits_never_cross_tenants(
    matrix_harness: MatrixHarness,
) -> None:
    app = create_app(runtime=matrix_harness.runtime)
    archive = _wire_command(
        command_name="archive_course",
        revision_target="course",
        target_id=COURSE_B,
        expected_revision=0,
        payload={"reason": "cross-tenant attempt"},
    )
    async with _api_client(app, matrix_harness.user_actor) as client:
        courses = await client.get("/api/v1/courses")
        foreign_operation = await client.get(f"/api/v1/operations/{OPERATION_B}")
        foreign_write = await client.post(
            f"/api/v1/courses/{COURSE_B}/archive",
            json=archive,
        )
    assert courses.status_code == 200
    assert [item["id"] for item in courses.json()["items"]] == [str(COURSE_A)]
    assert foreign_operation.status_code == 404
    assert foreign_write.status_code == 409
    async with matrix_harness.factory() as session:
        course_b = await session.scalar(select(Course).where(Course.id == COURSE_B))
    assert course_b is not None and course_b.status == "active"

    limited = create_app(Settings(command_body_limit_bytes=256), runtime=matrix_harness.runtime)
    async with _api_client(limited, matrix_harness.user_actor) as client:
        oversized = await client.post(
            f"/api/v1/courses/{COURSE_A}/archive",
            content=b'{"raw_body":"' + b"tenant-secret-" * 40 + b'"}',
        )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "request_too_large"
    assert "tenant-secret" not in oversized.text


async def test_mcp_reads_writes_and_body_limits_never_cross_tenants(
    matrix_harness: MatrixHarness,
) -> None:
    app = create_mcp_app(runtime=matrix_harness.runtime)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://tenant-matrix.test",
    ) as client:
        courses = await client.post(
            "/mcp",
            headers=_mcp_headers(matrix_harness.agent_token, "list_courses"),
            json={},
        )
        foreign_operation = await client.post(
            "/mcp",
            headers=_mcp_headers(matrix_harness.agent_token, "get_operation"),
            json={"operation_id": str(OPERATION_B)},
        )
        foreign_write = await client.post(
            "/mcp",
            headers=_mcp_headers(matrix_harness.agent_token, "set_availability"),
            json=_wire_command(
                command_name="set_reviewer_availability",
                revision_target="membership",
                target_id=MEMBERSHIP_B,
                expected_revision=0,
                payload={
                    "planned_minutes": 30,
                    "until_at": (
                        require_utc(matrix_harness.runtime.clock()) + timedelta(hours=1)
                    ).isoformat(),
                },
            ),
        )
    assert courses.status_code == 200
    assert [item["id"] for item in courses.json()["items"]] == [str(COURSE_A)]
    assert foreign_operation.status_code == 404
    assert foreign_write.status_code == 409

    limited = create_mcp_app(
        Settings(command_body_limit_bytes=256),
        runtime=matrix_harness.runtime,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=limited),
        base_url="http://tenant-matrix.test",
    ) as client:
        oversized = await client.post(
            "/mcp",
            headers=_mcp_headers(matrix_harness.agent_token, "list_courses"),
            content=b'{"raw_body":"' + b"tenant-secret-" * 40 + b'"}',
        )
    assert oversized.status_code == 413
    assert "tenant-secret" not in oversized.text


async def test_db_candidate_keys_composite_fks_delivery_and_promotion_are_tenant_bound() -> None:
    tables = Base.metadata.tables
    for table_name in (
        "operation_attempt",
        "artifact_reference",
        "artifact_version",
        "artifact_promotion",
        "submission",
        "submission_version",
        "review_case",
        "review_iteration",
        "ai_review_run",
        "ai_review_attempt",
        "review_publication",
        "external_delivery",
        "delivery_attempt",
        "delivery_reconciliation_observation",
    ):
        table = tables[table_name]
        assert ("organization_id", "id") in _unique_keys(table), table_name
    for child, parent in (
        ("operation_attempt", "operation"),
        ("artifact_version", "artifact_reference"),
        ("artifact_promotion", "artifact_version"),
        ("submission_version", "submission"),
        ("review_iteration", "review_case"),
        ("ai_review_attempt", "ai_review_run"),
        ("external_delivery", "review_publication"),
        ("delivery_attempt", "external_delivery"),
        ("delivery_reconciliation_observation", "delivery_attempt"),
    ):
        assert _has_composite_tenant_fk(tables[child], parent), (child, parent)


async def test_mysql_rejects_cross_tenant_child_and_projects_audit_and_credentials(
    matrix_harness: MatrixHarness,
) -> None:
    now = require_utc(matrix_harness.runtime.clock())
    async with matrix_harness.factory() as session:
        session.add(
            OperationAttempt(
                id=UUID("00000000-0000-7000-8000-000000162020"),
                organization_id=ORG_A,
                operation_id=OPERATION_B,
                attempt_number=1,
                worker_identity="cross-tenant-worker",
                started_at=now,
                outcome="processing",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with session_scope(matrix_harness.factory) as session:
        session.add_all(
            [
                _audit(ORG_A, UUID("00000000-0000-7000-8000-000000162021"), now),
                _audit(ORG_B, UUID("00000000-0000-7000-8000-000000162022"), now),
            ]
        )
    async with matrix_harness.factory() as session:
        credentials = ExternalCredentialRepository(session)
        assert await credentials.get_exact(ORG_A, CREDENTIAL_A, 1) is not None
        assert await credentials.get_exact(ORG_A, CREDENTIAL_B, 1) is None
        assert await credentials.get_exact(ORG_B, CREDENTIAL_A, 1) is None
        audit_a = await AuditEventRepository(session).list_for_entity(
            ORG_A,
            entity_type="tenant_matrix",
            entity_id=AUDIT_ENTITY,
        )
        audit_b = await AuditEventRepository(session).list_for_entity(
            ORG_B,
            entity_type="tenant_matrix",
            entity_id=AUDIT_ENTITY,
        )
    assert [row.organization_id for row in audit_a] == [ORG_A]
    assert [row.organization_id for row in audit_b] == [ORG_B]


async def test_outbox_redis_cache_jobs_and_concurrency_preserve_tenant_identity(
    matrix_harness: MatrixHarness,
    redis_container: DockerContainer,
) -> None:
    for organization_id, message_id in ((ORG_A, MESSAGE_A), (ORG_B, MESSAGE_B)):
        await matrix_harness.runtime.enqueue_outbox(
            organization_id=str(organization_id),
            message_id=str(message_id),
            payload={"event_type": "TenantMatrixRequested", "private": str(organization_id)},
            max_attempts=2,
        )
    leases = await matrix_harness.runtime.lease_outbox(
        owner="tenant-matrix-relay",
        now=matrix_harness.runtime.clock(),
        limit=10,
        lease_seconds=30,
    )
    assert {(lease.organization_id, lease.message_id) for lease in leases} == {
        (str(ORG_A), str(MESSAGE_A)),
        (str(ORG_B), str(MESSAGE_B)),
    }
    cache_keys = {
        organization_id: matrix_harness.runtime.redis_key(
            organization_id=str(organization_id),
            category="operation",
            identity=str(OPERATION_A if organization_id == ORG_A else OPERATION_B),
        )
        for organization_id in (ORG_A, ORG_B)
    }
    assert cache_keys[ORG_A].startswith(f"review-platform:{ORG_A}:")
    assert cache_keys[ORG_B].startswith(f"review-platform:{ORG_B}:")
    assert cache_keys[ORG_A] != cache_keys[ORG_B]
    redis = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    publisher = RedisOutboxPublisher(redis)
    keys = [
        redis_outbox_key(
            organization_id=str(organization_id),
            event_type="TenantMatrixRequested",
        )
        for organization_id in (ORG_A, ORG_B)
    ]
    try:
        for lease in leases:
            await publisher.publish(
                OutboxSignal(
                    message_id=lease.message_id,
                    organization_id=lease.organization_id,
                    tenant_namespace=f"review-platform:{lease.organization_id}",
                    event_type="TenantMatrixRequested",
                    payload_version="1.1.0",
                )
            )
        signals = [json.loads(cast(bytes, await redis.lpop(key))) for key in keys]
        assert {item["organization_id"] for item in signals} == {str(ORG_A), str(ORG_B)}
        assert all("private" not in item for item in signals)
    finally:
        await redis.delete(*keys)
        await publisher.close()

    messages = [_task_message(ORG_A, MESSAGE_A), _task_message(ORG_B, MESSAGE_B)]
    correlated = [TenantCorrelationMiddleware().pre_execute(message) for message in messages]
    assert [item.labels["tenant_namespace"] for item in correlated] == [
        f"review-platform:{ORG_A}",
        f"review-platform:{ORG_B}",
    ]
    policy = BrokerPolicy.from_settings(
        Settings(
            redis_url="redis://localhost:6379/0",
            course_import_concurrency=1,
            ai_review_concurrency=2,
            delivery_concurrency=3,
        )
    )
    assert policy.concurrency_by_kind == {
        "course_import": 1,
        "artifact_capture": 1,
        "ai_review": 2,
        "external_delivery": 3,
        "email": 3,
    }
    limiter = KindConcurrencyMiddleware({"course_import": 1})
    await limiter.pre_execute(correlated[0])
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(limiter.pre_execute(correlated[1]), timeout=0.01)
    limiter.on_error(correlated[0], cast(Any, None), RuntimeError("release slot"))
    await limiter.pre_execute(correlated[1])
    limiter.on_error(correlated[1], cast(Any, None), RuntimeError("release slot"))


async def test_minio_signed_urls_promotion_and_cleanup_never_cross_tenants(
    matrix_harness: MatrixHarness,
) -> None:
    storage = cast(S3ObjectStorage, matrix_harness.runtime.components()["object_storage"])
    content_a = b"tenant-a-artifact"
    content_b = b"tenant-b-artifact"
    key_a = storage.upload(
        organization_id=str(ORG_A),
        artifact_version_id=str(ARTIFACT_A),
        source=io.BytesIO(content_a),
        media_type="application/octet-stream",
        filename="staged/matrix/artifact.bin",
    ).key
    key_b = storage.upload(
        organization_id=str(ORG_B),
        artifact_version_id=str(ARTIFACT_B),
        source=io.BytesIO(content_b),
        media_type="application/octet-stream",
        filename="staged/matrix/artifact.bin",
    ).key
    assert matrix_harness.runtime.sign_artifact_read(
        organization_id=str(ORG_A),
        artifact_version_id=str(ARTIFACT_A),
        requested_by_organization_id=str(ORG_A),
        object_key=key_a,
    )
    with pytest.raises(BoundaryViolation):
        matrix_harness.runtime.sign_artifact_read(
            organization_id=str(ORG_A),
            artifact_version_id=str(ARTIFACT_A),
            requested_by_organization_id=str(ORG_B),
            object_key=key_a,
        )
    with pytest.raises(TenantObjectBoundaryError):
        S3PromotionObjects(storage).delete(
            organization_id=ORG_A,
            artifact_version_id=ARTIFACT_A,
            key=key_b,
        )

    cleaner = ArtifactOrphanCleaner(
        repository=_LivePromotionIntents({(ORG_A, key_a)}),
        inventory=_Inventory(
            [
                StagedObjectCandidate(ORG_A, ARTIFACT_A, key_a, matrix_harness.runtime.clock()),
                StagedObjectCandidate(ORG_B, ARTIFACT_B, key_b, matrix_harness.runtime.clock()),
            ]
        ),
        objects=S3PromotionObjects(storage),
    )
    result = await cleaner.cleanup(
        older_than=require_utc(matrix_harness.runtime.clock()),
        dry_run=False,
    )
    assert result.protected == 1
    assert result.deleted == (key_b,)
    output = io.BytesIO()
    storage.download_to(
        organization_id=str(ORG_A),
        artifact_version_id=str(ARTIFACT_A),
        key=key_a,
        destination=output,
        expected_digest=sha256_digest(content_a),
    )
    assert output.getvalue() == content_a


async def test_provider_batches_errors_and_exact_credentials_are_tenant_bound(
    matrix_harness: MatrixHarness,
) -> None:
    resolver = _CredentialResolver(
        {
            (ORG_A, CREDENTIAL_A): CredentialMaterial(
                ORG_A, CREDENTIAL_A, 1, "github", secret="secret-a"
            ),
            (ORG_B, CREDENTIAL_B): CredentialMaterial(
                ORG_B, CREDENTIAL_B, 1, "github", secret="secret-b"
            ),
        }
    )
    provider = GitHubArtifactProvider(
        client=_GitHubClient(),
        credentials=resolver,
        handoff=_UnusedHandoff(),
    )
    results = await asyncio.gather(
        provider.preflight(_preflight(ORG_A, CREDENTIAL_A)),
        provider.preflight(_preflight(ORG_B, CREDENTIAL_B)),
    )
    assert {result["organization_id"] for result in results} == {str(ORG_A), str(ORG_B)}
    assert resolver.calls == {
        (ORG_A, CREDENTIAL_A, 1, "github"),
        (ORG_B, CREDENTIAL_B, 1, "github"),
    }
    with pytest.raises(CredentialBindingMismatch):
        await provider.preflight(_preflight(ORG_A, CREDENTIAL_B))

    provider_secret = "provider-secret-must-not-cross-boundary"
    failed = await GitHubArtifactProvider(
        client=_GitHubClient(
            failure=ProviderClientError(
                "access_denied",
                f"Bearer {provider_secret}",
                retryable=False,
                action="install_github_app",
            )
        ),
        credentials=resolver,
        handoff=_UnusedHandoff(),
    ).preflight(_preflight(ORG_A, CREDENTIAL_A))
    assert failed["organization_id"] == str(ORG_A)
    assert failed["read_capability"] == "requires_action"
    assert provider_secret not in repr(failed)

    repository = SqlDeliveryRepository()
    async with matrix_harness.factory() as session:
        claims_a = await repository.claim_batch(
            ORG_A,
            "github",
            worker_identity="tenant-a-provider-batch",
            now=matrix_harness.runtime.clock(),
            lease_for=timedelta(seconds=30),
            limit=10,
            transaction=session,
        )
        claims_b = await repository.claim_batch(
            ORG_B,
            "github",
            worker_identity="tenant-b-provider-batch",
            now=matrix_harness.runtime.clock(),
            lease_for=timedelta(seconds=30),
            limit=10,
            transaction=session,
        )
    assert claims_a == claims_b == ()


def _wire_command(
    *,
    command_name: str,
    revision_target: str,
    target_id: UUID,
    expected_revision: int,
    payload: Mapping[str, object],
) -> dict[str, object]:
    return {
        "request_id": "00000000-0000-7000-8000-000000162030",
        "idempotency_key": f"tenant-matrix-{command_name}-0001",
        "command_name": command_name,
        "revision_target": revision_target,
        "target_id": str(target_id),
        "expected_revision": expected_revision,
        "payload": dict(payload),
    }


def _mcp_headers(token: str, tool_name: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool_name,
    }


def _unique_keys(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _has_composite_tenant_fk(table: Table, parent: str) -> bool:
    return any(
        "organization_id" in {column.name for column in constraint.columns}
        and f"{parent}.organization_id"
        in {element.target_fullname for element in constraint.elements}
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )


def _audit(organization_id: UUID, event_id: UUID, occurred_at: Any) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        organization_id=organization_id,
        actor_type="user",
        actor_user_id=USER_A if organization_id == ORG_A else USER_B,
        installation_operator_id=None,
        agent_id=None,
        agent_authorization_id=None,
        action="tenant_matrix",
        entity_type="tenant_matrix",
        entity_id=AUDIT_ENTITY,
        before_revision=None,
        after_revision=None,
        request_id=event_id,
        trace_id=event_id,
        outcome="succeeded",
        sanitized_details={"organization_id": str(organization_id)},
        occurred_at=occurred_at,
    )


def _task_message(organization_id: UUID, message_id: UUID) -> TaskiqMessage:
    return TaskiqMessage(
        task_id=str(message_id),
        task_name="tenant.matrix",
        labels={
            "organization_id": str(organization_id),
            "message_id": str(message_id),
            "task_kind": "course_import",
        },
        args=[],
        kwargs={},
    )


class _LivePromotionIntents:
    def __init__(self, live: set[tuple[UUID, str]]) -> None:
        self._live = live

    async def has_live_staged_intent(
        self,
        organization_id: UUID,
        staged_key: str,
    ) -> bool:
        return (organization_id, staged_key) in self._live


class _Inventory:
    def __init__(self, candidates: Sequence[StagedObjectCandidate]) -> None:
        self._candidates = candidates

    async def list_staged(self, *, older_than: Any) -> Sequence[StagedObjectCandidate]:
        del older_than
        return self._candidates


class _CredentialResolver:
    def __init__(
        self,
        values: Mapping[tuple[UUID, UUID], CredentialMaterial],
    ) -> None:
        self._values = values
        self.calls: set[tuple[UUID, UUID, int, str]] = set()

    async def resolve_exact(
        self,
        *,
        organization_id: UUID,
        credential_binding_id: UUID,
        credential_binding_version: int,
        provider: Literal["github", "google_docs"],
    ) -> CredentialMaterial | None:
        self.calls.add(
            (
                organization_id,
                credential_binding_id,
                credential_binding_version,
                provider,
            )
        )
        return self._values.get((organization_id, credential_binding_id))


class _GitHubClient:
    def __init__(self, failure: ProviderClientError | None = None) -> None:
        self._failure = failure

    async def inspect_repository(
        self,
        locator: GitHubLocator,
        *,
        credential: CredentialMaterial,
        follow_redirects: Literal[False] = False,
    ) -> GitHubRepositorySnapshot:
        del credential
        assert follow_redirects is False
        if self._failure is not None:
            raise self._failure
        return GitHubRepositorySnapshot(
            owner=locator.owner,
            repository=locator.repository,
            resolved_commit="a" * 40,
            source_url="https://github.com/example/repository",
        )

    def stream_archive(
        self,
        snapshot: GitHubRepositorySnapshot,
        *,
        credential: CredentialMaterial,
        limits: GitHubCaptureLimits,
        follow_redirects: Literal[False] = False,
    ) -> AsyncIterator[bytes]:
        del snapshot, credential, limits, follow_redirects

        async def unused() -> AsyncIterator[bytes]:
            if False:
                yield b""

        return unused()


class _UnusedHandoff(ArtifactContentHandoff):
    async def ingest(
        self,
        stream: AsyncIterator[bytes],
        *,
        max_bytes: int,
        expected_media_type: str,
        provider_version: str,
    ) -> HandoffContent:
        del stream, max_bytes, expected_media_type, provider_version
        raise AssertionError("preflight must not hand off artifact content")


def _preflight(organization_id: UUID, credential_id: UUID) -> ProviderPayload:
    return {
        "contract_version": "1.1.0",
        "organization_id": str(organization_id),
        "provider": "github",
        "url": "https://github.com/example/repository",
        "credential_binding_id": str(credential_id),
        "credential_binding_version": 1,
    }
