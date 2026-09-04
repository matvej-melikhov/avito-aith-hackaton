---
description: "Dependency-ordered TDD implementation tasks for backend core"
---

# Tasks: Backend Core Review Platform

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `context-traceability.md`, `requirements-traceability.md`, `contracts/`, `quickstart.md`, `.specify/memory/constitution.md`

**Contract freeze**: `contracts/manifest.json` freezes contract set 1.0.0 before application implementation. Story phases MUST NOT edit canonical contracts; a semantic change requires a separately approved contract version.

**TDD rule**: Each RED task must fail for the named missing behavior, never for collection/import/infrastructure errors. Its paired implementation must make it green before the phase checkpoint.

## Phase 1: Setup — runnable isolated environment

- [ ] T001 Create Python 3.13 project metadata, exact runtime/test dependencies, pytest markers, Ruff, and mypy configuration in `backend/pyproject.toml`
- [ ] T002 Generate the reproducible dependency lock without committing or pushing in `backend/uv.lock`
- [ ] T003 Create the importable package and application version in `backend/src/review_platform/__init__.py`
- [ ] T004 [P] Implement environment-only typed settings with safe offline defaults in `backend/src/review_platform/settings.py`
- [ ] T005 [P] Define non-secret local ports, limits, retention, encryption-key references, and live-sandbox variables in `deploy/env.example`
- [ ] T006 Create the Python 3.13 image and console entrypoints for API, worker, relay, email worker, bootstrap, and MCP in `backend/Dockerfile`
- [ ] T007 Define MySQL 8.4, Redis, MinIO, Mailpit, API, worker, outbox relay, email worker, and MCP health checks in `deploy/compose.yaml`
- [ ] T008 Implement deterministic clock/UUID fixtures and opt-in live-test guards in `backend/tests/conftest.py`
- [ ] T009 Implement isolated Testcontainers lifecycle, readiness, cleanup, and per-suite MySQL/Redis/MinIO fixtures in `backend/tests/fixtures/containers.py`

**Checkpoint**: locked uv sync and empty pytest collection succeed; no external URL is contacted.

---

## Phase 2: Frozen contracts and foundational persistence

**Purpose**: Make only shared contract, operation, idempotency, audit, outbox, storage, and request-boundary tests green. Concrete MCP invocation and story behavior are deliberately excluded.

### RED specifications

- [ ] T010 [P] Write and pass a traceability validator for FR-001..FR-085 and SC-001..SC-023 requiring fixture/gate, owner, command, and status in `backend/tests/contract/test_requirements_traceability.py`
- [ ] T011 [P] Write failing manifest and deterministic runtime-copy hash tests for all frozen contract files in `backend/tests/contract/test_contract_manifest.py`
- [ ] T012 [P] Write failing Draft 2020-12 tests for all 27 command variants, conditional payloads, actor union, version, and additional-property rejection in `backend/tests/contract/test_command_schema.py`
- [ ] T013 [P] Write failing OpenAPI tests for 43 paths, 45 operations, response schemas, security, command mappings, examples, and external references in `backend/tests/contract/test_openapi.py`
- [ ] T014 [P] Write failing static MCP manifest tests for 11 tools, closed scopes, roles, REST mappings, typed results, stateless metadata, and absence of direct publication in `backend/tests/contract/test_mcp_manifest.py`
- [ ] T015 [P] Write failing course-import, artifact-provider, delivery/reconciliation, and email schema/fixture tests in `backend/tests/contract/test_provider_contracts.py`
- [ ] T016 [P] Write failing wire-to-application tests proving actor/organization come only from user, agent, component, or installation-operator authorization in `backend/tests/contract/test_command_boundary.py`
- [ ] T017 [P] Write failing state tests for CommandReceipt, Operation/Attempt, Outbox lease, and terminal failure visibility in `backend/tests/state/test_foundational_operations.py`
- [ ] T018 [P] Write failing multi-relay tests for `SKIP LOCKED`, lease tokens, expiry recovery, duplicate messages, exhausted attempts, and actionable poison messages in `backend/tests/isolation/test_outbox_leasing.py`
- [ ] T019 [P] Write failing S3 boundary tests for tenant prefixes, byte ceilings, digest verification, signed URL TTL, staged promotion, and orphan cleanup in `backend/tests/isolation/test_object_storage_limits.py`
- [ ] T020 [P] Write failing log/error tests for token, PII, provider body, artifact content, and magic-link redaction in `backend/tests/isolation/test_secret_redaction.py`

