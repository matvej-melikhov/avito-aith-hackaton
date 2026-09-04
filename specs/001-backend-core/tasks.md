---
description: "Dependency-ordered TDD implementation tasks for backend core"
---

# Tasks: Backend Core Review Platform

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `context-traceability.md`, `requirements-traceability.md`, `constitution-snapshot.md`, `contract-compatibility.md`, `contract-fixtures/`, `contracts/`, `quickstart.md`, `.specify/memory/constitution.md`

**Contract state**: T022 froze contract set 1.1.0 after a READY `$speckit-analyze`: 28 commands, 43 OpenAPI paths / 46 operations, 12 MCP tools, and 19 hashed artifacts. Story phases MUST NOT edit canonical contracts; later semantic changes require a new version and compatibility/migration notes.

**Executable-specification rule**: static schema/manifest checks are expected to be GREEN before runtime implementation. Behavioral, state-machine, concurrency, and isolation tests are introduced RED, must fail for the named missing behavior rather than import or infrastructure errors, and become GREEN before their phase checkpoint.

## Phase 1: Setup — runnable isolated environment

**Purpose**: Establish a reproducible Python and container test harness without importing application entrypoints that are created in later phases.

- [X] T001 Create Python 3.13 project metadata, exact runtime/test dependencies, pytest markers, Ruff, and mypy configuration in `backend/pyproject.toml`
- [X] T002 Generate the reproducible dependency lock without committing or pushing in `backend/uv.lock`
- [X] T003 Create the importable package and application version in `backend/src/review_platform/__init__.py`
- [X] T004 [P] Implement environment-only typed settings with safe offline defaults in `backend/src/review_platform/settings.py`
- [X] T005 [P] Define non-secret local ports, limits, retention, encryption-key references, and live-sandbox variables in `deploy/env.example`
- [X] T006 Define Python 3.13 image stages and deferred API, worker, relay, email-worker, bootstrap, operator, and MCP commands in `backend/Dockerfile`
- [X] T007 Define MySQL 8.4, Redis, MinIO, Mailpit, API, worker, outbox relay, email worker, and MCP services and health checks in `deploy/compose.yaml`
- [X] T008 Implement deterministic clock/UUID fixtures, pytest plugin registration, and explicit live-test guards in `backend/tests/conftest.py`
- [X] T009 Implement isolated Testcontainers lifecycle, readiness, cleanup, and per-suite MySQL/Redis/MinIO fixtures in `backend/tests/fixtures/containers.py`
- [X] T010 Write a container smoke test that creates and reads tenant-tagged data from MySQL, Redis, and MinIO without application entrypoints in `backend/tests/integration/test_container_smoke.py`

**Checkpoint**: `uv sync --locked`, pytest collection, T010, and `docker compose config --quiet` succeed without external-provider access; application image build and process imports are deliberately deferred to the release gate.

---

## Phase 2: Candidate contracts and shared foundation

**Purpose**: Validate immutable design inputs, then implement only shared tenant, operation, idempotency, audit, outbox, storage-primitive, and request-boundary behavior.

### Static executable specifications — GREEN before runtime work

- [X] T011 [P] Write and pass a traceability validator for FR-001..FR-085 and SC-001..SC-023 requiring fixture/gate, owner, command, and status in `backend/tests/contract/test_requirements_traceability.py`
- [X] T012 [P] Write and pass manifest/hash tests for all 19 contract-set artifacts, their manifest-declared candidate/frozen lifecycle status, compatibility notes, the byte-identical constitution snapshot, and shared fixtures in `backend/tests/contract/test_contract_manifest.py`
- [X] T013 [P] Write and pass Draft 2020-12 tests for all 28 command variants, conditional payloads, actor/transport policy, version, and additional-property rejection in `backend/tests/contract/test_command_schema.py`
- [X] T014 [P] Write and pass OpenAPI tests for 43 paths / 46 operations, exact per-route command refs, path/target equality, typed success/error responses, examples, and external references in `backend/tests/contract/test_openapi.py`
- [X] T015 [P] Write and pass static MCP manifest tests for exactly 12 tools, closed scopes, roles, typed results, recommendation-to-iteration flow, and absence of direct publication in `backend/tests/contract/test_mcp_manifest.py`
- [X] T016 [P] Write and pass typed identity, course-import, artifact-provider, delivery/reconciliation, email, exact credential-binding, AI, and shared fixture/vector tests in `backend/tests/contract/test_provider_contracts.py`

### Shared behavioral specifications — RED before implementation

- [X] T017 [P] Write failing runtime boundary tests for production-only component composition, exact route command, path/target equality, server-owned actor/organization, operator-only bootstrap/recovery, and REST-only human commands in `backend/tests/contract/test_command_boundary.py`
- [X] T018 [P] Write failing state tests for CommandReceipt, Operation/OperationAttempt, Outbox lease, terminal failure visibility, and legal transition non-regression in `backend/tests/state/test_foundational_operations.py`
- [X] T019 [P] Write failing multi-relay tests for `SKIP LOCKED`, lease tokens, expiry recovery, duplicate messages, exhausted attempts, and actionable poison messages in `backend/tests/isolation/test_outbox_leasing.py`
- [X] T020 [P] Write failing foundational tenant tests for Organization-scoped operation/receipt/audit/outbox rows, composite foreign keys, Redis namespaces, S3 object keys/signed URLs, and generic API reads in `backend/tests/isolation/test_foundational_tenant_boundaries.py`
- [X] T021 [P] Write failing shared-sink redaction tests for logs, generic API errors, OperationAttempt, OutboxMessage, and AuditEvent in `backend/tests/isolation/test_foundational_redaction.py`

