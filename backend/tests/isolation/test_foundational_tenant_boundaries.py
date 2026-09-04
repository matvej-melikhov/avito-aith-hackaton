from __future__ import annotations

import pytest

from review_platform.application.foundation_runtime import (
    BoundaryViolation,
    build_foundation_runtime,
)

pytestmark = pytest.mark.behavioral

ORG_A = "00000000-0000-7000-8000-000000000001"
ORG_B = "00000000-0000-7000-8000-000000000002"
ROW = "00000000-0000-7000-8000-000000000003"


@pytest.mark.parametrize("table", ["command_receipt", "operation", "audit_event", "outbox_message"])
def test_every_foundational_row_is_organization_scoped(table: str) -> None:
    runtime = build_foundation_runtime()
    created = runtime.insert_tenant_row(table=table, organization_id=ORG_A, references={})

    assert created["organization_id"] == ORG_A
    assert runtime.read_tenant_row(table=table, organization_id=ORG_B, row_id=created["id"]) is None


def test_composite_foreign_key_rejects_cross_tenant_reference() -> None:
    runtime = build_foundation_runtime()
    runtime.insert_tenant_row(table="operation", organization_id=ORG_A, references={})

    with pytest.raises(BoundaryViolation, match="organization"):
        runtime.insert_tenant_row(
            table="operation_attempt",
            organization_id=ORG_B,
            references={"operation": (ORG_A, ROW)},
        )


def test_redis_namespace_starts_with_the_organization_id() -> None:
    runtime = build_foundation_runtime()

    redis_key = runtime.redis_key(organization_id=ORG_A, category="operation", identity=ROW)

    assert redis_key.startswith(f"review-platform:{ORG_A}:")
    assert ORG_B not in redis_key


def test_s3_namespace_starts_with_organization_and_artifact_version() -> None:
    runtime = build_foundation_runtime()

    s3_key = runtime.s3_key(organization_id=ORG_A, artifact_version_id=ROW)

    assert s3_key.startswith(f"{ORG_A}/{ROW}/")
    assert ORG_B not in s3_key


def test_signed_artifact_url_requires_the_requesting_tenant_to_match() -> None:
    runtime = build_foundation_runtime()

    with pytest.raises(BoundaryViolation, match="organization"):
        runtime.sign_artifact_read(
            organization_id=ORG_A,
            artifact_version_id=ROW,
            requested_by_organization_id=ORG_B,
        )


def test_generic_api_read_never_falls_back_to_global_id_lookup() -> None:
    runtime = build_foundation_runtime()
    created = runtime.insert_tenant_row(table="operation", organization_id=ORG_A, references={})

    assert (
        runtime.read_tenant_row(table="operation", organization_id=ORG_B, row_id=created["id"])
        is None
    )