### Shared implementation

- [ ] T021 Implement deterministic contract copy/check tooling driven exclusively by `contracts/manifest.json` in `scripts/sync_backend_contracts.py`
- [ ] T022 Package manifest-verified schemas as runtime resources in `backend/src/review_platform/contracts/schemas/`
- [ ] T023 Implement schema loading, reference resolution, version selection, and generated-schema conformance in `backend/src/review_platform/contracts/registry.py`
- [ ] T024 Implement strict Pydantic wire/application commands and user/agent/operator actor variants in `backend/src/review_platform/contracts/commands.py`
- [ ] T025 [P] Implement UUIDv7, UTC clock, digest, revision, and sanitized error primitives in `backend/src/review_platform/domain/primitives.py`
- [ ] T026 Configure asyncmy SQLAlchemy engine, sessions, naming conventions, and test transactions in `backend/src/review_platform/infrastructure/db/session.py`
- [ ] T027 Implement organization-key mixins and composite tenant foreign-key helpers in `backend/src/review_platform/infrastructure/db/base.py`
- [ ] T028 Implement CommandReceipt, AuditEvent, OutboxMessage, Operation, and OperationAttempt tables in `backend/src/review_platform/infrastructure/db/models/operations.py`
- [ ] T029 Configure async Alembic and create reversible foundational operations migration with explicit root revision in `backend/migrations/versions/0001_foundational_operations.py`
- [ ] T030 Implement tenant-scoped operation, receipt, audit, and outbox repositories in `backend/src/review_platform/infrastructure/db/repositories/operations.py`
- [ ] T031 Implement authenticated request context and read/write/worker auth-epoch revalidation in `backend/src/review_platform/application/request_context.py`
- [ ] T032 Implement role/scope authorization, closed-scope validation, and fail-closed tenant checks in `backend/src/review_platform/application/authorization.py`
- [ ] T033 Implement shared validation, transaction, CAS, and transport-independent dispatch in `backend/src/review_platform/application/command_bus.py`
- [ ] T034 Implement idempotency reservation, replay, payload-conflict rejection, and stable result references in `backend/src/review_platform/application/idempotency.py`
- [ ] T035 Implement append-only audit creation and atomic rollback semantics in `backend/src/review_platform/application/audit.py`
- [ ] T036 Implement stable outbox message creation, leasing, max-attempt visibility, and manual recovery in `backend/src/review_platform/infrastructure/db/outbox.py`
- [ ] T037 Implement `FOR UPDATE SKIP LOCKED` relay, compare-and-set lease completion, Redis publication, and crash recovery in `backend/src/review_platform/infrastructure/tasks/outbox_relay.py`
- [ ] T038 Configure Taskiq routing, bounded retry/backoff, tenant correlation, and worker auth revalidation in `backend/src/review_platform/infrastructure/tasks/broker.py`
- [ ] T039 Implement tenant-scoped S3 staged upload, limits, digest/promotion, signed reads, and garbage collection in `backend/src/review_platform/infrastructure/object_storage/s3.py`
- [ ] T040 Implement schema-backed provider ports and deterministic shared mocks/replays in `backend/src/review_platform/application/ports/providers.py` and `backend/src/review_platform/infrastructure/providers/mocks.py`
- [ ] T041 Implement sanitized FastAPI middleware, application factory, generic operation read route, and health/readiness probes in `backend/src/review_platform/api/middleware.py`, `backend/src/review_platform/api/routes/operations.py`, and `backend/src/review_platform/main.py`
- [ ] T042 Implement executable worker, relay, and email-worker process entrypoints in `backend/src/review_platform/infrastructure/tasks/__main__.py`, `backend/src/review_platform/infrastructure/tasks/relay_main.py`, and `backend/src/review_platform/infrastructure/tasks/email_main.py`

**Checkpoint**: T010-T020 are green. No user-story or concrete MCP transport test is required to complete Foundation.

---

## Phase 3: User Story 1 — organization and courses (P1, MVP)

