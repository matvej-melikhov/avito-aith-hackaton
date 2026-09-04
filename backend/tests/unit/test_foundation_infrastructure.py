from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import mysql
from taskiq import TaskiqMessage

from review_platform.infrastructure.db.models.operations import OutboxMessage
from review_platform.infrastructure.db.outbox import (
    OutboxDraft,
    OutboxLease,
    OutboxService,
    StaleOutboxLease,
)
from review_platform.infrastructure.db.repositories.operations import OutboxMessageRepository
from review_platform.infrastructure.object_storage.s3 import (
    ObjectDigestMismatch,
    S3ObjectStorage,
    TenantObjectBoundaryError,
)
from review_platform.infrastructure.providers.mocks import (
    FixtureEmailProvider,
    FrozenFixtureStore,
)
from review_platform.infrastructure.tasks.broker import TenantCorrelationMiddleware
from review_platform.infrastructure.tasks.outbox_relay import (
    OutboxSignal,
    RedisOutboxPublisher,
)
from review_platform.infrastructure.tasks.registry import HandlerRegistry, HandlerRegistryError

ORG = UUID("00000000-0000-7000-8000-000000000001")
MESSAGE = UUID("00000000-0000-7000-8000-000000000010")
AGGREGATE = UUID("00000000-0000-7000-8000-000000000020")
ARTIFACT = "00000000-0000-7000-8000-000000000030"
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class FakeOutboxRepository:
    def __init__(self) -> None:
        self.message: OutboxMessage | None = None

    async def add(self, message: OutboxMessage) -> OutboxMessage:
        self.message = message
        return message

    async def get(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        for_update: bool = False,
    ) -> OutboxMessage | None:
        del for_update
        if (
            self.message is not None
            and self.message.organization_id == organization_id
            and self.message.message_id == message_id
        ):
            return self.message
        return None

    async def lease_available(
        self,
        *,
        now: datetime,
        owner: str,
        lease_seconds: int,
        limit: int,
        token_factory: Callable[[], UUID],
    ) -> Sequence[OutboxMessage]:
        assert self.message is not None
        if limit < 1 or self.message.attempts >= self.message.max_attempts:
            return ()
        self.message.lease_owner = owner
        self.message.lease_token = token_factory()
        self.message.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self.message.enqueue_state = "leased"
        self.message.attempts += 1
        return (self.message,)

    async def complete_lease(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        lease_token: UUID,
        completed_at: datetime,
    ) -> bool:
        if not self._matches(organization_id, message_id, lease_token):
            return False
        assert self.message is not None
        self.message.enqueue_state = "completed"
        self.message.completed_at = completed_at
        self.message.lease_token = None
        return True

    async def fail_lease(
        self,
        organization_id: UUID,
        message_id: UUID,
        *,
        lease_token: UUID,
        available_at: datetime,
        exhausted: bool,
        error_code: str,
        sanitized_error: Mapping[str, Any],
    ) -> bool:
        if not self._matches(organization_id, message_id, lease_token):
            return False
        assert self.message is not None
        self.message.enqueue_state = "action_required" if exhausted else "pending"
        self.message.available_at = available_at
        self.message.error_code = error_code
        self.message.sanitized_error = dict(sanitized_error)
        self.message.lease_token = None
        return True

    def _matches(self, organization_id: UUID, message_id: UUID, token: UUID) -> bool:
        return bool(
            self.message is not None
            and self.message.organization_id == organization_id
            and self.message.message_id == message_id
            and self.message.lease_token == token
            and self.message.enqueue_state == "leased"
        )


def _draft(*, max_attempts: int = 2) -> OutboxDraft:
    return OutboxDraft(
        organization_id=ORG,
        message_id=MESSAGE,
        aggregate_type="course",
        aggregate_id=AGGREGATE,
        event_type="CourseImportRequested",
        payload_version="1.1.0",
        payload={"safe": True},
        available_at=NOW,
        max_attempts=max_attempts,
    )


