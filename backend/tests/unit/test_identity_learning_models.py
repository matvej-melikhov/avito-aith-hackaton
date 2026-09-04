"""Structural executable specification for identity and learning persistence."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint, Table, UniqueConstraint
from sqlalchemy.exc import IntegrityError
from testcontainers.mysql import MySqlContainer

from review_platform.infrastructure.db.base import Base
from review_platform.infrastructure.db.models import (
    AgentAuthorization,
    Course,
    CourseMembership,
    CourseRun,
    DestinationBinding,
    ExternalCourseBinding,
    ExternalCredential,
    ExternalIdentity,
    Invitation,
    OAuthState,
    Organization,
    OrganizationMembership,
    Session,
    User,
)
from review_platform.infrastructure.db.session import (
    create_database_engine,
)
from review_platform.infrastructure.db.session import (
    test_transaction as foundation_test_transaction,
)


def _unique_column_sets(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_signatures(
    table: Table,
) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def test_identity_and_learning_tables_are_registered_in_foundation_metadata() -> None:
    expected = {
        "user",
        "external_identity",
        "organization_membership",
        "invitation",
        "oauth_state",
        "session",
        "agent_authorization",
        "external_credential",
        "external_course_binding",
        "course",
        "course_run",
        "course_membership",
        "destination_binding",
    }

    assert expected <= set(Base.metadata.tables)


def test_every_tenant_identity_and_learning_table_has_candidate_key_and_org_fk() -> None:
    tenant_models = (
        OrganizationMembership,
        Invitation,
        Session,
        AgentAuthorization,
        Course,
        CourseRun,
        CourseMembership,
    )
    for model in tenant_models:
        table = model.__table__
        assert ("organization_id", "id") in _unique_column_sets(table)
        assert (
            ("organization_id",),
            ("organization.id",),
        ) in _foreign_key_signatures(table)

    assert ("organization_id", "state_id") in _unique_column_sets(OAuthState.__table__)
    assert (
        ("organization_id",),
        ("organization.id",),
    ) in _foreign_key_signatures(OAuthState.__table__)
    for model in (ExternalCredential, ExternalCourseBinding, DestinationBinding):
        assert {column.name for column in model.__table__.primary_key.columns} == {
            "id",
            "binding_version",
        }
        assert ("organization_id", "id", "binding_version") in _unique_column_sets(
            model.__table__
        )
        assert (
            ("organization_id",),
            ("organization.id",),
        ) in _foreign_key_signatures(model.__table__)


def test_all_links_between_tenant_owned_rows_carry_organization_id() -> None:
    expected_links: Iterable[tuple[Table, tuple[str, ...], tuple[str, ...]]] = (
        (
            Session.__table__,
            ("organization_id", "membership_id", "user_id"),
            (
                "organization_membership.organization_id",
                "organization_membership.id",
                "organization_membership.user_id",
            ),
        ),
        (
            AgentAuthorization.__table__,
            ("organization_id", "user_id"),
            (
                "organization_membership.organization_id",
                "organization_membership.user_id",
            ),
        ),
        (
            OAuthState.__table__,
            ("organization_id", "credential_binding_id", "credential_binding_version"),
            (
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ),
        ),
        (
            OAuthState.__table__,
            ("organization_id", "resulting_session_id"),
            ("session.organization_id", "session.id"),
        ),
        (
            ExternalCourseBinding.__table__,
            ("organization_id", "course_id"),
            ("course.organization_id", "course.id"),
        ),
        (
            ExternalCourseBinding.__table__,
            ("organization_id", "credential_id", "credential_binding_version"),
            (
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ),
        ),
        (
            CourseRun.__table__,
            ("organization_id", "course_id"),
            ("course.organization_id", "course.id"),
        ),
        (
            CourseMembership.__table__,
            ("organization_id", "course_run_id"),
            ("course_run.organization_id", "course_run.id"),
        ),
        (
            DestinationBinding.__table__,
            ("organization_id", "course_run_id"),
            ("course_run.organization_id", "course_run.id"),
        ),
        (
            DestinationBinding.__table__,
            ("organization_id", "credential_id", "credential_binding_version"),
            (
                "external_credential.organization_id",
                "external_credential.id",
                "external_credential.binding_version",
            ),
        ),
    )
    for table, local_columns, remote_columns in expected_links:
        assert (local_columns, remote_columns) in _foreign_key_signatures(table)


def test_required_identity_and_roster_uniqueness_is_enforced_by_schema() -> None:
    assert ("provider", "issuer", "subject") in _unique_column_sets(
        ExternalIdentity.__table__
    )
    assert (
        "organization_id",
        "provider",
        "external_course_id",
        "binding_version",
    ) in _unique_column_sets(ExternalCourseBinding.__table__)
    assert (
        "organization_id",
        "course_run_id",
        "user_id",
        "kind",
    ) in _unique_column_sets(CourseMembership.__table__)
    assert ("organization_id", "user_id") in _unique_column_sets(
        OrganizationMembership.__table__
    )
    assert (
        "organization_id",
        "course_id",
        "external_run_id",
    ) in _unique_column_sets(CourseRun.__table__)


def test_session_and_authorization_persist_exact_revocation_versions() -> None:
    assert {
        "membership_id",
        "membership_revision",
        "auth_epoch",
        "token_digest",
        "expires_at",
        "revoked_at",
    } <= set(Session.__table__.columns.keys())
    assert {
        "user_id",
        "agent_id",
        "scopes",
        "membership_revision",
        "auth_epoch",
        "token_digest",
        "revision",
        "expires_at",
        "revoked_at",
    } <= set(AgentAuthorization.__table__.columns.keys())


def test_secrets_are_digest_only_or_ciphertext_only() -> None:
    identity_tables = (
        Invitation.__table__,
        Session.__table__,
        AgentAuthorization.__table__,
        OAuthState.__table__,
        ExternalCredential.__table__,
    )
    forbidden = {
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "secret",
        "pkce_verifier",
        "plaintext",
    }
    for table in identity_tables:
        assert forbidden.isdisjoint(table.columns.keys())

    assert "token_digest" in Invitation.__table__.columns
    assert "token_digest" in Session.__table__.columns
    assert "token_digest" in AgentAuthorization.__table__.columns
    assert "state_digest" in OAuthState.__table__.columns
    assert "pkce_verifier_ciphertext" in OAuthState.__table__.columns
    assert "ciphertext" in ExternalCredential.__table__.columns


def test_openapi_projection_fields_have_persisted_sources() -> None:
    assert {"id", "display_name", "status"} <= set(User.__table__.columns.keys())
    assert {"id", "user_id", "roles", "status", "revision", "auth_epoch"} <= set(
        OrganizationMembership.__table__.columns.keys()
    )
    assert {"id", "normalized_email", "role", "status", "expires_at", "revision"} <= set(
        Invitation.__table__.columns.keys()
    )
    assert {"id", "title", "status", "revision"} <= set(Course.__table__.columns.keys())
    assert {
        "id",
        "course_id",
        "external_run_id",
        "title",
        "timezone",
        "status",
        "revision",
    } <= set(CourseRun.__table__.columns.keys())


def test_alembic_identity_course_revision_is_preserved_below_current_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(scripts.get_heads()) == 1
    revision = scripts.get_revision("0002_identity_and_courses")
    assert revision is not None
    assert revision.down_revision == "0001_foundation"


@pytest.mark.infrastructure
@pytest.mark.anyio
async def test_mysql_rejects_cross_tenant_links_and_duplicate_external_identities(
    mysql_container: MySqlContainer,
) -> None:
    engine = create_database_engine(mysql_container.get_connection_url())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    org_a = UUID("00000000-0000-7000-8000-000000000901")
    org_b = UUID("00000000-0000-7000-8000-000000000902")
    user_id = UUID("00000000-0000-7000-8000-000000000903")
    membership_id = UUID("00000000-0000-7000-8000-000000000904")
    course_id = UUID("00000000-0000-7000-8000-000000000905")
    course_run_id = UUID("00000000-0000-7000-8000-000000000906")
    credential_a_id = UUID("00000000-0000-7000-8000-000000000907")
    credential_b_id = UUID("00000000-0000-7000-8000-000000000908")
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    try:
        async with foundation_test_transaction(engine) as session:
            session.add_all(
                [
                    Organization(id=org_a, slug="model-org-a", name="Model Org A"),
                    Organization(id=org_b, slug="model-org-b", name="Model Org B"),
                    User(id=user_id, display_name="Fixture User"),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    OrganizationMembership(
                        id=membership_id,
                        organization_id=org_a,
                        user_id=user_id,
                        roles=["methodologist"],
                    ),
                    Course(
                        id=course_id,
                        organization_id=org_a,
                        title="Fixture Course",
                        description="",
                        source_kind="external",
                    ),
                    ExternalCredential(
                        id=credential_a_id,
                        organization_id=org_a,
                        provider="stepik",
                        binding_version=1,
                        ciphertext="ciphertext-a",
                        key_id="key-a",
                    ),
                    ExternalCredential(
                        id=credential_b_id,
                        organization_id=org_b,
                        provider="stepik",
                        binding_version=1,
                        ciphertext="ciphertext-b",
                        key_id="key-b",
                    ),
                ]
            )
            await session.flush()
            session.add(
                CourseRun(
                    id=course_run_id,
                    organization_id=org_a,
                    course_id=course_id,
                    title="Fixture Run",
                    timezone="Europe/Moscow",
                    status="active",
                )
            )
            session.add(
                ExternalIdentity(
                    id=UUID("00000000-0000-7000-8000-000000000909"),
                    user_id=user_id,
                    provider="stepik",
                    issuer="https://stepik.org",
                    subject="fixture-user",
                )
            )
            await session.flush()
            session.add(
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000000910"),
                    organization_id=org_a,
                    course_run_id=course_run_id,
                    user_id=user_id,
                    kind="student",
                    source="imported",
                    status="active",
                    external_version="fixture-1",
                    joined_at=now,
                )
            )
            await session.flush()

            invalid_rows = (
                CourseRun(
                    id=UUID("00000000-0000-7000-8000-000000000911"),
                    organization_id=org_b,
                    course_id=course_id,
                    title="Cross Tenant Run",
                    timezone="UTC",
                ),
                Session(
                    id=UUID("00000000-0000-7000-8000-000000000912"),
                    organization_id=org_b,
                    user_id=user_id,
                    membership_id=membership_id,
                    membership_revision=0,
                    auth_epoch=0,
                    token_digest="sha256:" + "1" * 64,
                    expires_at=now + timedelta(hours=1),
                ),
                ExternalCourseBinding(
                    id=UUID("00000000-0000-7000-8000-000000000913"),
                    organization_id=org_a,
                    course_id=course_id,
                    provider="stepik",
                    external_course_id="cross-credential",
                    external_url="https://stepik.org/course/1",
                    provider_version="fixture-1",
                    credential_id=credential_b_id,
                    credential_binding_version=1,
                    binding_version=1,
                ),
                ExternalIdentity(
                    id=UUID("00000000-0000-7000-8000-000000000914"),
                    user_id=user_id,
                    provider="stepik",
                    issuer="https://stepik.org",
                    subject="fixture-user",
                ),
                CourseMembership(
                    id=UUID("00000000-0000-7000-8000-000000000915"),
                    organization_id=org_a,
                    course_run_id=course_run_id,
                    user_id=user_id,
                    kind="student",
                    source="imported",
                    status="active",
                    external_version="fixture-2",
                    joined_at=now,
                ),
            )
            for row in invalid_rows:
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush([row])
    finally:
        await engine.dispose()
