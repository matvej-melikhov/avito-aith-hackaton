# Contract compatibility: 1.0.0 → 1.1.0

Version 1.1.0 is a pre-implementation breaking correction of the committed 1.0.0 design. No runtime consumer of 1.0.0 exists.

## Breaking wire changes

- Every REST mutation uses its exact command schema; a generic command envelope is rejected.
- Artifact preflight is addressed to an exact CourseRunHomework, is idempotent, and returns Submission identity/revision plus a usable Artifact Reference only on success.
- Human-only commands require an interactive REST session; operator commands require local operator transport.
- AI and delivery requests require complete immutable provenance.
- Operation responses expose kind, input version and ordered attempt history.
- Agent authorization creation returns the bearer secret once and stores only its digest.
- Bootstrap and recovery are local operator commands and have no public REST endpoints.
- Every provider request names an exact credential binding ID and version.
- AI review requests name the exact component credential binding ID and version used by each attempt; these operational credentials do not change the immutable-input fingerprint.
- Human and AI criterion scores are nonnegative and are semantically bounded by the matching criterion max points; a human total is the decision sum and cannot exceed the homework max score.
- AI evidence, reviewer/student text slots, and signal questions have the same required/nullability contract in provider events and ReviewDetail projections.
- Every command endpoint declares the shared non-null conflict response; declared authentication, validation, and component-authorization errors use the same non-null error object.
- OAuth, magic-link, current-session logout, and AI event ingestion are explicit protocol mutations with typed one-time/replay identities; they are the only exceptions to the business-command envelope.

## Added reads and transitions

- CourseRun, organization membership and invitation list reads with the IDs and revisions required by the next command.
- Open-review iteration tool for the MCP recommendation flow.
- Typed identifiers in create, save and successor responses plus complete submission/review/AI/delivery history projections.
- Homework history has no global current version: it returns append-only CourseRun publication records and marks the current publication independently for every Course Run, including an empty publication list for a new draft.
- Archive blocks new recommendation, review opening, and publication while preserving already committed publication/delivery intents for recovery.
- Current published review is selected from published iterations only; creating an unpublished successor does not hide it.
- Delivery identity includes the exact destination binding, so two bindings of the same provider kind remain independent.
- Shared delivery fixtures include reconciliation request plus found, not-found, and action-required outcomes.

## Migration rule

Because implementation has not started, there is no persisted runtime data migration. Any future 1.0.0 client fixture MUST be rejected with `unsupported_contract_version`; completed adversarial reviews are followed by a READY `$speckit-analyze` result, after which 1.1.0 becomes frozen mechanically before runtime implementation.
