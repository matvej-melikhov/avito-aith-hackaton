# Requirements traceability: backend core

This file is a pre-implementation gate. `PLANNED` means the named executable specification must exist and first demonstrate RED before the corresponding behavior is implemented. `BLOCKED` means backend mocks cannot prove the external or human outcome.

## Functional requirements

| Requirements | Executable specification | Owner | Reproducible command | Status |
|---|---|---|---|---|
| FR-001, FR-002, FR-003, FR-004, FR-005, FR-006 | `tests/contract/test_identity_api.py`, `tests/isolation/test_identity_concurrency.py` | Backend Core | `uv run --directory backend pytest tests/contract/test_identity_api.py tests/isolation/test_identity_concurrency.py -q` | PLANNED |
| FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015 | `tests/integration/test_organization_course_lifecycle.py`, `tests/contract/test_course_api.py` | Backend Core | `uv run --directory backend pytest tests/integration/test_organization_course_lifecycle.py tests/contract/test_course_api.py -q` | PLANNED |
| FR-016, FR-017, FR-018, FR-019 | `tests/contract/test_homework_api.py`, `tests/state/test_homework_versioning.py` | Backend Core | `uv run --directory backend pytest tests/contract/test_homework_api.py tests/state/test_homework_versioning.py -q` | PLANNED |
| FR-020, FR-021, FR-022, FR-023, FR-024 | `tests/contract/test_artifact_submission_api.py`, `tests/integration/test_submission_lifecycle.py` | Backend Core | `uv run --directory backend pytest tests/contract/test_artifact_submission_api.py tests/integration/test_submission_lifecycle.py -q` | PLANNED |
| FR-025, FR-026, FR-027, FR-028, FR-029, FR-030, FR-031, FR-032, FR-033, FR-034 | `tests/state/test_review_recommendation.py`, `tests/state/test_review_responsibility.py`, `tests/state/test_human_review_publication.py`, `tests/isolation/test_review_successors.py`, `tests/contract/test_review_detail.py` | Backend Core | `uv run --directory backend pytest tests/state/test_review_recommendation.py tests/state/test_review_responsibility.py tests/state/test_human_review_publication.py tests/isolation/test_review_successors.py tests/contract/test_review_detail.py -q` | PLANNED |
| FR-035, FR-036, FR-037, FR-038, FR-039, FR-040, FR-041, FR-042 | `tests/state/test_human_review_publication.py`, `tests/integration/test_delivery_recovery.py`, `tests/isolation/test_delivery_idempotency.py` | Backend Core | `uv run --directory backend pytest tests/state/test_human_review_publication.py tests/integration/test_delivery_recovery.py tests/isolation/test_delivery_idempotency.py -q` | PLANNED |
| FR-043, FR-044, FR-045, FR-046, FR-047, FR-048 | `tests/isolation/test_agent_revocation.py`, `tests/contract/test_agent_authorization_api.py` | Backend Core | `uv run --directory backend pytest tests/isolation/test_agent_revocation.py tests/contract/test_agent_authorization_api.py -q` | PLANNED |
| FR-049, FR-050, FR-051, FR-052, FR-053, FR-054, FR-055, FR-056, FR-057, FR-058, FR-059, FR-060, FR-061 | `tests/contract/test_provider_contracts.py`, `tests/contract/test_ai_contract.py`, `tests/integration/test_ai_event_ingestion.py` | Backend Core + Provider Owners | `uv run --directory backend pytest tests/contract/test_provider_contracts.py tests/contract/test_ai_contract.py tests/integration/test_ai_event_ingestion.py -q` | PLANNED |
| FR-062, FR-063, FR-064, FR-065, FR-066, FR-067 | `tests/integration/test_organization_course_lifecycle.py`, `tests/isolation/test_identity_concurrency.py` | Backend Core | `uv run --directory backend pytest tests/integration/test_organization_course_lifecycle.py tests/isolation/test_identity_concurrency.py -q` | PLANNED |
| FR-068, FR-069, FR-070, FR-071, FR-072, FR-073, FR-074 | `tests/integration/test_homework_publication.py`, `tests/integration/test_submission_lifecycle.py`, `tests/isolation/test_review_successors.py`, `tests/state/test_human_review_publication.py` | Backend Core | `uv run --directory backend pytest tests/integration/test_homework_publication.py tests/integration/test_submission_lifecycle.py tests/isolation/test_review_successors.py tests/state/test_human_review_publication.py -q` | PLANNED |
| FR-075, FR-076, FR-077, FR-078, FR-079 | `tests/contract/test_ai_contract.py`, `tests/isolation/test_ai_component_access.py`, `tests/integration/test_delivery_recovery.py` | Backend Core | `uv run --directory backend pytest tests/contract/test_ai_contract.py tests/isolation/test_ai_component_access.py tests/integration/test_delivery_recovery.py -q` | PLANNED |
| FR-080 | `tests/contract/test_command_boundary.py`, `tests/contract/test_openapi.py` | Backend Core | `uv run --directory backend pytest tests/contract/test_command_boundary.py tests/contract/test_openapi.py -q` | PLANNED |
| FR-081 | `tests/isolation/test_tenant_boundary_matrix.py` | Backend Core | `uv run --directory backend pytest tests/isolation/test_tenant_boundary_matrix.py -q` | PLANNED |
| FR-082, FR-083, FR-084, FR-085 | `tests/state/test_human_review_publication.py`, `tests/state/test_review_responsibility.py`, `tests/isolation/test_review_successors.py`, `tests/isolation/test_publication_authority.py` | Backend Core | `uv run --directory backend pytest tests/state/test_human_review_publication.py tests/state/test_review_responsibility.py tests/isolation/test_review_successors.py tests/isolation/test_publication_authority.py -q` | PLANNED |

