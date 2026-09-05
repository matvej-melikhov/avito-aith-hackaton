"""Real MySQL walk across every backend-core Alembic revision."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from testcontainers.mysql import MySqlContainer

pytestmark = [
    pytest.mark.infrastructure,
    pytest.mark.filterwarnings(
        "ignore:Computed default on review_iteration.initial_submission_version_id "
        "cannot be modified"
    ),
]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISIONS = (
    "0001_foundation",
    "0002_identity_and_courses",
    "0003_homework_versions",
    "0004_submissions_and_artifacts",
    "0005_review_spine",
    "0006_ai_review",
    "0007_human_review",
    "0008_delivery_recovery",
)

WORKSPACE_REVISIONS = (
    "0009_workspace_self_review",
    "0010_uploaded_artifacts",
    "0011_workspace_version_fields",
    "0012_workspace_durable",
    "0013_review_signal_choices",
    "0014_homework_check_settings",
    "0015_homework_sources",
)

ORG = UUID("00000000-0000-7000-8000-000000169001")
USER = UUID("00000000-0000-7000-8000-000000169002")
MEMBERSHIP = UUID("00000000-0000-7000-8000-000000169003")
CREDENTIAL = UUID("00000000-0000-7000-8000-000000169004")
COURSE = UUID("00000000-0000-7000-8000-000000169005")
RUN = UUID("00000000-0000-7000-8000-000000169006")
DESTINATION = UUID("00000000-0000-7000-8000-000000169007")
HOMEWORK = UUID("00000000-0000-7000-8000-000000169008")
HOMEWORK_VERSION = UUID("00000000-0000-7000-8000-000000169009")
CRITERION_SET = UUID("00000000-0000-7000-8000-000000169010")
CRITERION = UUID("00000000-0000-7000-8000-000000169011")
RUN_HOMEWORK = UUID("00000000-0000-7000-8000-000000169012")
HOMEWORK_PUBLICATION = UUID("00000000-0000-7000-8000-000000169013")
ARTIFACT_REFERENCE = UUID("00000000-0000-7000-8000-000000169014")
ARTIFACT_VERSION = UUID("00000000-0000-7000-8000-000000169015")
SUBMISSION = UUID("00000000-0000-7000-8000-000000169016")
SUBMISSION_VERSION = UUID("00000000-0000-7000-8000-000000169017")
REVIEW_CASE = UUID("00000000-0000-7000-8000-000000169018")
REVIEW_ITERATION = UUID("00000000-0000-7000-8000-000000169019")
REVIEW_REVISION = UUID("00000000-0000-7000-8000-000000169020")
AI_RUN = UUID("00000000-0000-7000-8000-000000169021")
REVIEW_PUBLICATION = UUID("00000000-0000-7000-8000-000000169022")
DELIVERY = UUID("00000000-0000-7000-8000-000000169023")
DELIVERY_ATTEMPT = UUID("00000000-0000-7000-8000-000000169024")
OBSERVATION = UUID("00000000-0000-7000-8000-000000169025")
RECEIPT = UUID("00000000-0000-7000-8000-000000169026")
REQUEST = UUID("00000000-0000-7000-8000-000000169027")
ARTIFACT_OPERATION = UUID("00000000-0000-7000-8000-000000169028")
DELIVERY_OPERATION = UUID("00000000-0000-7000-8000-000000169029")
CLAIM_TOKEN = UUID("00000000-0000-7000-8000-000000169030")

FOUNDATION_DIGEST = "sha256:" + "1" * 64
ARTIFACT_DIGEST = "sha256:" + "2" * 64
HOMEWORK_DIGEST = "sha256:" + "3" * 64
CRITERIA_DIGEST = "sha256:" + "4" * 64
INPUT_FINGERPRINT = "sha256:" + "5" * 64
PUBLICATION_FINGERPRINT = "sha256:" + "6" * 64
DELIVERY_PAYLOAD_DIGEST = "sha256:" + "7" * 64
RECONCILE_REQUEST_DIGEST = "sha256:" + "8" * 64
RECONCILE_RESULT_DIGEST = "sha256:" + "9" * 64
REVISION_BYTES = "Точная immutable revision: 01\nLine two."
CIPHERTEXT = "enc:v1:immutable-ciphertext"
NOW = "2026-09-05 12:00:00"
LATER = "2026-09-12 12:00:00"


def _u(value: UUID) -> str:
    return value.hex


def _run(connection: Connection, statement: str, values: Mapping[str, Any]) -> None:
    connection.execute(text(statement), dict(values))


def _config(async_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", async_url.replace("%", "%%"))
    return config


def _clean_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table_name in inspect(connection).get_table_names():
            connection.execute(text(f"DROP TABLE `{table_name}`"))
        connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _version(engine: Engine) -> str:
    with engine.connect() as connection:
        value = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(value, str)
    return value


def _scalar(engine: Engine, statement: str, values: Mapping[str, Any]) -> Any:
    with engine.connect() as connection:
        return connection.scalar(text(statement), dict(values))


def _insert_foundation(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO organization (id, slug, name, status, revision) "
            "VALUES (:id, 'migration-walk', 'Migration Walk', 'active', 7)",
            {"id": _u(ORG)},
        )
        for operation_id, kind in (
            (ARTIFACT_OPERATION, "artifact_capture"),
            (DELIVERY_OPERATION, "external_delivery"),
        ):
            _run(
                connection,
                "INSERT INTO operation "
                "(id, organization_id, kind, input_version, state, revision) "
                "VALUES (:id, :org, :kind, 'migration-walk:v1', 'succeeded', 3)",
                {"id": _u(operation_id), "org": _u(ORG), "kind": kind},
            )
        _run(
            connection,
            "INSERT INTO command_receipt "
            "(id, organization_id, idempotency_key, request_id, command_name, target_id, "
            "expected_revision, payload_digest, actor_snapshot, status, result_reference) "
            "VALUES (:id, :org, 'migration-walk', :request, 'walk', :target, 7, "
            ":digest, :actor, 'succeeded', :result)",
            {
                "id": _u(RECEIPT),
                "org": _u(ORG),
                "request": _u(REQUEST),
                "target": _u(COURSE),
                "digest": FOUNDATION_DIGEST,
                "actor": json.dumps({"kind": "fixture"}),
                "result": json.dumps({"operation_id": _u(ARTIFACT_OPERATION)}),
            },
        )


def _insert_identity_and_course(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO user (id, display_name, status) VALUES (:id, 'Migration User', 'active')",
            {"id": _u(USER)},
        )
        _run(
            connection,
            "INSERT INTO organization_membership "
            "(id, organization_id, user_id, roles, status, auth_epoch, revision) "
            "VALUES (:id, :org, :user, :roles, 'active', 2, 4)",
            {
                "id": _u(MEMBERSHIP),
                "org": _u(ORG),
                "user": _u(USER),
                "roles": json.dumps(["methodologist", "student"]),
            },
        )
        _run(
            connection,
            "INSERT INTO external_credential "
            "(id, organization_id, provider, binding_version, ciphertext, key_id, status) "
            "VALUES (:id, :org, 'github', 1, :ciphertext, 'migration-key', 'active')",
            {"id": _u(CREDENTIAL), "org": _u(ORG), "ciphertext": CIPHERTEXT},
        )
        _run(
            connection,
            "INSERT INTO course "
            "(id, organization_id, title, description, source_kind, status, revision) "
            "VALUES (:id, :org, 'Migration Course', '', 'standalone', 'active', 5)",
            {"id": _u(COURSE), "org": _u(ORG)},
        )
        _run(
            connection,
            "INSERT INTO course_run "
            "(id, organization_id, course_id, external_run_id, title, timezone, status, revision) "
            "VALUES (:id, :org, :course, NULL, 'Migration Run', 'UTC', 'active', 6)",
            {"id": _u(RUN), "org": _u(ORG), "course": _u(COURSE)},
        )
        _run(
            connection,
            "INSERT INTO destination_binding "
            "(id, organization_id, course_run_id, kind, binding_version, recipient_ref, "
            "credential_id, credential_binding_version, required, status, revision) "
            "VALUES (:id, :org, :run, 'github', 1, 'repo:migration', :credential, "
            "1, 1, 'active', 2)",
            {
                "id": _u(DESTINATION),
                "org": _u(ORG),
                "run": _u(RUN),
                "credential": _u(CREDENTIAL),
            },
        )


def _insert_homework(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO homework (id, organization_id, course_id, title, revision) "
            "VALUES (:id, :org, :course, 'Migration Homework', 4)",
            {"id": _u(HOMEWORK), "org": _u(ORG), "course": _u(COURSE)},
        )
        _run(
            connection,
            "INSERT INTO homework_version "
            "(id, organization_id, homework_id, version_number, student_text, max_score, "
            "artifact_kinds, estimated_review_minutes, revision) "
            "VALUES (:id, :org, :homework, 1, 'immutable requirements', 10.00, "
            ":kinds, 30, 1)",
            {
                "id": _u(HOMEWORK_VERSION),
                "org": _u(ORG),
                "homework": _u(HOMEWORK),
                "kinds": json.dumps(["repository"]),
            },
        )
        _run(
            connection,
            "INSERT INTO criterion_set (id, organization_id, homework_version_id) "
            "VALUES (:id, :org, :version)",
            {"id": _u(CRITERION_SET), "org": _u(ORG), "version": _u(HOMEWORK_VERSION)},
        )
        _run(
            connection,
            "INSERT INTO criterion "
            "(id, organization_id, criterion_set_id, stable_key, position, title, description, "
            "max_points, active) VALUES (:id, :org, :set_id, 'correctness', 0, "
            "'Correctness', 'Exact criterion', 10.00, 1)",
            {"id": _u(CRITERION), "org": _u(ORG), "set_id": _u(CRITERION_SET)},
        )
        _run(
            connection,
            "INSERT INTO course_run_homework "
            "(id, organization_id, course_run_id, homework_id, current_publication_id, "
            "status, revision) VALUES (:id, :org, :run, :homework, NULL, 'active', 3)",
            {"id": _u(RUN_HOMEWORK), "org": _u(ORG), "run": _u(RUN), "homework": _u(HOMEWORK)},
        )
        _run(
            connection,
            "INSERT INTO course_run_homework_publication "
            "(id, organization_id, course_run_homework_id, homework_id, homework_version_id, "
            "publication_sequence, submission_deadline, review_deadline, published_at) "
            "VALUES (:id, :org, :relation, :homework, :version, 1, :now, :later, :now)",
            {
                "id": _u(HOMEWORK_PUBLICATION),
                "org": _u(ORG),
                "relation": _u(RUN_HOMEWORK),
                "homework": _u(HOMEWORK),
                "version": _u(HOMEWORK_VERSION),
                "now": NOW,
                "later": LATER,
            },
        )
        _run(
            connection,
            "UPDATE course_run_homework SET current_publication_id=:publication WHERE id=:id",
            {"publication": _u(HOMEWORK_PUBLICATION), "id": _u(RUN_HOMEWORK)},
        )


def _insert_submission_spine(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO artifact_reference "
            "(id, organization_id, provider, credential_binding_id, credential_binding_version, "
            "original_url, locator, read_capability, feedback_capability, last_checked_at, "
            "revision) "
            "VALUES (:id, :org, 'github', :credential, 1, 'https://example.invalid/repo', "
            ":locator, 'available', 'available', :now, 2)",
            {
                "id": _u(ARTIFACT_REFERENCE),
                "org": _u(ORG),
                "credential": _u(CREDENTIAL),
                "locator": json.dumps({"repository": "migration/repo"}),
                "now": NOW,
            },
        )
        _run(
            connection,
            "INSERT INTO artifact_version "
            "(id, organization_id, artifact_reference_id, provider_version, content_digest, "
            "object_key, media_type, byte_size, captured_at, metadata) "
            "VALUES (:id, :org, :reference, 'commit-1', :digest, 'tenant/object', "
            "'application/zip', 42, :now, :metadata)",
            {
                "id": _u(ARTIFACT_VERSION),
                "org": _u(ORG),
                "reference": _u(ARTIFACT_REFERENCE),
                "digest": ARTIFACT_DIGEST,
                "now": NOW,
                "metadata": json.dumps({"immutable": True}),
            },
        )
        _run(
            connection,
            "INSERT INTO submission "
            "(id, organization_id, course_run_homework_id, course_run_id, homework_id, "
            "student_id, current_predeadline_version_id, revision) "
            "VALUES (:id, :org, :relation, :run, :homework, :student, NULL, 3)",
            {
                "id": _u(SUBMISSION),
                "org": _u(ORG),
                "relation": _u(RUN_HOMEWORK),
                "run": _u(RUN),
                "homework": _u(HOMEWORK),
                "student": _u(USER),
            },
        )
        _run(
            connection,
            "INSERT INTO submission_version "
            "(id, organization_id, submission_id, course_run_id, homework_id, sequence, "
            "homework_version_id, artifact_reference_id, artifact_version_id, submitted_at, "
            "effective_deadline, phase, status, capture_operation_id, revision) "
            "VALUES (:id, :org, :submission, :run, :homework, 1, :homework_version, "
            ":reference, :artifact, :now, :later, 'before_deadline', 'ready', :operation, 2)",
            {
                "id": _u(SUBMISSION_VERSION),
                "org": _u(ORG),
                "submission": _u(SUBMISSION),
                "run": _u(RUN),
                "homework": _u(HOMEWORK),
                "homework_version": _u(HOMEWORK_VERSION),
                "reference": _u(ARTIFACT_REFERENCE),
                "artifact": _u(ARTIFACT_VERSION),
                "now": NOW,
                "later": LATER,
                "operation": _u(ARTIFACT_OPERATION),
            },
        )
        _run(
            connection,
            "UPDATE submission SET current_predeadline_version_id=:version WHERE id=:id",
            {"version": _u(SUBMISSION_VERSION), "id": _u(SUBMISSION)},
        )
        _run(
            connection,
            "INSERT INTO review_case "
            "(id, organization_id, course_run_id, homework_id, student_id, "
            "current_iteration_id, revision) "
            "VALUES (:id, :org, :run, :homework, :student, NULL, 2)",
            {
                "id": _u(REVIEW_CASE),
                "org": _u(ORG),
                "run": _u(RUN),
                "homework": _u(HOMEWORK),
                "student": _u(USER),
            },
        )
        _run(
            connection,
            "INSERT INTO review_iteration "
            "(id, organization_id, review_case_id, course_run_id, homework_id, student_id, "
            "iteration_number, submission_version_id, artifact_version_id, homework_version_id, "
            "criterion_set_id, effective_deadline, responsible_reviewer_id, status, "
            "current_revision_id, predecessor_iteration_id, origin, revision) "
            "VALUES (:id, :org, :case_id, :run, :homework, :student, 1, :submission_version, "
            ":artifact, :homework_version, :criterion_set, :later, :student, 'ready_to_publish', "
            "NULL, NULL, 'initial', 4)",
            {
                "id": _u(REVIEW_ITERATION),
                "org": _u(ORG),
                "case_id": _u(REVIEW_CASE),
                "run": _u(RUN),
                "homework": _u(HOMEWORK),
                "student": _u(USER),
                "submission_version": _u(SUBMISSION_VERSION),
                "artifact": _u(ARTIFACT_VERSION),
                "homework_version": _u(HOMEWORK_VERSION),
                "criterion_set": _u(CRITERION_SET),
                "later": LATER,
            },
        )
        _run(
            connection,
            "UPDATE review_case SET current_iteration_id=:iteration WHERE id=:id",
            {"iteration": _u(REVIEW_ITERATION), "id": _u(REVIEW_CASE)},
        )


def _insert_review_revision(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO review_revision "
            "(id, organization_id, review_iteration_id, revision_number, author_user_id, "
            "base_revision_id, feedback, total_score, created_at) "
            "VALUES (:id, :org, :iteration, 1, :author, NULL, :feedback, 10.00, :now)",
            {
                "id": _u(REVIEW_REVISION),
                "org": _u(ORG),
                "iteration": _u(REVIEW_ITERATION),
                "author": _u(USER),
                "feedback": REVISION_BYTES,
                "now": NOW,
            },
        )
        _run(
            connection,
            "UPDATE review_iteration SET current_revision_id=:revision WHERE id=:id",
            {"revision": _u(REVIEW_REVISION), "id": _u(REVIEW_ITERATION)},
        )


def _insert_ai_run(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO ai_review_run "
            "(id, organization_id, review_iteration_id, course_run_id, submission_version_id, "
            "artifact_version_id, content_digest, homework_version_id, homework_digest, "
            "criterion_set_id, criteria_digest, contract_version, fingerprint_algorithm, "
            "input_fingerprint, status, current_attempt_no, finished_at, revision) "
            "VALUES (:id, :org, :iteration, :run, :submission, :artifact, :content, "
            ":homework_version, :homework_digest, :criterion_set, :criteria_digest, '1.1.0', "
            "'sha256', :fingerprint, 'succeeded', 0, :now, 3)",
            {
                "id": _u(AI_RUN),
                "org": _u(ORG),
                "iteration": _u(REVIEW_ITERATION),
                "run": _u(RUN),
                "submission": _u(SUBMISSION_VERSION),
                "artifact": _u(ARTIFACT_VERSION),
                "content": ARTIFACT_DIGEST,
                "homework_version": _u(HOMEWORK_VERSION),
                "homework_digest": HOMEWORK_DIGEST,
                "criterion_set": _u(CRITERION_SET),
                "criteria_digest": CRITERIA_DIGEST,
                "fingerprint": INPUT_FINGERPRINT,
                "now": NOW,
            },
        )


def _insert_publication_and_delivery(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO review_publication "
            "(id, organization_id, review_iteration_id, review_revision_id, "
            "publication_request_id, publication_version, published_by, published_at, "
            "status, revision) VALUES (:id, :org, :iteration, :revision, NULL, 1, "
            ":publisher, :now, 'published', 2)",
            {
                "id": _u(REVIEW_PUBLICATION),
                "org": _u(ORG),
                "iteration": _u(REVIEW_ITERATION),
                "revision": _u(REVIEW_REVISION),
                "publisher": _u(USER),
                "now": NOW,
            },
        )
        _run(
            connection,
            "INSERT INTO external_delivery "
            "(id, organization_id, publication_id, operation_id, delivery_key, "
            "destination_binding_id, binding_version, credential_binding_id, "
            "credential_binding_version, destination_kind, recipient_ref, course_run_id, "
            "homework_version_id, criterion_set_id, submission_version_id, artifact_version_id, "
            "artifact_content_digest, review_iteration_id, review_revision_id, contract_version, "
            "publication_fingerprint, payload_version, payload_digest, payload, state, "
            "attempt_count, revision) VALUES (:id, :org, :publication, :operation, "
            "'migration-delivery', :destination, 1, :credential, 1, 'github', "
            "'repo:migration', :run, :homework_version, :criterion_set, :submission, :artifact, "
            ":artifact_digest, :iteration, :review_revision, '1.1.0', :publication_fingerprint, "
            "'1.1.0', :payload_digest, :payload, 'succeeded', 1, 4)",
            {
                "id": _u(DELIVERY),
                "org": _u(ORG),
                "publication": _u(REVIEW_PUBLICATION),
                "operation": _u(DELIVERY_OPERATION),
                "destination": _u(DESTINATION),
                "credential": _u(CREDENTIAL),
                "run": _u(RUN),
                "homework_version": _u(HOMEWORK_VERSION),
                "criterion_set": _u(CRITERION_SET),
                "submission": _u(SUBMISSION_VERSION),
                "artifact": _u(ARTIFACT_VERSION),
                "artifact_digest": ARTIFACT_DIGEST,
                "iteration": _u(REVIEW_ITERATION),
                "review_revision": _u(REVIEW_REVISION),
                "publication_fingerprint": PUBLICATION_FINGERPRINT,
                "payload_digest": DELIVERY_PAYLOAD_DIGEST,
                "payload": json.dumps({"contract_version": "1.1.0"}),
            },
        )


def _insert_delivery_recovery(engine: Engine) -> None:
    with engine.begin() as connection:
        _run(
            connection,
            "INSERT INTO delivery_attempt "
            "(id, organization_id, delivery_id, external_delivery_id, operation_id, "
            "attempt_number, credential_binding_id, credential_binding_version, worker_identity, "
            "claim_token, lease_expires_at, state, started_at, finished_at, outcome) "
            "VALUES (:id, :org, :delivery, :delivery, :operation, 1, :credential, 1, "
            "'migration-worker', :token, :later, 'succeeded', :now, :now, 'succeeded')",
            {
                "id": _u(DELIVERY_ATTEMPT),
                "org": _u(ORG),
                "delivery": _u(DELIVERY),
                "operation": _u(DELIVERY_OPERATION),
                "credential": _u(CREDENTIAL),
                "token": _u(CLAIM_TOKEN),
                "now": NOW,
                "later": LATER,
            },
        )
        _run(
            connection,
            "INSERT INTO delivery_reconciliation_observation "
            "(id, organization_id, delivery_id, external_delivery_id, delivery_attempt_id, "
            "attempt_number, operation_id, credential_binding_id, credential_binding_version, "
            "request_payload_version, request_digest, result_payload_version, result_digest, "
            "observed_at, outcome) VALUES (:id, :org, :delivery, :delivery, :attempt, 1, "
            ":operation, :credential, 1, '1.1.0', :request_digest, '1.1.0', :result_digest, "
            ":now, 'succeeded')",
            {
                "id": _u(OBSERVATION),
                "org": _u(ORG),
                "delivery": _u(DELIVERY),
                "attempt": _u(DELIVERY_ATTEMPT),
                "operation": _u(DELIVERY_OPERATION),
                "credential": _u(CREDENTIAL),
                "request_digest": RECONCILE_REQUEST_DIGEST,
                "result_digest": RECONCILE_RESULT_DIGEST,
                "now": NOW,
            },
        )


def _assert_foundation_bytes(engine: Engine) -> None:
    assert (
        _scalar(
            engine,
            "SELECT payload_digest FROM command_receipt WHERE id=:id",
            {"id": _u(RECEIPT)},
        )
        == FOUNDATION_DIGEST
    )
    assert (
        _scalar(
            engine,
            "SELECT revision FROM organization WHERE id=:id",
            {"id": _u(ORG)},
        )
        == 7
    )


def _assert_revision_bytes(engine: Engine) -> None:
    feedback = _scalar(
        engine,
        "SELECT feedback FROM review_revision WHERE id=:id",
        {"id": _u(REVIEW_REVISION)},
    )
    assert isinstance(feedback, str)
    assert feedback.encode("utf-8") == REVISION_BYTES.encode("utf-8")


def _assert_publication_snapshot(engine: Engine) -> None:
    row = None
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT review_revision_id, publication_version, revision "
                "FROM review_publication WHERE id=:id"
            ),
            {"id": _u(REVIEW_PUBLICATION)},
        ).one()
    assert tuple(row) == (_u(REVIEW_REVISION), 1, 2)


def test_real_mysql_alembic_walk_preserves_supported_immutable_rows(
    mysql_container: MySqlContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_url = mysql_container.get_connection_url()
    async_url = sync_url.replace("mysql+pymysql://", "mysql+asyncmy://")
    engine = create_engine(sync_url, pool_pre_ping=True)
    config = _config(async_url)
    monkeypatch.setenv("REVIEW_PLATFORM_DATABASE_URL", async_url)
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [WORKSPACE_REVISIONS[-1]]
    ordered_revisions = tuple(
        revision.revision for revision in reversed(list(scripts.walk_revisions()))
    )
    assert ordered_revisions == REVISIONS + WORKSPACE_REVISIONS

    try:
        _clean_database(engine)
        introductions = {
            REVISIONS[0]: _insert_foundation,
            REVISIONS[1]: _insert_identity_and_course,
            REVISIONS[2]: _insert_homework,
            REVISIONS[3]: _insert_submission_spine,
            REVISIONS[4]: _insert_review_revision,
            REVISIONS[5]: _insert_ai_run,
            REVISIONS[6]: _insert_publication_and_delivery,
            REVISIONS[7]: _insert_delivery_recovery,
        }
        introduced_tables = (
            "command_receipt",
            "course",
            "homework_version",
            "artifact_version",
            "review_revision",
            "ai_review_run",
            "review_publication",
            "delivery_attempt",
        )
        for revision, table_name in zip(REVISIONS, introduced_tables, strict=True):
            command.upgrade(config, revision)
            assert _version(engine) == revision
            assert table_name in _tables(engine)
            introductions[revision](engine)
            _assert_foundation_bytes(engine)

        assert (
            _scalar(
                engine,
                "SELECT content_digest FROM artifact_version WHERE id=:id",
                {"id": _u(ARTIFACT_VERSION)},
            )
            == ARTIFACT_DIGEST
        )
        assert (
            _scalar(
                engine,
                "SELECT input_fingerprint FROM ai_review_run WHERE id=:id",
                {"id": _u(AI_RUN)},
            )
            == INPUT_FINGERPRINT
        )
        assert (
            _scalar(
                engine,
                "SELECT result_digest FROM delivery_reconciliation_observation WHERE id=:id",
                {"id": _u(OBSERVATION)},
            )
            == RECONCILE_RESULT_DIGEST
        )
        _assert_revision_bytes(engine)
        _assert_publication_snapshot(engine)
        for workspace_revision in WORKSPACE_REVISIONS:
            command.upgrade(config, workspace_revision)
            assert _version(engine) == workspace_revision
            _assert_foundation_bytes(engine)
            _assert_revision_bytes(engine)
            _assert_publication_snapshot(engine)
        command.check(config)

        downgrade_checks = (
            (REVISIONS[6], "delivery_attempt"),
            (REVISIONS[5], "review_publication"),
            (REVISIONS[4], "ai_review_run"),
            (REVISIONS[3], "review_revision"),
            (REVISIONS[2], "artifact_version"),
            (REVISIONS[1], "homework_version"),
            (REVISIONS[0], "course"),
        )
        for target, removed_table in downgrade_checks:
            command.downgrade(config, target)
            assert _version(engine) == target
            assert removed_table not in _tables(engine)
            _assert_foundation_bytes(engine)
            if target >= REVISIONS[4]:
                _assert_revision_bytes(engine)
            if target >= REVISIONS[6]:
                _assert_publication_snapshot(engine)

        command.downgrade(config, "base")
        assert _tables(engine) == {"alembic_version"}
        assert _scalar(engine, "SELECT COUNT(*) FROM alembic_version", {}) == 0
        assert "organization" not in _tables(engine)

        command.upgrade(config, "head")
        assert _version(engine) == WORKSPACE_REVISIONS[-1]
        assert set(introduced_tables).issubset(_tables(engine))
        for table_name in introduced_tables:
            assert _scalar(engine, f"SELECT COUNT(*) FROM `{table_name}`", {}) == 0
        command.check(config)
    finally:
        _clean_database(engine)
        engine.dispose()
