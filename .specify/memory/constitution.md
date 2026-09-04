<!--
Sync Impact Report
- Version change: template -> 1.0.0
- Added principles:
  - I. Human Controls Consequences
  - II. Immutable Inputs and Versioned Contracts
  - III. Private Self-Hosted Boundary
  - IV. Observable and Recoverable Operations
  - V. Executable Specifications and Honest Integration Tests
  - VI. Modular Ownership Through Explicit Contracts
- Added sections:
  - Security and Data Constraints
  - Development Workflow and Quality Gates
- Removed sections: none
- Deferred items: none
-->

# Avito AI Reviewer Constitution

## Core Principles

### I. Human Controls Consequences

AI and deterministic checks MAY prepare scores, explanations, evidence, feedback, and
signals. A reviewer or methodologist MUST make and publish every decision that affects a
student. AI output MUST NOT publish feedback, change the current score, impose a sanction,
or treat an AI-use signal as proof without an explicit authorized human action. The product
MUST remain usable when the AI component is unavailable.

### II. Immutable Inputs and Versioned Contracts

Every review, AI run, and external publication MUST identify the exact course run, homework
version, criterion set, submission version, artifact digest, and contract version it uses.
Mutable links MUST be resolved to a reproducible snapshot before processing. Late,
duplicated, or out-of-order results MUST NOT become current for another input or overwrite a
human revision. Contract changes MUST be versioned, documented, and accompanied by
compatibility or migration rules.

### III. Private Self-Hosted Boundary

The first deployment MUST be self-hosted and single-tenant, with an explicit organization
boundary preserved for future multi-tenant operation. Authorization MUST be checked for
every read, write, background action, artifact, cache entry, external credential, and audit
event. Student work, personal data, tokens, and secrets MUST remain inside the approved
processing boundary. Logs and errors MUST be sanitized. A second tenant MUST NOT be enabled
until cross-tenant read, write, queue, storage, cache, and credential isolation tests pass.

### IV. Observable and Recoverable Operations

Imports, artifact retrieval, AI review, and external publication MUST expose stable
identities, input versions, state, attempts, timestamps, and sanitized errors. Local
publication and the intent to deliver externally MUST be durable as one logical action.
Unknown external outcomes MUST be reconciled before retry. Persistent failures MUST remain
visible and actionable; they MUST NOT be converted into silent success. Web and MCP clients
MUST use the same authorization, concurrency, idempotency, and audit rules.

### V. Executable Specifications and Honest Integration Tests

A requirement that affects identity, state transitions, version selection, scoring,
publication, or tenant isolation MUST have an executable acceptance or contract test before
its implementation is considered complete. Tests MUST cover failures, retries, duplicate
events, out-of-order events, concurrent writes, access revocation, and provider limits where
applicable. Mocks and replay fixtures MUST verify local behavior. Claims about external API
behavior MUST remain blocked until a live sandbox test proves them. A mock passing MUST NOT
be reported as proof of provider behavior.

### VI. Modular Ownership Through Explicit Contracts

Backend core, AI review, Stepik, GitHub, and Google Docs MAY be implemented by different
owners. They MUST integrate only through reviewed, versioned schemas with shared contract
fixtures. Backend owns orchestration, state, audit, human revisions, and external-delivery
intent. The AI component owns model logic, prompts, checks, evidence extraction, and evals.
Provider adapters own provider authentication, normalization, retrieval, and publication.
No component MAY infer another component's undocumented behavior.

## Security and Data Constraints

- Product roles are methodologist, reviewer, and student; there is no product administrator.
- Bootstrap and recovery MUST use the operator boundary, an exact external identity, a
  one-time action, and an audit record.
- Reviewer invitations MUST be one-time, role-bound, identity-bound, expiring, and revocable.
- The last active methodologist invariant MUST hold atomically under concurrent changes.
- Revoking membership MUST invalidate sessions, agent authorizations, and pending commands.
- External student artifacts MUST be treated as untrusted input.
- Secrets MUST NOT appear in source code, artifacts, logs, prompts, test fixtures, or chat.
- Archived courses and superseded versions MUST retain required history without remaining
  active in recommendations or publication.

## Development Workflow and Quality Gates

1. Maintain one authoritative program scope and mark older conflicting documents superseded.
2. Complete feature specification and requirements-quality checklist before planning.
3. Freeze shared request, result, event, artifact, error, and command schemas before parallel
   component implementation.
4. Map every success criterion to an offline fixture or authorized live sandbox, an owner,
   and a reproducible command.
5. Complete technical plan and dependency-ordered tasks before application implementation.
6. Write executable contract and state-transition tests before or alongside the behavior they
   define; demonstrate the failing test before claiming the behavior implemented.
7. Run specification analysis after plan and task generation. P1 inconsistencies block
   implementation.
8. A feature is complete only when required tests pass, unresolved external gates are visible,
   and documentation matches observed behavior.
9. Commits and pushes follow the current user's explicit authorization and repository rules.

## Governance

This constitution supersedes conflicting workflow guidance in older project documents.
Amendments require an explicit rationale, a compatibility or migration impact assessment,
an updated Sync Impact Report, and semantic versioning:

- MAJOR for removing or redefining a principle incompatibly;
- MINOR for adding a principle or materially expanding governance;
- PATCH for clarification without changing obligations.

Every plan and code review MUST check constitution compliance. Exceptions MUST be recorded
with owner, scope, reason, expiry, and removal condition. Unrecorded exceptions are invalid.

**Version**: 1.0.0 | **Ratified**: 2026-09-04 | **Last Amended**: 2026-09-04