## Success criteria

| Criterion | Fixture or authorized gate | Owner | Reproducible command | Status |
|---|---|---|---|---|
| SC-001 | Fresh MySQL/Redis/MinIO/Mailpit installation fixture | Backend Core | `uv run --directory backend pytest tests/integration/test_organization_course_lifecycle.py -q` | PLANNED |
| SC-002 | Full import-to-homework human workflow | Product E2E + Stepik Owner | `uv run --directory backend pytest tests/live/test_provider_gates.py -m live -k stepik -q` | BLOCKED until authorized Stepik sandbox and frontend flow exist |
| SC-003 | Student preflight-to-submit human workflow | Product E2E + Artifact Owners | `uv run --directory backend pytest tests/live/test_provider_gates.py -m live -k artifact -q` | BLOCKED until authorized provider sandboxes and frontend flow exist |
| SC-004 | Reviewer selection-to-start human workflow | Product E2E | `uv run --directory backend pytest tests/e2e/test_reviewer_workflow_time.py -q` | BLOCKED until frontend flow exists; backend latency is tested separately |
| SC-005 | Two-session stale-save fixture | Backend Core | `uv run --directory backend pytest tests/state/test_human_review_publication.py -q` | PLANNED |
| SC-006 | Enumerated mutation-to-audit matrix | Backend Core | `uv run --directory backend pytest tests/isolation/test_audit_coverage.py -q` | PLANNED |
| SC-007 | Multi-destination provider failure fixture | Backend Core | `uv run --directory backend pytest tests/integration/test_delivery_recovery.py -q` | PLANNED |
| SC-008 | Duplicate publication and reconciliation fixture | Backend Core | `uv run --directory backend pytest tests/isolation/test_delivery_idempotency.py -q` | PLANNED |
| SC-009 | Two-organization DB/FK/S3/credential fixture | Backend Core | `uv run --directory backend pytest tests/isolation/test_tenant_boundary_matrix.py -q` | PLANNED |
| SC-010 | Failed import and delivery operation fixture | Backend Core | `uv run --directory backend pytest tests/integration/test_organization_course_lifecycle.py tests/integration/test_delivery_recovery.py -q` | PLANNED |
| SC-011 | Agent one-time token and revoke-versus-commit fixture | Backend Core | `uv run --directory backend pytest tests/contract/test_agent_authorization_api.py tests/isolation/test_agent_revocation.py -q` | PLANNED |
| SC-012 | Provider-independent persisted submission-history fixture | Backend Core | `uv run --directory backend pytest tests/contract/test_artifact_submission_api.py tests/integration/test_submission_lifecycle.py -q` | PLANNED |
| SC-013 | AI fingerprint golden vectors | Backend Core + AI Owner | `uv run --directory backend pytest tests/contract/test_ai_contract.py -q` | PLANNED |
| SC-014 | Late/stale AI event with human edit fixture | Backend Core | `uv run --directory backend pytest tests/integration/test_ai_event_ingestion.py -q` | PLANNED |
| SC-015 | AI-unavailable human publication fixture | Backend Core | `uv run --directory backend pytest tests/integration/test_human_review_without_ai.py -q` | PLANNED |
| SC-016 | Full kind/input-version/attempt-history operation fixture | Backend Core | `uv run --directory backend pytest tests/contract/test_course_api.py tests/integration/test_organization_course_lifecycle.py tests/integration/test_ai_event_ingestion.py -q` | PLANNED |
| SC-017 | Concurrent membership and invitation fixture | Backend Core | `uv run --directory backend pytest tests/isolation/test_identity_concurrency.py -q` | PLANNED |
| SC-018 | Same student/homework in two CourseRuns fixture | Backend Core | `uv run --directory backend pytest tests/integration/test_homework_publication.py tests/isolation/test_artifact_submission_boundary.py -q` | PLANNED |
| SC-019 | Stale save/publish matrix | Backend Core | `uv run --directory backend pytest tests/state/test_human_review_publication.py tests/isolation/test_review_successors.py -q` | PLANNED |
| SC-020 | Two-organization REST/MCP/job/outbox/cache/audit matrix | Backend Core | `uv run --directory backend pytest tests/isolation/test_tenant_boundary_matrix.py tests/isolation/test_agent_revocation.py -q` | PLANNED |
| SC-021 | Agent PublicationRequest without human confirmation fixture | Backend Core | `uv run --directory backend pytest tests/isolation/test_publication_authority.py tests/integration/test_agent_review_workflow.py -q` | PLANNED |
| SC-022 | Published correction successor fixture | Backend Core | `uv run --directory backend pytest tests/isolation/test_review_successors.py -q` | PLANNED |
| SC-023 | started/joined/released/completed concurrency fixture | Backend Core | `uv run --directory backend pytest tests/state/test_review_responsibility.py -q` | PLANNED |