### Shared implementation

- [X] T022 After an Analyze READY verdict, verify candidate 1.1.0 plus fixtures, mechanically set status to frozen, and regenerate hashes without semantic edits in `scripts/sync_backend_contracts.py`
- [X] T023 Package manifest-verified schemas as runtime resources in `backend/src/review_platform/contracts/schemas/`
- [X] T024 Implement schema loading, reference resolution, version selection, and generated-schema conformance in `backend/src/review_platform/contracts/registry.py`
- [X] T025 Implement strict Pydantic wire/application commands and the user/agent/installation-operator RequestActor variants in `backend/src/review_platform/contracts/commands.py`
- [X] T026 Implement UUIDv7, UTC clock, digest, revision, and sanitized-error primitives in `backend/src/review_platform/domain/primitives.py`
- [X] T027 Configure asyncmy SQLAlchemy engine, sessions, naming conventions, and test transactions in `backend/src/review_platform/infrastructure/db/session.py`
- [X] T028 Implement tenant-key mixins and composite tenant foreign-key helpers in `backend/src/review_platform/infrastructure/db/base.py`
- [X] T029 Implement the minimal single-installation Organization tenant anchor in `backend/src/review_platform/infrastructure/db/models/organization.py`
- [X] T030 Implement CommandReceipt, AuditEvent, OutboxMessage, Operation, and OperationAttempt tables referencing Organization in `backend/src/review_platform/infrastructure/db/models/operations.py`
- [X] T031 Configure async Alembic metadata/discovery/single-head checks and create Organization plus foundational operations in `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, and `backend/migrations/versions/0001_foundation.py`
- [X] T032 Implement tenant-scoped operation, receipt, audit, and outbox repositories in `backend/src/review_platform/infrastructure/db/repositories/operations.py`
- [X] T033 Define authenticated RequestActor context and the transport-independent auth-version guard protocol without concrete OrganizationMembership or AgentAuthorization access in `backend/src/review_platform/application/request_context.py`
- [X] T034 Implement role/scope authorization, closed-scope validation, and fail-closed tenant checks against the guard protocol in `backend/src/review_platform/application/authorization.py`
- [X] T035 Implement shared validation, transaction, expected-revision CAS, and transport-independent dispatch in `backend/src/review_platform/application/command_bus.py`
- [X] T036 Implement idempotency reservation, replay, payload-conflict rejection, and stable result references in `backend/src/review_platform/application/idempotency.py`
- [X] T037 Implement append-only audit creation, shared-sink sanitization, and atomic rollback semantics in `backend/src/review_platform/application/audit.py`
- [X] T038 Implement stable outbox creation, leasing, max-attempt visibility, and operator-visible recovery state in `backend/src/review_platform/infrastructure/db/outbox.py`
- [X] T039 Implement `FOR UPDATE SKIP LOCKED` relay, compare-and-set lease completion, Redis publication, and crash recovery in `backend/src/review_platform/infrastructure/tasks/outbox_relay.py`
- [X] T040 Configure Taskiq routing, bounded retry/backoff, tenant correlation, concurrency-limit hooks, and worker auth revalidation hooks in `backend/src/review_platform/infrastructure/tasks/broker.py`
- [X] T041 Implement only tenant-scoped S3 key construction, bounded upload/download, digest verification, signed reads, and low-level deletion primitives in `backend/src/review_platform/infrastructure/object_storage/s3.py`
- [X] T042 Implement schema-backed provider ports and mocks loaded from frozen shared fixtures in `backend/src/review_platform/application/ports/providers.py` and `backend/src/review_platform/infrastructure/providers/mocks.py`
- [X] T043 Compose FoundationRuntime only from the real command bus, SQLAlchemy repositories, outbox, S3, and middleware components, then implement sanitized FastAPI middleware, router registry, operation history, and health probes in `backend/src/review_platform/application/foundation_runtime.py`, `backend/src/review_platform/api/middleware.py`, `backend/src/review_platform/api/routes/__init__.py`, `backend/src/review_platform/api/routes/operations.py`, and `backend/src/review_platform/main.py`
- [X] T044 Implement explicit Taskiq handler registry and executable worker, relay, and email-worker entrypoints in `backend/src/review_platform/infrastructure/tasks/registry.py`, `backend/src/review_platform/infrastructure/tasks/__main__.py`, `backend/src/review_platform/infrastructure/tasks/relay_main.py`, and `backend/src/review_platform/infrastructure/tasks/email_main.py`
- [X] T045 Run T011-T021 against the T009 MySQL/Redis/MinIO fixtures, assert FoundationRuntime exposes no test or in-memory adapters, run the migration single-head check, and record the Foundation GREEN evidence in `backend/tests/evidence/foundation.md`

**Checkpoint**: T011-T021 are GREEN. No concrete identity, artifact promotion, external delivery, AgentAuthorization, MCP invocation, or full-system tenant matrix is required yet.

---

## Phase 3: User Story 1 — organization and courses (Priority: P1)

**Goal**: Bootstrap the installation, authenticate, manage membership safely, import courses and rosters, and archive/restore Course and CourseRun.

**Independent Test**: Activate the exact bootstrap identity locally, authenticate, import a roster, read it, add/remove roles safely, and archive/restore Course and CourseRun.

- [X] T046 [P] [US1] Write failing local-only bootstrap/recovery operator actor, OAuth state/callback/logout protocol-replay, typed identity/session, and absence-of-public-operator-endpoints tests, excluding agent-token behavior owned by US7, in `backend/tests/contract/test_identity_api.py`
- [X] T047 [P] [US1] Write failing Course/CourseRun identity, roster/membership/invitation reads, typed create results, archive/restore, archived-action guards, and operation-history tests in `backend/tests/contract/test_course_api.py`
- [X] T048 [P] [US1] Write failing local bootstrap, typed identity assertion, invitation-email, import-retry, roster-upsert, and history acceptance tests in `backend/tests/integration/test_organization_course_lifecycle.py`
- [X] T049 [P] [US1] Write failing last-methodologist, mismatched verified-email assertion, invitation token/state double-consume, consume-versus-revoke, and membership-revocation race tests across REST and claimed/unclaimed jobs in `backend/tests/isolation/test_identity_concurrency.py`
- [X] T050 [US1] Implement User, ExternalIdentity, OrganizationMembership, Invitation, OAuthState, Session, AgentAuthorization persistence, and ExternalCredential tables referencing the foundational Organization in `backend/src/review_platform/infrastructure/db/models/identity.py`
- [X] T051 [US1] Implement ExternalCourseBinding, Course, CourseRun, CourseMembership, and DestinationBinding tables in `backend/src/review_platform/infrastructure/db/models/learning.py`
- [X] T052 [US1] Create the reversible identity/course migration, including the AgentAuthorization table required by later PublicationRequest foreign keys, with `down_revision=0001_foundation` in `backend/migrations/versions/0002_identity_and_courses.py`
- [X] T053 [US1] Implement tenant-scoped identity, invitation, session, and encrypted credential repositories in `backend/src/review_platform/infrastructure/db/repositories/identity.py`
- [X] T054 [US1] Implement tenant-scoped Course/CourseRun/membership/binding repositories and cache-key factories in `backend/src/review_platform/infrastructure/db/repositories/learning.py`
- [X] T055 [US1] Implement fixed-order OrganizationMembership locks, auth-epoch validation, and final pre-commit revalidation as the concrete user guard in `backend/src/review_platform/application/auth_guards/membership.py`
- [X] T056 [US1] Implement one-time exact-identity bootstrap and installation-operator recovery in `backend/src/review_platform/application/services/bootstrap.py`
- [X] T057 [US1] Orchestrate Stepik OAuth and magic-link callbacks through atomically consumed OAuthState/invitation protocol identities and the frozen identity-provider assertion contract, then idempotently create/logout sessions with auth-epoch checks in `backend/src/review_platform/application/services/authentication.py`
- [X] T058 [US1] Implement invitation issue/consume/revoke against Invitation target identity plus typed email delivery intent in `backend/src/review_platform/application/services/invitations.py`
- [X] T059 [US1] Implement serialized role mutation, last-methodologist recount, session/pending-command invalidation, and audit in `backend/src/review_platform/application/services/memberships.py`
- [X] T060 [US1] Implement schema-backed course import, exact credential binding ID/version, roster upsert, Operation attempts/errors, and idempotent resume in `backend/src/review_platform/application/services/course_import.py`
- [X] T061 [US1] Implement Course/CourseRun list, roster read, conflict-safe archive/restore, and the shared archived-state guard for new recommendation/open-review/publication actions in `backend/src/review_platform/application/services/courses.py`
- [X] T062 [US1] Implement and register identity, invitation, membership, Course, CourseRun, and roster routes from frozen OpenAPI in `backend/src/review_platform/api/routes/identity_courses.py` and `backend/src/review_platform/api/routes/__init__.py`
- [X] T063 [US1] Implement local bootstrap and one-time recovery CLIs plus the registered course-import handler in `backend/src/review_platform/bootstrap.py`, `backend/src/review_platform/operator.py`, `backend/src/review_platform/infrastructure/tasks/course_import.py`, and `backend/src/review_platform/infrastructure/tasks/registry.py`
- [X] T064 [US1] Implement the schema-backed SMTP/Mailpit adapter and register the invitation-email handler in `backend/src/review_platform/infrastructure/providers/email_smtp.py`, `backend/src/review_platform/infrastructure/tasks/email.py`, and `backend/src/review_platform/infrastructure/tasks/registry.py`

**Checkpoint**: US1 is independently GREEN with mocks; external Stepik behavior remains BLOCKED until its authorized live gate.

---

## Phase 4: User Story 2 — versioned homework (Priority: P2)

**Goal**: Create immutable homework requirements and publish a selected version to a CourseRun.

**Independent Test**: Create/publish homework, read current and historical versions, change requirements, and observe a durable requirements-changed event without mutating prior data.

- [X] T065 [P] [US2] Write failing create/version/publish/read OpenAPI and exact-command tests for a draft with no publication and per-CourseRun current publications in `backend/tests/contract/test_homework_api.py`
- [X] T066 [P] [US2] Write failing full-version round-trip, criterion total, stable-key, per-CourseRun publication, immutable-history, and durable requirements-changed event tests carrying previous/current version IDs in `backend/tests/state/test_homework_versioning.py`
- [X] T067 [P] [US2] Write failing two-CourseRun history tests where different versions are current simultaneously plus durable requirements-change event acceptance tests in `backend/tests/integration/test_homework_publication.py`
- [X] T068 [US2] Implement Homework, HomeworkVersion, CourseRunHomework, append-only CourseRunHomeworkPublication, CriterionSet, and Criterion tables in `backend/src/review_platform/infrastructure/db/models/homework.py`
- [X] T069 [US2] Create the reversible homework migration with `down_revision=0002_identity_and_courses` in `backend/migrations/versions/0003_homework_versions.py`
- [X] T070 [US2] Implement immutable requirement digest and criterion-set validation in `backend/src/review_platform/domain/homework.py`
- [X] T071 [US2] Implement create/version, append-only per-CourseRun publication/current selection, full history, and transactional HomeworkRequirementsChanged outbox events without requiring later ReviewIteration tables in `backend/src/review_platform/application/services/homeworks.py`
- [X] T072 [US2] Implement tenant-scoped homework repositories and history projections with no global current version in `backend/src/review_platform/infrastructure/db/repositories/homeworks.py`
- [X] T073 [US2] Implement and register homework mutation, published-list, and history routes from frozen OpenAPI in `backend/src/review_platform/api/routes/homeworks.py` and `backend/src/review_platform/api/routes/__init__.py`
- [X] T074 [US2] Run the US2 suite and record that event emission is GREEN while affected-review projection and successor creation remain explicitly owned by US5 in `backend/tests/evidence/us2.md`

**Checkpoint**: US2 independently proves immutable requirements and durable change events; review-impact consumption is not claimed until US5.

---

## Phase 5: User Story 3 — submissions and immutable artifacts (Priority: P3)

**Goal**: Preflight and capture immutable artifacts, submit versions inside a CourseRunHomework, and explicitly open one review iteration.

**Independent Test**: Preflight returns an opaque reference, submit consumes it, replacements preserve history, late versions remain pending, and one ReviewIteration opens explicitly.

- [X] T075 [P] [US3] Write failing CourseRunHomework-addressed preflight tests for two-flow isolation, conditional usable reference, Submission ID/revision, capture Operation ID, exact target, typed result, and history in `backend/tests/contract/test_artifact_submission_api.py`
- [X] T076 [P] [US3] Write failing SubmissionVersion and initial ReviewIteration transition tests in `backend/tests/state/test_submission_review_states.py`
- [X] T077 [P] [US3] Write failing cross-tenant reference/version/S3/read and ReviewCase uniqueness tests in `backend/tests/isolation/test_artifact_submission_boundary.py`
- [X] T078 [P] [US3] Write failing preflight, capture, DB-commit-before-promotion recovery, replacement, late revision, history, and intent-aware cleanup tests in `backend/tests/integration/test_submission_lifecycle.py`
- [X] T079 [US3] Implement Submission, SubmissionVersion, ArtifactReference, ArtifactVersion, and ArtifactPromotion tables in `backend/src/review_platform/infrastructure/db/models/submission.py`
- [X] T080 [US3] Implement ReviewCase and initial ReviewIteration tables with CourseRun-aware uniqueness in `backend/src/review_platform/infrastructure/db/models/review_case.py`
- [X] T081 [US3] Create the reversible submission/artifact/review-case migration with `down_revision=0003_homework_versions` in `backend/migrations/versions/0004_submissions_and_artifacts.py`
- [X] T082 [US3] Implement idempotent preflight addressed to CourseRunHomework that creates/finds Submission and conditionally returns a usable tenant-scoped ArtifactReference in `backend/src/review_platform/application/services/artifact_preflight.py`
- [ ] T083 [US3] Implement bounded immutable capture with atomic ArtifactVersion/ArtifactPromotion/outbox creation, provider credential binding provenance, and Operation linkage in `backend/src/review_platform/application/services/artifact_capture.py`
- [ ] T084 [US3] Implement Submission CAS, effective requirements/deadline snapshots, replacement, and late pending versions in `backend/src/review_platform/application/services/submissions.py`
- [ ] T085 [US3] Implement explicit conflict-safe opening of at most one ReviewIteration for a selected SubmissionVersion in `backend/src/review_platform/application/services/review_iterations.py`
- [ ] T086 [US3] Implement tenant-scoped submission/artifact/review-case repositories and only the canonical US3 SubmissionHistory fields: submission versions, artifact versions, initial review iterations, immutable input IDs/digests, revisions, and capture Operation IDs in `backend/src/review_platform/infrastructure/db/repositories/submissions.py`
- [ ] T087 [US3] Implement durable promotion/recovery and intent-aware orphan cleanup on top of the Foundation S3 primitives in `backend/src/review_platform/infrastructure/object_storage/promotions.py`
- [ ] T088 [US3] Implement schema-backed GitHub and Google Docs artifact adapters with exact credential binding ID/version in `backend/src/review_platform/infrastructure/providers/github_artifacts.py` and `backend/src/review_platform/infrastructure/providers/google_docs_artifacts.py`
- [ ] T089 [US3] Implement and register artifact capture, promotion recovery, and cleanup workers with full Operation attempts in `backend/src/review_platform/infrastructure/tasks/artifacts.py` and `backend/src/review_platform/infrastructure/tasks/registry.py`
- [ ] T090 [US3] Implement and register exact-command preflight, submission-version create, history, and open-iteration routes in `backend/src/review_platform/api/routes/submissions.py` and `backend/src/review_platform/api/routes/__init__.py`

**Checkpoint**: US3 is GREEN; `CourseRunHomework preflight → artifact_reference_id → submit` is observable through the public API.

---

## Phase 6: User Story 4 — AI review over a shared human-review spine (Priority: P4)

**Goal**: Run AI against immutable versioned inputs while keeping human revisions authoritative and editable.

**Independent Test**: Start AI for immutable inputs; accept partial/success/retry/stale/duplicate events; preserve an intervening human ReviewRevision.

- [ ] T091 [P] [US4] Write failing AI request/event tests for required credential binding ID/version, full provenance, minimal-event-to-ReviewDetail round trip, shared fingerprint vectors, exact criterion coverage, score range zero..criterion max, terminal success/error rules, sequence, and 1.1.0 compatibility in `backend/tests/contract/test_ai_contract.py`
- [ ] T092 [P] [US4] Write failing ReviewRevision/Decision/Note persistence and human-edit protection tests in `backend/tests/state/test_review_spine.py`
- [ ] T093 [P] [US4] Write failing AIReviewRun transition, attempt separation, typed failures, and terminal non-regression tests in `backend/tests/state/test_ai_review_run.py`
- [ ] T094 [P] [US4] Write failing identical replay, event-ID collision, duplicate/out-of-order/old-attempt/stale, fingerprint mismatch, and human-override ingestion tests in `backend/tests/integration/test_ai_event_ingestion.py`
- [ ] T095 [P] [US4] Write failing component-token, provider-error redaction, and signed artifact URL tenant-isolation tests in `backend/tests/isolation/test_ai_component_access.py`
- [ ] T096 [US4] Implement ReviewRevision, ReviewCriterionDecision, and ReviewNote tables without mutable publication state in `backend/src/review_platform/infrastructure/db/models/review_revision.py`
- [ ] T097 [US4] Implement AIReviewRun, AIReviewAttempt, AIReviewEventReceipt, AICriterionSuggestion, and AISignal tables in `backend/src/review_platform/infrastructure/db/models/ai_review.py`
- [ ] T098 [US4] Create the reversible review-spine migration with `down_revision=0004_submissions_and_artifacts` in `backend/migrations/versions/0005_review_spine.py`
- [ ] T099 [US4] Create the reversible AI-review migration with `down_revision=0005_review_spine` in `backend/migrations/versions/0006_ai_review.py`
- [ ] T100 [US4] Implement canonical immutable-input fingerprinting and shared vector verification in `backend/src/review_platform/domain/ai_fingerprint.py`
- [ ] T101 [US4] Implement generated typed AI request/event/error models plus exact completeness and zero..criterion-max semantic validation for every AI suggestion in `backend/src/review_platform/contracts/ai_review.py`
- [ ] T102 [US4] Implement tenant-scoped draft revision/decision/note repositories with expected-current CAS in `backend/src/review_platform/infrastructure/db/repositories/review_revisions.py`
- [ ] T103 [US4] Implement tenant-scoped AI run/attempt/event/suggestion/signal repositories in `backend/src/review_platform/infrastructure/db/repositories/ai_reviews.py`
- [ ] T104 [US4] Implement append-only human draft revision creation used to prove AI cannot overwrite a human edit in `backend/src/review_platform/application/services/review_drafts.py`
- [ ] T105 [US4] Implement idempotent AI start with frozen immutable inputs, exact credential binding provenance, Operation ID, and signed artifact grant in `backend/src/review_platform/application/services/ai_review_start.py`
- [ ] T106 [US4] Implement sequenced idempotent AI event ingestion, terminal completeness/error checks, stale detection, and separate AI signal storage in `backend/src/review_platform/application/services/ai_review_events.py`
- [ ] T107 [US4] Implement and register AI dispatch/event workers with full Operation attempt history in `backend/src/review_platform/infrastructure/tasks/ai_review.py` and `backend/src/review_platform/infrastructure/tasks/registry.py`
- [ ] T108 [US4] Implement and register AI start/read routes and enrich review detail with the canonical AI run/signal/suggestion fields in `backend/src/review_platform/api/routes/ai_reviews.py`, `backend/src/review_platform/api/routes/__init__.py`, and `backend/src/review_platform/application/projections/review_detail.py`

**Checkpoint**: US4 is GREEN for AI lifecycle and human-revision protection. Publishing a human review while AI is unavailable is deliberately proven in US5, where publication exists.

---

## Phase 7: User Story 5 — human review, responsibility, and publication (Priority: P5)

**Goal**: Recommend work, record non-exclusive participation, edit versioned reviews, request publication through an agent, and publish only through an interactive human command.

**Independent Test**: Recommend/open/get a review, save revisions with conflict detection, record participation events, create a harmless publication request, publish as a human, and correct only through a successor iteration.

- [ ] T109 [P] [US5] Write failing exact-command reviewer CourseRun selection, planned-hours, deterministic recommendation ordering, and archive-versus-recommend/open races in `backend/tests/state/test_review_recommendation.py`
- [ ] T110 [P] [US5] Write failing started/joined/released/completed append-only and non-exclusive concurrency tests in `backend/tests/state/test_review_responsibility.py`
- [ ] T111 [P] [US5] Write failing human revision completeness, per-criterion score range, total-score equality/upper-bound, expected-revision conflict, archive-versus-publish race, unpublished-successor current-result preservation, and immutable-byte tests in `backend/tests/state/test_human_review_publication.py`
- [ ] T112 [P] [US5] Write failing idempotent ReviewImpactEvent persistence, affected-review projection, requirements migration, and correction successor race tests that preserve predecessor bytes in `backend/tests/isolation/test_review_successors.py`
- [ ] T113 [P] [US5] Write failing agent PublicationRequest versus interactive-human publication boundary tests in `backend/tests/isolation/test_publication_authority.py`
- [ ] T114 [P] [US5] Write failing review-detail tests for immutable inputs, current feedback/score, decisions, notes, AI run/signal, delivery provenance, and revisions in `backend/tests/contract/test_review_detail.py`
- [ ] T115 [P] [US5] Write failing acceptance test proving a human can edit and publish while the AI component is unavailable in `backend/tests/integration/test_human_review_without_ai.py`
- [ ] T116 [US5] Implement AvailabilityPlan, ReviewerCourseSelection, and append-only ReviewResponsibility tables in `backend/src/review_platform/infrastructure/db/models/review_work.py`
- [ ] T117 [US5] Implement ReviewIterationRelation, append-only ReviewImpactEvent, ReviewPublication, PublicationRequest, ExternalDelivery intent, and destination snapshot tables in `backend/src/review_platform/infrastructure/db/models/publication.py`
- [ ] T118 [US5] Create the reversible human-review migration for preferences, selections, responsibility, impacts, successors, and publication with `down_revision=0006_ai_review` in `backend/migrations/versions/0007_human_review.py`
- [ ] T119 [US5] Implement deadline-first, same-reviewer-continuation, age, planned-hours, and workload recommendation ordering in `backend/src/review_platform/domain/recommendation.py`
- [ ] T120 [US5] Implement exact-command CourseRun selection and free-form planned-hours updates without a hard work cap in `backend/src/review_platform/application/services/reviewer_course_selections.py` and `backend/src/review_platform/application/services/reviewer_availability.py`
- [ ] T121 [US5] Implement deterministic tenant-scoped recommendation and recommend-to-open continuity in `backend/src/review_platform/application/services/recommendations.py`
- [ ] T122 [US5] Implement append-only started/joined/released/completed participation events without locks or exclusive edit rights in `backend/src/review_platform/application/services/review_responsibility.py`
- [ ] T123 [US5] Implement immutable revision save with every score in zero..criterion max, computed total equal to the decision sum and not above homework max, exact completeness, notes, and expected-current CAS in `backend/src/review_platform/application/services/review_revisions.py`
- [ ] T124 [US5] Implement requirements migration by locking ReviewCase, creating one successor/relation, transferring matching stable criterion keys, and updating only the current pointer in `backend/src/review_platform/application/services/review_requirements.py`
- [ ] T125 [US5] Implement correction with the same single-successor transaction while preserving predecessor revision/publication bytes in `backend/src/review_platform/application/services/review_corrections.py`
- [ ] T126 [US5] Implement idempotent agent PublicationRequest creation with no current-result or delivery side effects in `backend/src/review_platform/application/services/publication_requests.py`
- [ ] T127 [US5] Implement interactive-human publication that locks and rejects archived Course/CourseRun, validates the requested ReviewRevision, snapshots every required DestinationBinding, and atomically writes publication, deliveries with Operation IDs, outbox, and audit in `backend/src/review_platform/application/services/review_publication.py`
- [ ] T128 [US5] Implement tenant-scoped availability, CourseRun selection, responsibility, and recommendation repositories in `backend/src/review_platform/infrastructure/db/repositories/review_work.py`
- [ ] T129 [US5] Implement tenant-scoped successor, publication-request, publication, and delivery-intent repositories in `backend/src/review_platform/infrastructure/db/repositories/publications.py`
- [ ] T130 [US5] Implement and register exact-command reviewer CourseRun selection, availability, review, successor, responsibility, publication-request, typed save, and human publish routes in `backend/src/review_platform/api/routes/reviews.py` and `backend/src/review_platform/api/routes/__init__.py`
- [ ] T131 [US5] Complete the review projection with immutable inputs/download, current feedback/score selected only from published iterations, decisions, notes, AI run/signal/attempt/error, responsibility, publication request, delivery Operation ID, and provenance in `backend/src/review_platform/application/projections/review_detail.py`
- [ ] T132 [US5] Consume HomeworkRequirementsChanged idempotently, persist one append-only ReviewImpactEvent per affected iteration/version pair, and expose successor proposals without mutating prior iterations in `backend/src/review_platform/application/services/review_requirement_impacts.py` and `backend/src/review_platform/infrastructure/tasks/registry.py`

**Checkpoint**: US5 is GREEN; published bytes are immutable, the agent request is harmless alone, only an interactive human creates delivery intent, and AI unavailability does not block human publication.

---

## Phase 8: User Story 6 — delivery recovery (Priority: P6)

**Goal**: Deliver every publication independently, reconcile ambiguous outcomes, and recover without duplicates or stale overwrites.

**Independent Test**: Fail one destination ambiguously, reconcile before retry, recover without duplicates, preserve local publication, and reject stale delivery.

- [ ] T133 [P] [US6] Write failing delivery state, destination snapshot, exact credential binding, full provenance, bounded payload/error, retry-cap, and reconciliation contract tests in `backend/tests/state/test_external_delivery.py`
- [ ] T134 [P] [US6] Write failing timeout/reconciliation/manual-retry/multi-destination acceptance tests in `backend/tests/integration/test_delivery_recovery.py`
- [ ] T135 [P] [US6] Write failing duplicate-worker, two-bindings-of-one-kind, cross-tenant provider batch, archive-after-durable-intent, and stale-publication ordering tests in `backend/tests/isolation/test_delivery_idempotency.py`
- [ ] T136 [US6] Implement DeliveryAttempt and reconciliation observation tables in `backend/src/review_platform/infrastructure/db/models/delivery.py`
- [ ] T137 [US6] Create the reversible delivery-recovery migration with `down_revision=0007_human_review` in `backend/migrations/versions/0008_delivery_recovery.py`
- [ ] T138 [US6] Implement frozen 1.1.0 typed payload/error rendering and full publication provenance fingerprint in `backend/src/review_platform/domain/delivery_payload.py`
- [ ] T139 [US6] Implement scheduling, bounded retry, unknown outcome, reconciliation-before-retry, supersession, and manual recovery in `backend/src/review_platform/application/services/deliveries.py`
- [ ] T140 [US6] Implement tenant-scoped attempt/observation repositories and stale-publication guards in `backend/src/review_platform/infrastructure/db/repositories/deliveries.py`
- [ ] T141 [US6] Implement schema-backed delivery and reconciliation workers with stable logical keys, exact credential binding provenance, and Operation attempts in `backend/src/review_platform/infrastructure/tasks/deliveries.py`
- [ ] T142 [US6] Register delivery/reconciliation handlers and concurrency limits in `backend/src/review_platform/infrastructure/tasks/registry.py`
- [ ] T143 [US6] Implement and register delivery list and exact-command human-only retry routes in `backend/src/review_platform/api/routes/deliveries.py` and `backend/src/review_platform/api/routes/__init__.py`
- [ ] T144 [US6] Add typed delivery attempt history, error/action, reconciliation observations, provenance, and independent destination states to `backend/src/review_platform/application/projections/review_detail.py`

**Checkpoint**: US6 is GREEN; unknown outcomes never use blind retry, every delivery is observable, and old results cannot overwrite new ones.

---

## Phase 9: User Story 7 — authorized agent and MCP (Priority: P7)

**Goal**: Grant revocable scoped agent access and expose the existing application layer through MCP 2026-07-28 without granting publication authority.

**Independent Test**: Human grants scoped access, agent reviews over MCP and requests publication, human publishes, revocation blocks concurrent reads/writes/jobs, and direct agent publication is impossible.

- [ ] T145 [P] [US7] Write failing grant/revoke, closed-scope, TTL, one-time response secret, and digest-only storage tests in `backend/tests/contract/test_agent_authorization_api.py`
- [ ] T146 [P] [US7] Write failing MCP 2026-07-28 transport tests for headers, stateless requests, bearer auth, typed outputs, and obsolete handshake rejection in `backend/tests/contract/test_mcp_protocol.py`
- [ ] T147 [P] [US7] Write failing 12-tool REST/MCP parity tests for CourseRun discovery, recommend→open→get flow, handler, role, scope, CAS, idempotency, audit, and no direct publication in `backend/tests/contract/test_http_mcp_parity.py`
- [ ] T148 [P] [US7] Write failing revoke-versus-commit races against concurrent REST/MCP reads/writes and queued/claimed jobs using Membership and AgentAuthorization revalidation in `backend/tests/isolation/test_agent_revocation.py`
- [ ] T149 [P] [US7] Write failing end-to-end agent edit/responsibility/AI/publication-request plus separate human-confirmation tests in `backend/tests/integration/test_agent_review_workflow.py`
- [ ] T150 [US7] Implement opaque agent token creation, hashing, rotation, and constant-time verification for the existing AgentAuthorization table in `backend/src/review_platform/infrastructure/auth/agent_tokens.py`
- [ ] T151 [US7] Implement the tenant-scoped AgentAuthorization repository, digest lookup, row locks, and revision checks in `backend/src/review_platform/infrastructure/db/repositories/agents.py`
- [ ] T152 [US7] Implement interactive grant returning the bearer secret exactly once, digest-only storage, revoke, scope intersection, TTL, and pending-command invalidation in `backend/src/review_platform/application/services/agent_authorizations.py`
- [ ] T153 [US7] Implement the concrete combined OrganizationMembership/AgentAuthorization fixed-order guard with final pre-commit revalidation in `backend/src/review_platform/application/auth_guards/agent.py`
- [ ] T154 [US7] Implement MCP bearer resolution to current AgentAuthorization, represented user, membership, organization, and auth epochs in `backend/src/review_platform/mcp/authentication.py`
- [ ] T155 [US7] Implement the stateless MCP server, protocol/version headers, bearer context, size limits, and sanitized errors in `backend/src/review_platform/mcp/server.py`
- [ ] T156 [US7] Bind the frozen read/discovery MCP tools to existing handlers with typed results and audit in `backend/src/review_platform/mcp/tools/read.py`
- [ ] T157 [US7] Bind the frozen review/recommendation/responsibility mutation MCP tools to existing handlers with exact scopes, CAS, and idempotency in `backend/src/review_platform/mcp/tools/review.py`
- [ ] T158 [US7] Bind AI start and PublicationRequest MCP tools without any direct publication handler in `backend/src/review_platform/mcp/tools/ai_publication.py`
- [ ] T159 [US7] Assemble and assert exactly 12 tools in the MCP registry in `backend/src/review_platform/mcp/tools/__init__.py`
- [ ] T160 [US7] Implement and register session-only grant/revoke routes with one-time token response plus MCP entrypoint in `backend/src/review_platform/api/routes/agents.py`, `backend/src/review_platform/api/routes/__init__.py`, and `backend/src/review_platform/mcp/__main__.py`
- [ ] T161 [US7] Run the complete revocation and REST/MCP parity suites and record the US7 GREEN evidence in `backend/tests/evidence/us7.md`

**Checkpoint**: US7 is GREEN; revocation blocks all subsequent authority and uncommitted concurrent work, while MCP can request but cannot perform final publication.

---

## Phase 10: Cross-cutting release gates

**Purpose**: Test the accumulated system only after every referenced model, transport, worker, and provider boundary exists.

- [ ] T162 [P] Add the full two-tenant matrix for DB/composite FKs, REST/MCP reads/writes/body limits, queued/claimed jobs/concurrency caps, outbox, Redis/cache, S3/signed URLs, credentials, provider batches/errors, audit, promotion, delivery, and cleanup in `backend/tests/isolation/test_tenant_boundary_matrix.py`
- [ ] T163 [P] Add full-system redaction tests for REST/MCP, logs, attempts, outbox, audit, AI, delivery, provider bodies, artifact content, bearer secrets, PII, and magic links in `backend/tests/isolation/test_secret_redaction.py`
- [ ] T164 [P] Add enumerated audit coverage for every review, score, role, course/archive, agent, responsibility, publication, artifact, and delivery mutation in `backend/tests/isolation/test_audit_coverage.py`
- [ ] T165 [P] Add measurable backend latency tests for sub-1-second mutations, sub-2-second recommendation, and sub-5-second operation visibility in `backend/tests/integration/test_performance_targets.py`
- [ ] T166 Write failing retention tests for explicit tombstones, preserved digests/provenance/successor history, audited purge, no dangling references, and dry-run deletion in `backend/tests/state/test_retention_policy.py`
- [ ] T167 Implement and register tenant-safe retention scheduling, dry-run metrics, deletion claims, and audit in `backend/src/review_platform/infrastructure/tasks/retention.py` and `backend/src/review_platform/infrastructure/tasks/registry.py`
- [ ] T168 Create provider-specific live tests and a durable NOT_RUN/BLOCKED/PASS/FAIL ledger without invoking providers by default in `backend/tests/live/test_provider_gates.py` and `backend/tests/live/gates.json`
- [ ] T169 Walk every Alembic revision against representative immutable rows and assert lossless digests/publication bytes across supported upgrades in `backend/tests/integration/test_migration_walk.py`
- [ ] T170 Run complete offline tests, Ruff, mypy, manifest verification, migration walk, application image build, and every process-entrypoint import, then record observed commands in `specs/001-backend-core/quickstart.md`
- [ ] T171 Create the explicit reviewer-workflow timing gate that defaults to BLOCKED without a frontend harness, then validate and record that SC-002..SC-004 cannot be reported as passed prematurely in `backend/tests/e2e/test_reviewer_workflow_time.py` and `specs/001-backend-core/requirements-traceability.md`

**Checkpoint**: All offline gates are GREEN; skipped live/product gates remain visibly BLOCKED and cannot be reported as provider support.

---

## Dependencies and execution order

```text
Setup → Candidate Contracts/Foundation → US1 → US2 → US3 → US4 → US5 → US6 → US7 → Cross-cutting Release Gates
```

- T001-T010 establish only the isolated harness; they do not import future API, worker, relay, email, operator, or MCP entrypoints.
- Static gates T011-T016 must be GREEN before T022 freezes contracts. Behavioral tests T017-T021 must be demonstrably RED before T023-T044 and GREEN at T045.
- T022 is conditional on an Analyze READY verdict. If Analyze finds a contract defect, repair the design artifacts, rerun Analyze, and only then freeze.
- Canonical files under `specs/001-backend-core/contracts/` are immutable after T022; later semantic changes require a new contract version and compatibility note.
- Migrations form one explicit chain: `0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007 → 0008`; no story may create a second Alembic head.
- Organization is created in T029/T031, so foundational tenant-owned rows never depend on the later identity migration.
- Durable ArtifactPromotion begins in US3; concrete Membership locking begins in US1; AgentAuthorization locking and MCP begin in US7; full-system tenant/redaction gates wait until Phase 10.
- HomeworkRequirementsChanged emission is independently complete in US2; affected-review projection and successor proposals become complete in US5.
- Human draft protection is independently complete in US4; the AI-unavailable publication acceptance test becomes possible only in US5.
- `[P]` marks tasks that own distinct files and do not depend on another incomplete task in the same launch group. Migrations, shared registries, and dependent domain/service layers are intentionally sequential.
- Each user story begins with its own RED tests and ends only when those tests plus every earlier suite are GREEN.

## Suggested MVP

Complete Setup, Foundation, and US1. Stop and validate bootstrap, tenant isolation, auth revocation, course import, roster reads, and Course/CourseRun recovery before beginning homework behavior.

## Parallel execution examples

- **Foundation**: T011-T016 are one static GREEN wave; T017-T021 are a separate behavioral RED wave. Do not start T022 until both the Analyze verdict and static gates permit it.
- **US1**: T046-T049 may be authored together; after T052, T053 and T054 own distinct repositories, while services consume only completed interfaces.
- **US2**: T065-T067 form one RED wave; implementation is sequential through migration and service integration.
- **US3**: T075-T078 form one RED wave; model/migration work T079-T081 precedes services, promotion, adapters, workers, and routes.
- **US4**: T091-T095 form one RED wave; T096-T099 establish storage before repositories and services.
- **US5**: T109-T115 form one RED wave; T116-T118 establish storage before recommendation, editing, successor, and publication services.
- **US6**: T133-T135 form one RED wave; T136-T137 precede payload, repositories, service, workers, and routes.
- **US7**: T145-T149 form one RED wave; token/repository/service/guard tasks precede MCP transport and tool binding.
- **Release**: T162-T165 own separate accumulated test files and may run in parallel; retention remains RED-before-GREEN in T166-T167.

## Incremental implementation strategy

Complete and validate one phase at a time. Static candidate-contract checks start GREEN; each behavioral phase then demonstrates purposeful RED, implements only the named owner files, and returns the accumulated suite to GREEN. No phase may rely on a later migration, router, worker registration, contract edit, or external live result.

## Authorization boundary

This task list authorizes implementation only when `$speckit-implement` is explicitly requested. It does not authorize commits, pushes, live-provider calls, credentials, or external publication.