**Independent Test**: Activate exact bootstrap identity, authenticate, import a roster, read it, add/remove roles safely, and archive/restore both Course and CourseRun.

- [ ] T043 [P] [US1] Write failing bootstrap/operator actor, session, and recovery contract tests in `backend/tests/contract/test_identity_api.py`
- [ ] T044 [P] [US1] Write failing course, CourseRun, roster-read, archive/restore, and operation-response contract tests in `backend/tests/contract/test_course_api.py`
- [ ] T045 [P] [US1] Write failing bootstrap, invitation-email, import-retry, roster-upsert, and history acceptance tests in `backend/tests/integration/test_organization_course_lifecycle.py`
- [ ] T046 [P] [US1] Write failing last-methodologist, invitation-consume, and membership-revocation race tests across REST read/write and claimed/unclaimed jobs in `backend/tests/isolation/test_identity_concurrency.py`
- [ ] T047 [US1] Implement Organization, User, ExternalIdentity, Membership, Invitation, Session, AgentAuthorization/token, credentials, Course, CourseRun, and CourseMembership tables in `backend/src/review_platform/infrastructure/db/models/identity_learning.py`
- [ ] T048 [US1] Create reversible identity/course migration with `down_revision=0001_foundational_operations` in `backend/migrations/versions/0002_identity_and_courses.py`
- [ ] T049 [P] [US1] Implement tenant-scoped identity/course repositories and encrypted credential access in `backend/src/review_platform/infrastructure/db/repositories/identity_learning.py`
- [ ] T050 [US1] Implement one-time exact-identity bootstrap and installation-operator recovery in `backend/src/review_platform/application/services/bootstrap.py`
- [ ] T051 [US1] Implement Stepik OAuth boundary, magic-link sessions, logout, and read/write auth-epoch checks in `backend/src/review_platform/application/services/authentication.py`
- [ ] T052 [US1] Implement invitation issue/email-delivery/consume/revoke flow with one-time email binding in `backend/src/review_platform/application/services/invitations.py`
- [ ] T053 [US1] Implement serialized role mutation, last-methodologist recount, session/agent/pending-command invalidation, and audit in `backend/src/review_platform/application/services/memberships.py`
- [ ] T054 [US1] Implement schema-backed course import, roster upsert, Operation attempts/errors, and idempotent resume in `backend/src/review_platform/application/services/course_import.py`
- [ ] T055 [US1] Implement Course/CourseRun list, roster read, and conflict-safe archive/restore services in `backend/src/review_platform/application/services/courses.py`
- [ ] T056 [US1] Implement identity, invitation, organization, Course, CourseRun, and roster routes from frozen OpenAPI in `backend/src/review_platform/api/routes/identity_courses.py`
- [ ] T057 [US1] Implement `python -m review_platform.bootstrap` and course-import/email Taskiq handlers in `backend/src/review_platform/bootstrap.py` and `backend/src/review_platform/infrastructure/tasks/course_import.py`

**Checkpoint**: US1 is independently green with mocks; external Stepik behavior remains BLOCKED until its authorized live gate.

---

## Phase 4: User Story 2 — versioned homework (P2)

**Independent Test**: Create/publish homework, read current and historical versions, change requirements, and mark existing review inputs as affected without mutating them.

- [ ] T058 [P] [US2] Write failing create/version/publish/read OpenAPI and command tests in `backend/tests/contract/test_homework_api.py`
- [ ] T059 [P] [US2] Write failing version, criterion total, stable key, CourseRun publication, and immutable-history tests in `backend/tests/state/test_homework_versioning.py`
- [ ] T060 [P] [US2] Write failing two-CourseRun current/history acceptance tests in `backend/tests/integration/test_homework_publication.py`
- [ ] T061 [US2] Implement Homework, HomeworkVersion, CourseRunHomework, CriterionSet, and Criterion tables in `backend/src/review_platform/infrastructure/db/models/homework.py`
- [ ] T062 [US2] Create reversible homework migration with `down_revision=0002_identity_and_courses` in `backend/migrations/versions/0003_homework_versions.py`
- [ ] T063 [P] [US2] Implement immutable requirement digest and criterion-set validation in `backend/src/review_platform/domain/homework.py`
- [ ] T064 [US2] Implement create/version/publish/current/history and affected-review marking services in `backend/src/review_platform/application/services/homeworks.py`
- [ ] T065 [US2] Implement tenant-scoped homework repositories and history projections in `backend/src/review_platform/infrastructure/db/repositories/homeworks.py`
- [ ] T066 [US2] Implement homework mutation, published-list, and history routes from frozen OpenAPI in `backend/src/review_platform/api/routes/homeworks.py`

