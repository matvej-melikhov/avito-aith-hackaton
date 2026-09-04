"""Production-composed Foundation runtime over isolated MySQL and MinIO."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import boto3
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

from review_platform.application.foundation_runtime import (
    FoundationRuntime,
    build_foundation_runtime,
)
from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.session import (
    AsyncSessionFactory,
    create_database_engine,
    create_session_factory,
)
from review_platform.infrastructure.object_storage.s3 import S3Client
from review_platform.settings import Settings


@pytest.fixture
async def foundation_session_factory(
    mysql_container: MySqlContainer,
) -> AsyncIterator[AsyncSessionFactory]:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with factory.begin() as session:
        session.add_all(
            [
                Organization(
                    id=UUID("00000000-0000-7000-8000-000000000001"),
                    slug="fixture-org-a",
                    name="Fixture Org A",
                    status="active",
                    revision=0,
                ),
                Organization(
                    id=UUID("00000000-0000-7000-8000-000000000002"),
                    slug="fixture-org-b",
                    name="Fixture Org B",
                    status="active",
                    revision=0,
                ),
            ]
        )
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
async def foundation_session(
    foundation_session_factory: AsyncSessionFactory,
) -> AsyncIterator[AsyncSession]:
    async with foundation_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def foundation_runtime(
    foundation_session_factory: AsyncSessionFactory,
    minio_container: MinioContainer,
    fixed_clock: Callable[[], datetime],
    uuid7_factory: Callable[[], UUID],
) -> AsyncIterator[FoundationRuntime]:
    config = cast(dict[str, str], minio_container.get_config())
    client = cast(
        Any,
        boto3.client(
            "s3",
            endpoint_url=f"http://{config['endpoint']}",
            region_name="us-east-1",
            aws_access_key_id=config["access_key"],
            aws_secret_access_key=config["secret_key"],
        ),
    )
    bucket = "review-platform-foundation"
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        client.create_bucket(Bucket=bucket)
    runtime = build_foundation_runtime(
        Settings(s3_bucket=bucket),
        session_factory=foundation_session_factory,
        s3_client=cast(S3Client, client),
        id_factory=uuid7_factory,
        clock=fixed_clock,
    )
    try:
        yield runtime
    finally:
        await runtime.close()
