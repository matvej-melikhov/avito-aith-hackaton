"""Declarative base and tenant-key building blocks.

Every tenant-owned entity exposes ``(organization_id, id)`` as a candidate
key.  References between tenant-owned entities must use
:func:`tenant_foreign_key`; carrying the organization identifier in the
foreign key makes a cross-tenant link invalid even if an object identifier is
known.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

UUID_TYPE = Uuid(as_uuid=True, native_uuid=False)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware UTC datetimes through MySQL's timezone-naive DATETIME."""

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=False)

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(AsyncAttrs, DeclarativeBase):
    """Base for all SQLAlchemy mappings."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDIdentityMixin:
    """Globally opaque UUID identity.

    UUID generation belongs to the domain primitive layer so persistence does
    not silently substitute UUIDv4 when a caller forgot to provide an ID.
    """

    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True)


class TenantOwnedMixin:
    """Direct Organization ownership for a persisted row."""

    organization_id: Mapped[UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("organization.id", ondelete="RESTRICT"),
        nullable=False,
    )


class TenantEntityMixin(UUIDIdentityMixin, TenantOwnedMixin):
    """A tenant-owned UUID entity.

    Concrete models must include :func:`tenant_candidate_key` in their table
    arguments.  Keeping that declaration explicit prevents model-specific
    ``__table_args__`` from accidentally replacing a magic mixin constraint.
    """


class RevisionMixin:
    """Optimistic-concurrency revision starting at zero."""

    revision: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))


class TimestampMixin:
    """Creation and update timestamps stored as UTC instants."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


def tenant_candidate_key(id_column: str = "id", *, name: str | None = None) -> UniqueConstraint:
    """Return the candidate key targeted by tenant-composite foreign keys."""

    return UniqueConstraint(
        "organization_id",
        id_column,
        name=name,
    )


def tenant_foreign_key(
    local_id_column: str,
    referred_table: str,
    *,
    referred_id_column: str = "id",
    ondelete: str = "RESTRICT",
    name: str | None = None,
) -> ForeignKeyConstraint:
    """Build a tenant-safe FK from ``organization_id`` plus a local ID.

    The referred table must expose the corresponding candidate key.  The
    optional explicit name is useful for especially long domain names where a
    backend's identifier length is constrained.
    """

    return ForeignKeyConstraint(
        ["organization_id", local_id_column],
        [
            f"{referred_table}.organization_id",
            f"{referred_table}.{referred_id_column}",
        ],
        name=name,
        ondelete=ondelete,
    )


def string_enum_check(column: str, values: tuple[str, ...]) -> str:
    """Produce deterministic SQL for a portable string-enum check."""

    rendered_values = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered_values})"


def json_dict_default() -> dict[str, Any]:
    """Typed callable default for JSON object columns."""

    return {}


__all__ = [
    "NAMING_CONVENTION",
    "UUID_TYPE",
    "Base",
    "RevisionMixin",
    "TenantEntityMixin",
    "TenantOwnedMixin",
    "TimestampMixin",
    "UTCDateTime",
    "UUIDIdentityMixin",
    "json_dict_default",
    "string_enum_check",
    "tenant_candidate_key",
    "tenant_foreign_key",
]