**Checkpoint**: US2 is green; requirements are readable and immutable, while migration behavior remains an explicit later review command.

---

## Phase 5: User Story 3 — submissions and immutable artifacts (P3)

**Independent Test**: Preflight returns an opaque reference, submit consumes it, replacements preserve history, late versions remain pending, and one ReviewIteration opens explicitly.

- [ ] T067 [P] [US3] Write failing preflight-to-submit and submission-history contract tests in `backend/tests/contract/test_artifact_submission_api.py`
- [ ] T068 [P] [US3] Write failing SubmissionVersion and initial ReviewIteration transition tests in `backend/tests/state/test_submission_review_states.py`
- [ ] T069 [P] [US3] Write failing cross-tenant reference/version/S3/read and ReviewCase uniqueness tests in `backend/tests/isolation/test_artifact_submission_boundary.py`
- [ ] T070 [P] [US3] Write failing preflight, capture, replacement, late revision, history, and cleanup acceptance tests in `backend/tests/integration/test_submission_lifecycle.py`
- [ ] T071 [US3] Implement Submission, SubmissionVersion, ArtifactReference, ArtifactVersion, ReviewCase, and initial ReviewIteration tables in `backend/src/review_platform/infrastructure/db/models/submission.py`
- [ ] T072 [US3] Create reversible submission/artifact migration with `down_revision=0003_homework_versions` in `backend/migrations/versions/0004_submissions_and_artifacts.py`
- [ ] T073 [P] [US3] Implement schema-backed artifact preflight that persists/returns only usable tenant-scoped references in `backend/src/review_platform/application/services/artifact_preflight.py`
- [ ] T074 [US3] Implement bounded immutable capture and artifact envelope validation in `backend/src/review_platform/application/services/artifact_capture.py`
- [ ] T075 [US3] Implement submission CAS, effective requirements/deadline snapshots, replacement, and late versions in `backend/src/review_platform/application/services/submissions.py`
- [ ] T076 [US3] Implement explicit conflict-safe opening of one ReviewIteration for a selected SubmissionVersion in `backend/src/review_platform/application/services/review_iterations.py`
- [ ] T077 [US3] Implement tenant-scoped submission/artifact/review-case repositories and history projection in `backend/src/review_platform/infrastructure/db/repositories/submissions.py`
- [ ] T078 [US3] Implement preflight, submit, submission-history, and open-iteration routes from frozen OpenAPI in `backend/src/review_platform/api/routes/submissions.py`
- [ ] T079 [US3] Implement artifact-capture and staged-object cleanup workers with durable Operation attempts in `backend/src/review_platform/infrastructure/tasks/artifacts.py`

**Checkpoint**: US3 is green; `preflight → artifact_reference_id → submit` works through the public API.

---

## Phase 6: User Story 4 — AI review over shared human-review spine (P4)

**Independent Test**: Start AI for immutable inputs; accept partial/success/retry/stale/duplicate events; preserve an intervening human ReviewRevision.