def test_sql_claim_is_skip_locked_and_bounded() -> None:
    statement = OutboxMessageRepository.available_statement(now=NOW, limit=17)
    sql = str(statement.compile(dialect=mysql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT" in sql
    assert "LEASE_EXPIRES_AT" in sql
    assert "ATTEMPTS <" in sql


@pytest.mark.anyio
async def test_outbox_exhaustion_is_visible_and_expired_completion_is_rejected() -> None:
    repository = FakeOutboxRepository()
    tokens = iter(
        (
            UUID("00000000-0000-7000-8000-000000000101"),
            UUID("00000000-0000-7000-8000-000000000102"),
        )
    )
    service = OutboxService(repository, token_factory=lambda: next(tokens), clock=lambda: NOW)
    await service.create(_draft(max_attempts=2))

    first = (await service.lease(owner="relay-a", limit=1, lease_seconds=30, now=NOW))[0]
    with pytest.raises(StaleOutboxLease, match="expired"):
        await service.complete(first, now=NOW + timedelta(seconds=31))

    # Recovery claim after expiry gets a new token and consumes the final attempt.
    second = (
        await service.lease(
            owner="relay-b", limit=1, lease_seconds=30, now=NOW + timedelta(seconds=31)
        )
    )[0]
    assert second.token != first.token
    recovery = await service.fail(
        second,
        error={"code": "poison", "message": "bad payload", "action": "inspect"},
        now=NOW + timedelta(seconds=32),
    )
    assert recovery.state == "action_required"
    assert recovery.attempts == recovery.max_attempts == 2
    assert recovery.error == {"code": "poison", "message": "bad payload", "action": "inspect"}


class FakePipeline:
    def __init__(self) -> None:
        self.commands: list[tuple[str, object, object]] = []

    def rpush(self, key: str, value: bytes) -> FakePipeline:
        self.commands.append(("rpush", key, value))
        return self

    def expire(self, key: str, seconds: int) -> FakePipeline:
        self.commands.append(("expire", key, seconds))
        return self

    async def execute(self) -> list[int]:
        return [1, 1]


class FakeRedis:
    def __init__(self) -> None:
        self.value = FakePipeline()

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is True
        return self.value

    async def aclose(self) -> None:
        return None


@pytest.mark.anyio
async def test_redis_signal_has_stable_id_and_tenant_namespace_but_not_domain_payload() -> None:
    redis = FakeRedis()
    publisher = RedisOutboxPublisher(redis)  # type: ignore[arg-type]
    lease = OutboxLease(
        organization_id=ORG,
        message_id=MESSAGE,
        aggregate_type="course",
        aggregate_id=AGGREGATE,
        event_type="CourseImportRequested",
        payload_version="1.1.0",
        owner="relay-a",
        token=UUID("00000000-0000-7000-8000-000000000101"),
        expires_at=NOW + timedelta(seconds=30),
        attempts=1,
        max_attempts=2,
    )
    await publisher.publish(OutboxSignal.from_lease(lease))

    _, key, encoded = redis.value.commands[0]
    assert str(key).startswith(f"review-platform:{ORG}:")
    signal = json.loads(bytes(encoded))
    assert signal["message_id"] == str(MESSAGE)
    assert signal["tenant_namespace"] == f"review-platform:{ORG}"
    assert "payload" not in signal


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        key = str(kwargs["Key"])
        body = kwargs["Body"]
        assert hasattr(body, "read")
        self.objects[key] = body.read()  # type: ignore[union-attr]
        return {"ETag": "fixture-etag"}

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        key = str(kwargs["Key"])
        value = self.objects[key]
        return {
            "Body": io.BytesIO(value),
            "ContentLength": len(value),
            "ContentType": "application/octet-stream",
        }

    def generate_presigned_url(
        self,
        client_method: str,
        *,
        Params: Mapping[str, object],
        ExpiresIn: int,
    ) -> str:
        assert client_method == "get_object"
        return f"https://s3.example.test/{Params['Key']}?ttl={ExpiresIn}"

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        key = str(kwargs["Key"])
        self.deleted.append(key)
        return {}


def test_s3_primitives_verify_digest_and_tenant_on_upload_sign_download_delete() -> None:
    client = FakeS3()
    storage = S3ObjectStorage(client=client, bucket="fixture", max_object_bytes=16)
    stored = storage.upload(
        organization_id=str(ORG),
        artifact_version_id=ARTIFACT,
        source=io.BytesIO(b"artifact"),
        media_type="application/octet-stream",
    )
    assert stored.key.startswith(f"{ORG}/{ARTIFACT}/")

    destination = io.BytesIO()
    storage.download_to(
        organization_id=str(ORG),
        artifact_version_id=ARTIFACT,
        key=stored.key,
        destination=destination,
        expected_digest=stored.content_digest,
    )
    assert destination.getvalue() == b"artifact"

    with pytest.raises(TenantObjectBoundaryError, match="organization"):
        storage.sign_read(
            organization_id=str(ORG),
            artifact_version_id=ARTIFACT,
            requested_by_organization_id="00000000-0000-7000-8000-000000000099",
            key=stored.key,
            expires_in_seconds=900,
        )
    with pytest.raises(ObjectDigestMismatch):
        storage.upload(
            organization_id=str(ORG),
            artifact_version_id=ARTIFACT,
            source=io.BytesIO(b"wrong"),
            media_type="application/octet-stream",
            expected_digest=stored.content_digest,
        )

    storage.delete(
        organization_id=str(ORG),
        artifact_version_id=ARTIFACT,
        requested_by_organization_id=str(ORG),
        key=stored.key,
    )
    assert client.deleted == [stored.key]


@pytest.mark.anyio
async def test_fixture_provider_loads_and_validates_frozen_vectors_without_network() -> None:
    fixture = FrozenFixtureStore().load("email-v1.1.0.json")
    result = await FixtureEmailProvider().send(fixture["request"])
    assert result == fixture["success_result"]
    result_dict = dict(result)
    result_dict["outcome"] = "invented"
    assert FrozenFixtureStore().load("email-v1.1.0.json")["success_result"] != result_dict


def test_registry_and_entrypoint_imports_are_explicit() -> None:
    registry = HandlerRegistry()

    async def handler() -> None:
        return None

    registry.register(
        name="fixture.handler",
        kind="email",
        event_type="InvitationEmailRequested",
        handler=handler,
    )
    assert registry.names() == ("fixture.handler",)
    with pytest.raises(HandlerRegistryError, match="duplicate"):
        registry.register(
            name="fixture.handler",
            kind="email",
            event_type="InvitationEmailRequested",
            handler=handler,
        )

    __import__("review_platform.infrastructure.tasks.__main__")
    __import__("review_platform.infrastructure.tasks.relay_main")
    __import__("review_platform.infrastructure.tasks.email_main")


def test_tenant_correlation_is_added_from_stable_message_id() -> None:
    message = TaskiqMessage(
        task_id="task-1",
        task_name="fixture.handler",
        labels={"organization_id": str(ORG), "message_id": str(MESSAGE)},
        args=[],
        kwargs={},
    )
    validated = TenantCorrelationMiddleware().pre_send(message)
    assert validated.labels["correlation_id"] == str(MESSAGE)
    assert validated.labels["tenant_namespace"] == f"review-platform:{ORG}"