## Contract and live-gate policy

- Contract set 1.1.0 is frozen in `contracts/manifest.json` after a READY `$speckit-analyze` result and a mechanical T022 transition with no semantic edits; runtime copies must match every recorded SHA-256.
- Offline fixtures never access live URLs and prove only backend behavior.
- Each live provider writes `NOT_RUN`, `BLOCKED`, `PASS`, or `FAIL` plus scope, time and evidence to `backend/tests/live/gates.json`.
- A skipped or mock-only provider gate remains unverified and cannot be reported as provider support.

## Planning constraints

| Constraint | Executable specification | Owner | Reproducible command | Status |
|---|---|---|---|---|
| Backend latency targets | `tests/integration/test_performance_targets.py` | Backend Core | `uv run --directory backend pytest tests/integration/test_performance_targets.py -q` | PLANNED |
| Retention and history-safe deletion | `tests/state/test_retention_policy.py` | Backend Core | `uv run --directory backend pytest tests/state/test_retention_policy.py -q` | PLANNED |
| Every Alembic revision preserves representative immutable data | `tests/integration/test_migration_walk.py` | Backend Core | `uv run --directory backend pytest tests/integration/test_migration_walk.py -q` | PLANNED |
| Provider live-gate ledger | `tests/live/test_provider_gates.py`, `tests/live/gates.json` | Provider Owners | `uv run --directory backend pytest tests/live/test_provider_gates.py -m live -q` | BLOCKED until authorized sandboxes exist |