- [ ] T080 [P] [US4] Write failing AI request/event, fingerprint, criterion-completeness, sequence, and compatibility tests in `backend/tests/contract/test_ai_contract.py`
- [ ] T081 [P] [US4] Write failing ReviewRevision/Decision/Note persistence and human-edit protection tests in `backend/tests/state/test_review_spine.py`
- [ ] T082 [P] [US4] Write failing AIReviewRun transition and terminal non-regression tests in `backend/tests/state/test_ai_review_run.py`
- [ ] T083 [P] [US4] Write failing duplicate/out-of-order/old-attempt/stale and human-override ingestion tests in `backend/tests/integration/test_ai_event_ingestion.py`
- [ ] T084 [P] [US4] Write failing component-token and signed artifact URL tenant-isolation tests in `backend/tests/isolation/test_ai_component_access.py`
- [ ] T085 [US4] Implement ReviewRevision, ReviewCriterionDecision, ReviewNote, AIReviewRun, AIReviewAttempt, AIReviewEventReceipt, suggestions, and signals tables in `backend/src/review_platform/infrastructure/db/models/review_ai.py`
- [ ] T086 [US4] Create reversible shared review-spine migration with `down_revision=0004_submissions_and_artifacts` in `backend/migrations/versions/0005_review_spine.py`
- [ ] T087 [US4] Create reversible AI tables migration with `down_revision=0005_review_spine` in `backend/migrations/versions/0006_ai_review.py`
- [ ] T088 [P] [US4] Implement RFC 8785 fingerprints and golden vectors in `backend/src/review_platform/domain/ai_fingerprint.py`
- [ ] T089 [P] [US4] Implement strict AI request/event Pydantic types against frozen schema in `backend/src/review_platform/contracts/ai_review.py`
- [ ] T090 [US4] Implement AI start/retry, immutable snapshots, attempts, and component authorization in `backend/src/review_platform/application/services/ai_reviews.py`
- [ ] T091 [US4] Implement event deduplication, sequence/attempt checks, completeness, stale detection, and historical storage in `backend/src/review_platform/application/services/ai_events.py`
- [ ] T092 [US4] Implement tenant-scoped review-spine and AI repositories in `backend/src/review_platform/infrastructure/db/repositories/review_ai.py`
- [ ] T093 [US4] Implement AI Taskiq invocation, bounded retries, sanitized failures, and correlation in `backend/src/review_platform/infrastructure/tasks/ai_review.py`
- [ ] T094 [US4] Implement start-AI, event-ingestion, and component-download routes in `backend/src/review_platform/api/routes/ai_reviews.py`

**Checkpoint**: US4 is green and independently proves that AI output cannot mutate human revision data.

---

## Phase 7: User Story 5 — human review, successors, and publication (P5)

**Independent Test**: Select work, record all responsibility events, race edits, migrate requirements through a successor, correct a publication through a successor, and publish only by human action.

- [ ] T095 [P] [US5] Write failing review reads/preferences/recommendation/responsibility/save/successor/publication contract tests in `backend/tests/contract/test_review_api.py`
- [ ] T096 [P] [US5] Write failing recommendation ordering and responsibility non-exclusivity tests in `backend/tests/state/test_review_recommendation.py`
- [ ] T097 [P] [US5] Write failing one-decision-per-active-criterion, note, correction, and requirements-migration tests in `backend/tests/state/test_review_successors.py`
- [ ] T098 [P] [US5] Write failing concurrent save/publish/successor and stale revision tests in `backend/tests/isolation/test_review_concurrency.py`
- [ ] T099 [P] [US5] Write failing human workflow and AI-unavailable publication tests in `backend/tests/integration/test_human_review_workflow.py`
- [ ] T100 [P] [US5] Write failing PublicationRequest tests proving request alone has no result/delivery effect and human session confirms an exact revision once in `backend/tests/integration/test_publication_request.py`
- [ ] T101 [P] [US5] Write failing publication-to-audit/outbox/minimal-delivery atomicity and provenance tests in `backend/tests/isolation/test_publication_atomicity.py`
- [ ] T102 [US5] Implement ReviewerCourseSelection, AvailabilityPlan, ReviewResponsibility, PublicationRequest, ReviewPublication, and minimal ExternalDelivery tables in `backend/src/review_platform/infrastructure/db/models/human_review.py`
- [ ] T103 [US5] Create reversible human review/publication migration with `down_revision=0006_ai_review` in `backend/migrations/versions/0007_human_review.py`
- [ ] T104 [P] [US5] Implement deterministic recommendation ordering and explanations in `backend/src/review_platform/domain/recommendation.py`
- [ ] T105 [US5] Implement course selection and advisory availability with current membership checks in `backend/src/review_platform/application/services/reviewer_preferences.py`
- [ ] T106 [US5] Implement recommendation plus append-only started/joined/released/completed responsibility events in `backend/src/review_platform/application/services/review_queue.py`
- [ ] T107 [US5] Implement immutable revision save, decision completeness, notes, and expected-current CAS in `backend/src/review_platform/application/services/review_revisions.py`
- [ ] T108 [US5] Implement requirements migration as a new successor iteration with stable-key transfer only in `backend/src/review_platform/application/services/review_requirements.py`
- [ ] T109 [US5] Implement published correction as a new successor iteration/revision without changing predecessor bytes in `backend/src/review_platform/application/services/review_corrections.py`
- [ ] T110 [US5] Implement idempotent agent PublicationRequest creation without publication side effects in `backend/src/review_platform/application/services/publication_requests.py`
- [ ] T111 [US5] Implement interactive-user publication that validates exact revision/request and atomically writes publication, minimal deliveries, outbox, and audit in `backend/src/review_platform/application/services/review_publication.py`
- [ ] T112 [US5] Implement tenant-scoped review preference/responsibility/successor/publication repositories in `backend/src/review_platform/infrastructure/db/repositories/human_reviews.py`
- [ ] T113 [US5] Implement all frozen human-review, successor, responsibility, publication-request, and human publish routes in `backend/src/review_platform/api/routes/reviews.py`
- [ ] T114 [US5] Implement the complete review projection with decisions, notes, AI suggestions, responsibility, publication request, and delivery states in `backend/src/review_platform/application/projections/review_detail.py`

