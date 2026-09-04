"""Async Alembic environment backed by the application metadata."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    AuditEvent,
    CommandReceipt,
    Course,
    CourseMembership,
    CourseRun,
    DestinationBinding,
    ExternalCourseBinding,
    ExternalCredential,
    ExternalIdentity,
    Invitation,
    OAuthState,
    Operation,
    OperationAttempt,
    Organization,
    OrganizationMembership,
    OutboxMessage,
    Session,
    User,
)
from review_platform.infrastructure.db.session import asyncmy_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Explicit imports above make all current tables visible to Alembic.  Keeping a
# concrete tuple also prevents static tooling from mistaking them for dead
# imports while future revisions extend the discovery list.
MIGRATED_MODELS = (
    Organization,
    CommandReceipt,
    AuditEvent,
    Operation,
    OperationAttempt,
    OutboxMessage,
    User,
    ExternalIdentity,
    OrganizationMembership,
    Invitation,
    Session,
    ExternalCredential,
    OAuthState,
    AgentAuthorization,
    Course,
    ExternalCourseBinding,
    CourseRun,
    CourseMembership,
    DestinationBinding,
)
target_metadata = Base.metadata


def _database_url() -> str:
    configured = os.environ.get("REVIEW_PLATFORM_DATABASE_URL")
    if configured is None:
        configured = config.get_main_option("sqlalchemy.url")
    return asyncmy_url(configured).render_as_string(hide_password=False)


def _assert_single_head() -> None:
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        rendered = ", ".join(heads) if heads else "none"
        raise RuntimeError(f"migration history must have exactly one head; found: {rendered}")


def _configure(connection: Connection | None, *, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""

    _assert_single_head()
    _configure(None, url=_database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations through SQLAlchemy's asyncmy engine."""

    _assert_single_head()
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
