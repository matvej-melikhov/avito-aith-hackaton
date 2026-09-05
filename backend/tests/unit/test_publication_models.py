"""Persistence invariants for review publication and durable delivery intents."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.sql.schema import Table

from review_platform.infrastructure.db.base import Base, UTCDateTime
from review_platform.infrastructure.db.models import (
    ExternalDelivery,
    PublicationRequest,
    ReviewImpactEvent,
    ReviewIterationRelation,
    ReviewPublication,
    ReviewRevision,
)
from review_platform.infrastructure.db.models.publication import (
    EXTERNAL_DELIVERY_STATES,
    PUBLICATION_REQUEST_STATUSES,
    REVIEW_ITERATION_RELATION_KINDS,
    REVIEW_PUBLICATION_STATUSES,
)


def _unique_sets(table: Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key_pairs(table: Table) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def _check_sql(table: Table) -> str:
    return " ".join(
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )


def test_publication_models_are_registered_without_mutating_review_revision() -> None:
    expected = {
        "review_iteration_relation": ReviewIterationRelation.__table__,
        "review_impact_event": ReviewImpactEvent.__table__,
        "publication_request": PublicationRequest.__table__,
        "review_publication": ReviewPublication.__table__,
        "external_delivery": ExternalDelivery.__table__,
    }
    assert {name: Base.metadata.tables[name] for name in expected} == expected
    assert {
        "status",
        "published_at",
        "publication_id",
        "publication_request_id",
        "updated_at",
    }.isdisjoint(ReviewRevision.__table__.columns.keys())


def test_successor_relation_is_append_only_and_relation_local() -> None:
    table = ReviewIterationRelation.__table__
    assert (
        "organization_id",
        "predecessor_iteration_id",
        "successor_iteration_id",
    ) in _unique_sets(table)
    foreign_keys = _foreign_key_pairs(table)
    assert (
        ("organization_id", "review_case_id", "predecessor_iteration_id"),
        (
            "review_iteration.organization_id",
            "review_iteration.review_case_id",
            "review_iteration.id",
        ),
    ) in foreign_keys
    assert (
        ("organization_id", "review_case_id", "successor_iteration_id"),
        (
            "review_iteration.organization_id",
            "review_iteration.review_case_id",
            "review_iteration.id",
        ),
    ) in foreign_keys
    checks = _check_sql(table)
    assert REVIEW_ITERATION_RELATION_KINDS == ("correction", "requirements_migration")
    assert all(f"'{kind}'" in checks for kind in REVIEW_ITERATION_RELATION_KINDS)
    assert "predecessor_iteration_id <> successor_iteration_id" in checks
    assert all(column.onupdate is None for column in table.columns)
    assert {"revision", "updated_at"}.isdisjoint(table.columns.keys())


def test_review_impact_is_idempotent_append_only_provenance() -> None:
    table = ReviewImpactEvent.__table__
    assert {
        "source_event_id",
        "review_case_id",
        "review_iteration_id",
        "course_run_homework_id",
        "course_run_id",
        "homework_id",
        "previous_homework_version_id",
        "current_homework_version_id",
        "previous_publication_id",
        "current_publication_id",
        "publication_sequence",
        "occurred_at",
        "resolved_by_iteration_id",
        "resolved_at",
    } <= set(table.columns.keys())
    uniques = _unique_sets(table)
    assert ("organization_id", "source_event_id", "review_iteration_id") in uniques
    assert (
        "organization_id",
        "review_iteration_id",
        "current_homework_version_id",
    ) in uniques
    foreign_keys = _foreign_key_pairs(table)
    assert (
        ("organization_id", "source_event_id"),
        ("outbox_message.organization_id", "outbox_message.message_id"),
    ) in foreign_keys
    assert (
        ("organization_id", "review_case_id", "review_iteration_id"),
        (
            "review_iteration.organization_id",
            "review_iteration.review_case_id",
            "review_iteration.id",
        ),
    ) in foreign_keys
    assert all(column.onupdate is None for column in table.columns)
    assert isinstance(table.c.occurred_at.type, UTCDateTime)
    assert table.c.resolved_at.nullable
    assert table.c.resolved_by_iteration_id.nullable


def test_publication_request_is_agent_provenance_without_delivery_side_effect() -> None:
    table = PublicationRequest.__table__
    assert (
        "organization_id",
        "id",
        "review_iteration_id",
        "review_revision_id",
    ) in _unique_sets(table)
    assert ("organization_id", "idempotency_key") in _unique_sets(table)
    assert {
        "requested_by_user_id",
        "agent_id",
        "agent_authorization_id",
        "expires_at",
        "confirmed_by",
        "confirmed_at",
        "status",
        "revision",
    } <= set(table.columns.keys())
    assert {"delivery_id", "publication_id", "published_at"}.isdisjoint(table.columns.keys())
    checks = _check_sql(table)
    assert PUBLICATION_REQUEST_STATUSES == (
        "pending",
        "confirmed",
        "rejected",
        "expired",
        "superseded",
    )
    assert all(f"'{status}'" in checks for status in PUBLICATION_REQUEST_STATUSES)
    assert "revision >= 0" in checks
    assert table.c.agent_id.nullable
    assert table.c.agent_authorization_id.nullable
    assert "agent_id IS NULL AND agent_authorization_id IS NULL" in checks
    assert "agent_id IS NOT NULL AND agent_authorization_id IS NOT NULL" in checks
    foreign_keys = _foreign_key_pairs(table)
    assert (
        ("organization_id", "review_iteration_id", "review_revision_id"),
        (
            "review_revision.organization_id",
            "review_revision.review_iteration_id",
            "review_revision.id",
        ),
    ) in foreign_keys
    assert (
        ("organization_id", "agent_authorization_id"),
        ("agent_authorization.organization_id", "agent_authorization.id"),
    ) in foreign_keys


def test_review_publication_is_terminal_human_record_for_exact_revision() -> None:
    table = ReviewPublication.__table__
    uniques = _unique_sets(table)
    assert ("organization_id", "review_iteration_id", "review_revision_id") in uniques
    assert ("organization_id", "review_iteration_id", "publication_version") in uniques
    assert ("organization_id", "publication_request_id") in uniques
    assert REVIEW_PUBLICATION_STATUSES == ("published",)
    assert "'published'" in _check_sql(table)
    assert not table.c.published_by.nullable
    assert isinstance(table.c.published_at.type, UTCDateTime)
    assert "updated_at" not in table.columns

    foreign_keys = _foreign_key_pairs(table)
    assert (
        (
            "organization_id",
            "publication_request_id",
            "review_iteration_id",
            "review_revision_id",
        ),
        (
            "publication_request.organization_id",
            "publication_request.id",
            "publication_request.review_iteration_id",
            "publication_request.review_revision_id",
        ),
    ) in foreign_keys
    assert (
        ("organization_id", "published_by"),
        (
            "organization_membership.organization_id",
            "organization_membership.user_id",
        ),
    ) in foreign_keys


def test_external_delivery_has_exact_destination_and_publication_snapshot() -> None:
    table = ExternalDelivery.__table__
    assert {
        "publication_id",
        "operation_id",
        "delivery_key",
        "destination_binding_id",
        "binding_version",
        "credential_binding_id",
        "credential_binding_version",
        "destination_kind",
        "recipient_ref",
        "course_run_id",
        "homework_version_id",
        "criterion_set_id",
        "submission_version_id",
        "artifact_version_id",
        "artifact_content_digest",
        "review_iteration_id",
        "review_revision_id",
        "contract_version",
        "publication_fingerprint",
        "payload_version",
        "payload_digest",
        "payload",
    } <= set(table.columns.keys())
    assert (
        "organization_id",
        "publication_id",
        "destination_binding_id",
        "binding_version",
        "payload_version",
    ) in _unique_sets(table)
    assert ("organization_id", "operation_id") in _unique_sets(table)
    assert ("organization_id", "delivery_key") in _unique_sets(table)

    snapshot_columns = {
        "publication_id",
        "destination_binding_id",
        "binding_version",
        "credential_binding_id",
        "credential_binding_version",
        "destination_kind",
        "recipient_ref",
        "course_run_id",
        "homework_version_id",
        "criterion_set_id",
        "submission_version_id",
        "artifact_version_id",
        "artifact_content_digest",
        "review_iteration_id",
        "review_revision_id",
        "contract_version",
        "publication_fingerprint",
        "payload_version",
        "payload_digest",
        "payload",
    }
    assert all(table.c[name].onupdate is None for name in snapshot_columns)


def test_external_delivery_lifecycle_and_tenant_version_fks_are_exact() -> None:
    table = ExternalDelivery.__table__
    checks = _check_sql(table)
    assert EXTERNAL_DELIVERY_STATES == (
        "pending",
        "processing",
        "retryable_failed",
        "unknown_outcome",
        "reconciling",
        "succeeded",
        "action_required",
        "superseded",
    )
    assert all(f"'{state}'" in checks for state in EXTERNAL_DELIVERY_STATES)
    assert "attempt_count >= 0" in checks
    assert "binding_version >= 1" in checks
    assert "credential_binding_version >= 1" in checks
    assert "revision >= 0" in checks

    foreign_keys = _foreign_key_pairs(table)
    assert (
        ("organization_id", "destination_binding_id", "binding_version"),
        (
            "destination_binding.organization_id",
            "destination_binding.id",
            "destination_binding.binding_version",
        ),
    ) in foreign_keys
    assert (
        ("organization_id", "credential_binding_id", "credential_binding_version"),
        (
            "external_credential.organization_id",
            "external_credential.id",
            "external_credential.binding_version",
        ),
    ) in foreign_keys
    assert (
        (
            "organization_id",
            "publication_id",
            "review_iteration_id",
            "review_revision_id",
        ),
        (
            "review_publication.organization_id",
            "review_publication.id",
            "review_publication.review_iteration_id",
            "review_publication.review_revision_id",
        ),
    ) in foreign_keys


def test_every_owned_reference_to_a_tenant_table_carries_organization_id() -> None:
    tenant_tables = {
        "agent_authorization",
        "artifact_version",
        "course_run",
        "course_run_homework",
        "course_run_homework_publication",
        "criterion_set",
        "destination_binding",
        "external_credential",
        "homework",
        "homework_version",
        "operation",
        "organization_membership",
        "outbox_message",
        "publication_request",
        "review_iteration",
        "review_publication",
        "review_revision",
        "submission_version",
    }
    tables = (
        ReviewIterationRelation.__table__,
        ReviewImpactEvent.__table__,
        PublicationRequest.__table__,
        ReviewPublication.__table__,
        ExternalDelivery.__table__,
    )
    for table in tables:
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            referred_tables = {
                element.target_fullname.split(".", maxsplit=1)[0] for element in constraint.elements
            }
            if referred_tables & tenant_tables:
                local_columns = {element.parent.name for element in constraint.elements}
                remote_columns = {
                    element.target_fullname.rsplit(".", maxsplit=1)[1]
                    for element in constraint.elements
                }
                assert "organization_id" in local_columns
                assert "organization_id" in remote_columns
