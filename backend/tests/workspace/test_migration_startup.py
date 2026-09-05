"""A clean checkout can migrate an empty database to every runtime table."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base

pytestmark = pytest.mark.infrastructure


@pytest.fixture
def migration_database(docker_host: str) -> Iterator[MySqlContainer]:
    # A migration must start from an empty database, independently of the ORM fixtures.
    del docker_host
    with MySqlContainer("mysql:8.4.11", dialect="pymysql") as container:
        yield container


def test_fresh_database_has_complete_workspace_schema(migration_database: MySqlContainer) -> None:
    root = Path(__file__).resolve().parents[2]
    environment = dict(
        os.environ, REVIEW_PLATFORM_DATABASE_URL=migration_database.get_connection_url()
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    engine = create_engine(migration_database.get_connection_url())
    try:
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        for table, column in (
            ("homework_version", "review_only"),
            ("submission_version", "comment"),
        ):
            assert column in {field["name"] for field in inspector.get_columns(table)}
    finally:
        engine.dispose()
