"""Isolated session-scoped MySQL, Redis, and MinIO Testcontainers."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator

import pytest
from testcontainers.core.config import testcontainers_config
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy
from testcontainers.minio import MinioContainer
from testcontainers.mysql import MySqlContainer

MYSQL_IMAGE = "mysql:8.4.11"
REDIS_IMAGE = "redis:7.4.11-bookworm"
MINIO_IMAGE = "minio/minio:RELEASE.2025-09-07T16-13-09Z"


def _current_docker_endpoint() -> str:
    completed = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    endpoint = completed.stdout.strip()
    if not endpoint:
        raise RuntimeError("the active Docker context has no endpoint")
    return endpoint


@pytest.fixture(scope="session")
def docker_host() -> str:
    """Make docker-py use the same explicit endpoint as the Docker CLI context."""

    endpoint = os.environ.setdefault("DOCKER_HOST", _current_docker_endpoint())
    if "/.lima/" in endpoint:
        testcontainers_config.ryuk_disabled = True
        os.environ.setdefault(
            "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE",
            f"/run/user/{os.getuid()}/docker.sock",
        )
    return endpoint


@pytest.fixture(scope="session")
def mysql_container(docker_host: str) -> Iterator[MySqlContainer]:
    del docker_host
    container = MySqlContainer(
        image=MYSQL_IMAGE,
        dialect="pymysql",
        username="review_platform",
        password="local-dev-only",
        root_password="local-dev-root-only",
        dbname="review_platform",
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def redis_container(docker_host: str) -> Iterator[DockerContainer]:
    del docker_host
    container = DockerContainer(image=REDIS_IMAGE)
    container.with_exposed_ports(6379)
    container.waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def minio_container(docker_host: str) -> Iterator[MinioContainer]:
    del docker_host
    container = MinioContainer(
        image=MINIO_IMAGE,
        access_key="localminio",
        secret_key="local-minio-only",
    )
    container.with_env("MINIO_ROOT_USER", "localminio")
    container.with_env("MINIO_ROOT_PASSWORD", "local-minio-only")
    container.start()
    try:
        yield container
    finally:
        container.stop()