**Checkpoint**: US5 is green; published bytes are immutable, agent request is harmless alone, and only an interactive human creates delivery intent.

---

## Phase 8: User Story 6 — delivery recovery (P6)

**Independent Test**: Fail one destination ambiguously, reconcile before retry, recover without duplicates, preserve local publication, and reject stale delivery.

- [ ] T115 [P] [US6] Write failing delivery state, payload fingerprint, retry-cap, and reconciliation contract tests in `backend/tests/state/test_external_delivery.py`
- [ ] T116 [P] [US6] Write failing timeout/reconciliation/manual-retry/multi-destination acceptance tests in `backend/tests/integration/test_delivery_recovery.py`
- [ ] T117 [P] [US6] Write failing duplicate-worker and stale-publication ordering tests in `backend/tests/isolation/test_delivery_idempotency.py`
- [ ] T118 [US6] Implement DeliveryAttempt and reconciliation observation tables in `backend/src/review_platform/infrastructure/db/models/delivery.py`
- [ ] T119 [US6] Create reversible delivery recovery migration with `down_revision=0007_human_review` in `backend/migrations/versions/0008_delivery_recovery.py`
- [ ] T120 [P] [US6] Implement versioned provider payload rendering and full publication provenance fingerprint in `backend/src/review_platform/domain/delivery_payload.py`
- [ ] T121 [US6] Implement scheduling, bounded retry, unknown outcome, reconciliation, supersession, and manual recovery in `backend/src/review_platform/application/services/deliveries.py`
- [ ] T122 [US6] Implement tenant-scoped attempt repositories and stale-publication guards in `backend/src/review_platform/infrastructure/db/repositories/deliveries.py`
- [ ] T123 [US6] Implement schema-backed delivery and reconciliation workers with stable logical keys in `backend/src/review_platform/infrastructure/tasks/deliveries.py`
- [ ] T124 [US6] Implement delivery list and human-only retry routes from frozen OpenAPI in `backend/src/review_platform/api/routes/deliveries.py`
- [ ] T125 [US6] Add attempt, error, action, provenance, and independent destination states to review projection in `backend/src/review_platform/application/projections/review_detail.py`

**Checkpoint**: US6 is green; unknown outcomes never use blind retry and old results cannot overwrite new ones.

---

## Phase 9: User Story 7 — authorized agent and MCP (P7)

**Independent Test**: Human grants scoped access, agent reviews over MCP and requests publication, human publishes, revocation blocks concurrent reads/writes/jobs, and direct agent publication is impossible.

