"""Async SQLAlchemy engine and transaction lifecycle helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

AsyncSessionFactory = async_sessionmaker[AsyncSession]


def asyncmy_url(database_url: str | URL) -> URL:
    """Normalize a MySQL URL to SQLAlchemy's asyncmy driver.

    Local Testcontainers commonly expose a ``mysql+pymysql`` URL.  Only its
    transport driver changes here; credentials, host, database, and query
    parameters are preserved.
    """

    url = make_url(database_url) if isinstance(database_url, str) else database_url
    if not url.drivername.startswith("mysql"):
        raise ValueError("database URL must use the MySQL dialect")
    return url.set(drivername="mysql+asyncmy")


def create_database_engine(
    database_url: str | URL,
    *,
    echo: bool = False,
    pool_pre_ping: bool = False,
    pool_recycle: int = 1_800,
) -> AsyncEngine:
    """Create the application engine using asyncmy exclusively.

    The pinned SQLAlchemy/asyncmy pair cannot safely enable ``pre_ping`` when
    PyMySQL is also importable: SQLAlchemy chooses PyMySQL's no-argument ping
    convention while the asyncmy adapter requires ``ping(reconnect)``.  A
    bounded pool recycle remains enabled; callers can opt in after dependency
    versions make pre-ping compatible.
    """

    return create_async_engine(
        asyncmy_url(database_url),
        echo=echo,
        pool_pre_ping=pool_pre_ping,
        pool_recycle=pool_recycle,
    )


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    """Create sessions that keep mapped state readable after commit."""

    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(factory: AsyncSessionFactory) -> AsyncIterator[AsyncSession]:
    """Provide one atomic application unit of work."""

    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def test_transaction(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Run a test in an outer transaction that is always rolled back.

    ``create_savepoint`` keeps the outer transaction alive when application
    code calls ``session.commit()``.  This gives tests production-like commit
    behavior without leaking rows into the next test.
    """

    async with engine.connect() as connection:
        outer_transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        try:
            yield session
        finally:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()


__all__ = [
    "AsyncSessionFactory",
    "asyncmy_url",
    "create_database_engine",
    "create_session_factory",
    "session_scope",
    "test_transaction",
]
