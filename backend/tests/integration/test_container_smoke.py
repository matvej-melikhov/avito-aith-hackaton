"""T010: real isolated MySQL, Redis, and MinIO smoke test."""

from __future__ import annotations

from io import BytesIO

import pytest
from redis import Redis
from sqlalchemy import create_engine, text
from testcontainers.core.container import DockerContainer
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

pytestmark = pytest.mark.infrastructure

TENANT_ID = "00000000-0000-7000-8000-000000000001"


def test_mysql_redis_and_minio_round_trip_tenant_tagged_data(
    mysql_container: MySqlContainer,
    redis_container: DockerContainer,
    minio_container: MinioContainer,
) -> None:
    engine = create_engine(mysql_container.get_connection_url())
    redis_client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
        decode_responses=True,
    )
    minio_client = minio_container.get_client()
    redis_key = f"review-platform:{TENANT_ID}:smoke"
    bucket = "review-platform-smoke"
    object_key = f"{TENANT_ID}/smoke/payload.txt"
    payload = b"tenant-isolated-smoke"

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE tenant_smoke ("
                    "organization_id CHAR(36) PRIMARY KEY, payload VARCHAR(64) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_smoke (organization_id, payload) "
                    "VALUES (:organization_id, :payload)"
                ),
                {"organization_id": TENANT_ID, "payload": payload.decode()},
            )
            stored = connection.execute(
                text(
                    "SELECT payload FROM tenant_smoke "
                    "WHERE organization_id = :organization_id"
                ),
                {"organization_id": TENANT_ID},
            ).scalar_one()
        assert stored == payload.decode()

        assert redis_client.set(redis_key, payload) is True
        assert redis_client.get(redis_key) == payload.decode()

        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)
        minio_client.put_object(bucket, object_key, BytesIO(payload), length=len(payload))
        response = minio_client.get_object(bucket, object_key)
        try:
            assert response.read() == payload
        finally:
            response.close()
            response.release_conn()
    finally:
        redis_client.delete(redis_key)
        if minio_client.bucket_exists(bucket):
            try:
                minio_client.remove_object(bucket, object_key)
            finally:
                minio_client.remove_bucket(bucket)
        engine.dispose()