- [ ] T126 [P] [US7] Write failing grant/revoke, closed-scope, TTL, and token contract tests in `backend/tests/contract/test_agent_authorization_api.py`
- [ ] T127 [P] [US7] Write failing MCP 2026-07-28 transport tests for headers, stateless requests, auth, typed outputs, and obsolete handshake rejection in `backend/tests/contract/test_mcp_protocol.py`
- [ ] T128 [P] [US7] Write failing 11-tool REST/MCP parity tests for handler, role, scope, CAS, idempotency, audit, and no direct publication in `backend/tests/contract/test_http_mcp_parity.py`
- [ ] T129 [P] [US7] Write failing membership/agent revocation race tests against concurrent REST/MCP reads/writes and queued/claimed jobs in `backend/tests/isolation/test_agent_revocation.py`
- [ ] T130 [P] [US7] Write failing end-to-end agent edit/responsibility/AI/publication-request plus human-confirmation tests in `backend/tests/integration/test_agent_review_workflow.py`
- [ ] T131 [US7] Implement opaque agent token creation, hashing, rotation, and constant-time verification in `backend/src/review_platform/infrastructure/auth/agent_tokens.py`
- [ ] T132 [US7] Implement tenant-scoped authorization repository and constant-time token lookup in `backend/src/review_platform/infrastructure/db/repositories/agents.py`
- [ ] T133 [US7] Implement interactive grant/revoke, scope intersection, TTL, auth epoch, and pending-command invalidation in `backend/src/review_platform/application/services/agent_authorizations.py`
- [ ] T134 [US7] Implement MCP bearer resolution to the existing AgentAuthorization and current membership/auth epoch in `backend/src/review_platform/mcp/authentication.py`
- [ ] T135 [US7] Implement stateless MCP server, protocol/version headers, bearer context, and sanitized errors in `backend/src/review_platform/mcp/server.py`
- [ ] T136 [US7] Bind exactly 11 frozen tools to existing application handlers with manifest roles/scopes and audit in `backend/src/review_platform/mcp/tools.py`
- [ ] T137 [US7] Implement session-only agent grant/revoke REST routes and MCP entrypoint in `backend/src/review_platform/api/routes/agents.py` and `backend/src/review_platform/mcp/__main__.py`

**Checkpoint**: US7 is green; revocation blocks all subsequent authority and MCP can request but cannot perform final publication.

---

## Phase 10: Cross-cutting release gates

- [ ] T138 [P] Add enumerated audit coverage for every review, score, role, course/archive, agent, responsibility, publication, and delivery mutation in `backend/tests/isolation/test_audit_coverage.py`
- [ ] T139 [P] Add measurable backend latency tests for sub-1-second mutations, sub-2-second recommendation, and sub-5-second operation visibility in `backend/tests/integration/test_performance_targets.py`
- [ ] T140 Write failing retention and dry-run deletion tests for every configured lifecycle in `backend/tests/state/test_retention_policy.py`
- [ ] T141 Implement tenant-safe retention scheduling, dry-run metrics, and deletion claims in `backend/src/review_platform/infrastructure/tasks/retention.py`
- [ ] T142 Create provider-specific live tests and a durable NOT_RUN/BLOCKED/PASS/FAIL ledger in `backend/tests/live/test_provider_gates.py` and `backend/tests/live/gates.json`
- [ ] T143 Walk every Alembic revision up/down/up against representative MySQL data in `backend/tests/integration/test_migration_walk.py`
- [ ] T144 Run complete offline tests, Ruff, mypy, manifest verification, and migration walk and record observed commands in `specs/001-backend-core/quickstart.md`
- [ ] T145 Validate that SC-002..SC-004 remain BLOCKED until authorized provider sandboxes and frontend timing harness exist in `specs/001-backend-core/requirements-traceability.md`

**Checkpoint**: Offline gates are green; skipped live/product gates remain visibly BLOCKED and cannot be reported as provider support.

---

## Dependencies and execution order

```text
Setup → Frozen Contracts/Foundation → US1 → US2 → US3 → US4 → US5 → US6 → US7 → Release Gates
```

- Contract tests T010-T020 precede T021-T042 and must become green at the Foundation checkpoint.
- Canonical files under `specs/001-backend-core/contracts/` are immutable during story implementation.
- Migrations form one explicit chain: `0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007 → 0008`; story code may be parallelized only where it does not create another Alembic head.
- `[P]` marks only tasks that own distinct files and do not depend on another incomplete task in the same launch group.
- Each story begins with its own RED tests and ends only when those tests and all earlier suites are green.

## Suggested MVP

Complete Setup, Foundation, and US1. Stop and validate bootstrap, tenant isolation, auth revocation, course import, roster reads, and Course/CourseRun recovery before beginning homework behavior.

## Authorization boundary

This plan authorizes implementation tasks only when `$speckit-implement` is explicitly requested. It does not authorize commits, pushes, live-provider calls, credentials, or external publication.
