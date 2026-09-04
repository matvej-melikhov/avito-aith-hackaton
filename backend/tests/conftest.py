"""Deterministic, offline-by-default pytest configuration."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

pytest_plugins = ("tests.fixtures.containers",)

FIXED_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="run tests that require an explicitly authorized provider sandbox",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live tests require explicit --run-live authorization")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def fixed_clock() -> Callable[[], datetime]:
    return lambda: FIXED_NOW


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def uuid7_factory() -> Iterator[Callable[[], UUID]]:
    counter = 0

    def make_uuid() -> UUID:
        nonlocal counter
        counter += 1
        return UUID(f"00000000-0000-7000-8000-{counter:012d}")

    yield make_uuid


@pytest.fixture(autouse=True)
def no_network_by_default(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live") and request.config.getoption("--run-live"):
        return

    original_create_connection = cast(Callable[..., socket.socket], socket.create_connection)
    original_socket_connect = socket.socket.connect

    def guarded_create_connection(
        address: tuple[str, int], *args: Any, **kwargs: Any
    ) -> socket.socket:
        if address[0] in LOCAL_HOSTS:
            return original_create_connection(address, *args, **kwargs)
        raise RuntimeError("network access is disabled for offline tests")

    def guarded_socket_connect(
        instance: socket.socket, address: tuple[str, int] | str
    ) -> None:
        if isinstance(address, str) or address[0] in LOCAL_HOSTS:
            return original_socket_connect(instance, address)
        raise RuntimeError("network access is disabled for offline tests")

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    monkeypatch.setattr(socket.socket, "connect", guarded_socket_connect)
