"""Release gate for enumerated mutation audit coverage and safe provenance."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from review_platform.application.audit import AuditEventDraft, AuditRecorder
from review_platform.application.request_context import RequestActor
from review_platform.infrastructure.db.adapters import SqlAppendOnlyAuditRepository
from review_platform.infrastructure.db.models.operations import AuditEvent
from review_platform.infrastructure.db.models.organization import Organization
from review_platform.infrastructure.db.session import AsyncSessionFactory, session_scope

BACKEND_ROOT = Path.cwd().resolve()
SOURCE_ROOT = BACKEND_ROOT / "src" / "review_platform"

ORG = UUID("00000000-0000-7000-8000-000000193001")
USER = UUID("00000000-0000-7000-8000-000000193002")
AGENT = UUID("00000000-0000-7000-8000-000000193003")
AUTHORIZATION = UUID("00000000-0000-7000-8000-000000193004")
ENTITY = UUID("00000000-0000-7000-8000-000000193005")
REQUEST = UUID("00000000-0000-7000-8000-000000193006")
TRACE = UUID("00000000-0000-7000-8000-000000193007")
EVENT = UUID("00000000-0000-7000-8000-000000193008")
SECRET = "audit-release-gate-bearer-secret"


@dataclass(frozen=True, slots=True)
class AuditExpectation:
    category: str
    action: str
    module: str
    entrypoint: str


AUDIT_MATRIX = (
    AuditExpectation(
        "review",
        "open_review_iteration",
        "application/services/review_iterations.py",
        "open",
    ),
    AuditExpectation(
        "review-score",
        "save_review_revision",
        "application/services/review_drafts.py",
        "save",
    ),
    AuditExpectation(
        "review",
        "migrate_review_requirements",
        "application/services/review_requirements.py",
        "migrate",
    ),
    AuditExpectation(
        "review",
        "create_review_correction",
        "application/services/review_corrections.py",
        "create",
    ),
    AuditExpectation(
        "review",
        "start_ai_review",
        "application/services/ai_review_start.py",
        "start",
    ),
    AuditExpectation(
        "role",
        "create_invitation",
        "application/services/invitations.py",
        "issue",
    ),
    AuditExpectation(
        "role",
        "consume_invitation",
        "application/services/invitations.py",
        "consume",
    ),
    AuditExpectation(
        "role",
        "revoke_invitation",
        "application/services/invitations.py",
        "revoke",
    ),
    AuditExpectation(
        "role",
        "change_membership_roles",
        "application/services/memberships.py",
        "change_roles",
    ),
    AuditExpectation(
        "course",
        "start_course_import",
        "api/routes/identity_courses.py",
        "start_course_import",
    ),
    AuditExpectation(
        "course-archive",
        "archive_course",
        "application/services/courses.py",
        "archive_course",
    ),
    AuditExpectation(
        "course-archive",
        "restore_course",
        "application/services/courses.py",
        "restore_course",
    ),
    AuditExpectation(
        "course-archive",
        "archive_course_run",
        "application/services/courses.py",
        "archive_course_run",
    ),
    AuditExpectation(
        "course-archive",
        "restore_course_run",
        "application/services/courses.py",
        "restore_course_run",
    ),
    AuditExpectation(
        "course",
        "create_homework",
        "application/services/homeworks.py",
        "create_homework",
    ),
    AuditExpectation(
        "course",
        "create_homework_version",
        "application/services/homeworks.py",
        "create_version",
    ),
    AuditExpectation(
        "course",
        "publish_homework_version",
        "application/services/homeworks.py",
        "publish_version",
    ),
    AuditExpectation(
        "agent",
        "grant_agent_authorization",
        "application/services/agent_authorizations.py",
        "grant",
    ),
    AuditExpectation(
        "agent",
        "revoke_agent_authorization",
        "application/services/agent_authorizations.py",
        "revoke",
    ),
    AuditExpectation(
        "responsibility",
        "record_review_responsibility",
        "application/services/review_responsibility.py",
        "record",
    ),
    AuditExpectation(
        "publication",
        "request_review_publication",
        "application/services/publication_requests.py",
        "request",
    ),
    AuditExpectation(
        "publication-score",
        "publish_review",
        "application/services/review_publication.py",
        "publish",
    ),
    AuditExpectation(
        "artifact",
        "preflight_submission",
        "application/services/artifact_preflight.py",
        "preflight",
    ),
    AuditExpectation(
        "artifact",
        "submit_work",
        "application/services/submissions.py",
        "submit_work",
    ),
    AuditExpectation(
        "artifact",
        "capture_artifact",
        "application/services/artifact_capture.py",
        "capture",
    ),
    AuditExpectation(
        "artifact",
        "promote_artifact",
        "infrastructure/object_storage/promotions.py",
        "promote",
    ),
    AuditExpectation(
        "delivery",
        "claim_delivery",
        "application/services/deliveries.py",
        "claim",
    ),
    AuditExpectation(
        "delivery",
        "record_delivery_result",
        "application/services/deliveries.py",
        "record_worker_result",
    ),
    AuditExpectation(
        "delivery",
        "reconcile_delivery",
        "application/services/deliveries.py",
        "apply_reconciliation",
    ),
    AuditExpectation(
        "delivery",
        "retry_delivery",
        "application/services/deliveries.py",
        "manual_recovery",
    ),
    AuditExpectation(
        "delivery",
        "supersede_delivery",
        "application/services/deliveries.py",
        "supersede",
    ),
)


def test_every_consequential_mutation_has_an_audit_path() -> None:
    categories = {expectation.category.split("-", maxsplit=1)[0] for expectation in AUDIT_MATRIX}
    assert categories == {
        "review",
        "role",
        "course",
        "agent",
        "responsibility",
        "publication",
        "artifact",
        "delivery",
    }
    assert len({expectation.action for expectation in AUDIT_MATRIX}) == len(AUDIT_MATRIX)

    missing: list[str] = []
    for expectation in AUDIT_MATRIX:
        source_path = SOURCE_ROOT / expectation.module
        if not source_path.is_file():
            missing.append(f"{expectation.category}:{expectation.action}:missing_module")
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        functions = _functions(tree)
        roots = functions.get(expectation.entrypoint, ())
        if not roots or not any(_reaches_audit(root, functions, visited=set()) for root in roots):
            missing.append(f"{expectation.category}:{expectation.action}:no_audit_path")

    assert missing == [], "missing mutation audit coverage:\n" + "\n".join(missing)


@pytest.mark.anyio
@pytest.mark.infrastructure
async def test_sql_audit_preserves_server_actor_versions_outcome_and_no_secrets(
    foundation_session_factory: AsyncSessionFactory,
) -> None:
    async with session_scope(foundation_session_factory) as session:
        session.add(Organization(id=ORG, slug="audit-coverage", name="Audit Coverage"))
    actor = RequestActor.agent(
        organization_id=ORG,
        user_id=USER,
        roles={"reviewer"},
        membership_revision=7,
        auth_epoch=5,
        agent_id=AGENT,
        agent_authorization_id=AUTHORIZATION,
        agent_authorization_revision=4,
        scopes={"reviews:write"},
    )
    async with session_scope(foundation_session_factory) as session:
        await AuditRecorder(
            SqlAppendOnlyAuditRepository(),
            event_id_factory=lambda: EVENT,
            clock=lambda: _aware_now(),
        ).record(
            AuditEventDraft(
                organization_id=ORG,
                actor=actor,
                action="save_review_revision",
                entity_type="review_iteration",
                entity_id=ENTITY,
                before_revision=7,
                after_revision=8,
                request_id=REQUEST,
                trace_id=TRACE,
                outcome="succeeded",
                details={
                    "authorization": f"Bearer {SECRET}",
                    "magic_link": f"https://example.test/invite?token={SECRET}",
                    "provider_response": {"body": SECRET, "safe_code": "accepted"},
                    "decision_count": 3,
                },
            ),
            transaction=session,
        )

    async with foundation_session_factory() as session:
        stored = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == ORG,
                AuditEvent.id == EVENT,
            )
        )
    assert stored is not None
    assert (
        stored.organization_id,
        stored.actor_type,
        stored.actor_user_id,
        stored.agent_id,
        stored.agent_authorization_id,
    ) == (ORG, "agent", USER, AGENT, AUTHORIZATION)
    assert (
        stored.action,
        stored.entity_type,
        stored.entity_id,
        stored.before_revision,
        stored.after_revision,
        stored.request_id,
        stored.trace_id,
        stored.outcome,
    ) == (
        "save_review_revision",
        "review_iteration",
        ENTITY,
        7,
        8,
        REQUEST,
        TRACE,
        "succeeded",
    )
    rendered = json.dumps(stored.sanitized_details, sort_keys=True)
    assert SECRET not in rendered
    assert "authorization" not in stored.sanitized_details
    assert "magic_link" not in stored.sanitized_details
    assert stored.sanitized_details == {
        "provider_response": {"safe_code": "accepted"},
        "decision_count": 3,
    }


def _functions(tree: ast.AST) -> dict[str, tuple[ast.AsyncFunctionDef | ast.FunctionDef, ...]]:
    found: dict[str, list[ast.AsyncFunctionDef | ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            found.setdefault(node.name, []).append(node)
    return {name: tuple(nodes) for name, nodes in found.items()}


def _reaches_audit(
    function: ast.AsyncFunctionDef | ast.FunctionDef,
    functions: dict[str, tuple[ast.AsyncFunctionDef | ast.FunctionDef, ...]],
    *,
    visited: set[str],
) -> bool:
    identity = f"{function.lineno}:{function.name}"
    if identity in visited:
        return False
    visited.add(identity)
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    if any(_is_audit_call(call.func) for call in calls):
        return True
    for call in calls:
        called_name = _called_name(call.func)
        if called_name is None:
            continue
        for candidate in functions.get(called_name, ()):
            if _reaches_audit(candidate, functions, visited=visited):
                return True
    return False


def _is_audit_call(function: ast.expr) -> bool:
    rendered = ast.unparse(function)
    return (
        rendered in {"AuditEvent", "AuditEventDraft"}
        or "._audit.record" in rendered
        or rendered.endswith(".record_open")
    )


def _called_name(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _aware_now() -> datetime:
    return datetime(2026, 9, 5, 18, tzinfo=UTC)
