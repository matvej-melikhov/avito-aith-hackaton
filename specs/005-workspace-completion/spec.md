# Workspace completion and student self-review

Status: implementation in progress. Baseline: backend `1a1a0b4` plus the existing frontend.

Authority: `docs/frontend-completion-and-ai-self-review-plan.md` and the user's subsequent authorization to implement it. The frozen 001 contracts retain their meanings; extensions are exposed under `/api/v2` and a new `student-self-review:2.0.0` AI contract.

## Required behavior

- Complete all С1–С5, Р1–Р5, К1–К9 screens with local fixtures and production backend boundaries.
- A student prepares a persistent work draft and an immutable artifact, runs optional self-review, then submits explicitly. A self-review never fabricates a human review iteration.
- One finite quota per organization/publication-in-course-run/student. Resubmission, URL changes and requirements versions do not reset it.
- Atomically reserve a slot on accepted start; consume exactly once when a valid final result is available. Definitive technical failure releases the reservation. Unknown dispatch outcome retains it until resolution. Technical retries never allocate another student attempt.
- Student projections exclude reference solutions, reviewer notes, private criteria, credentials, AI authorship signals and unpublished human results.
- Work recommendations remain advisory; course authorization continues to apply. Primary student assignment is distinct from nonexclusive review participation.
- Numeric points are bounded by the criterion maximum. Methodologists can explicitly enter editing mode. Publication is an interactive action on a saved revision.

## Decisions

Quota N is mandatory for published workspace policy; zero disables self-review. A valid final result includes at least one completed criterion finding. All-not-checked is a technical unsuccessful result. Partials are progress only until a final result is persisted. Final persisted results consume quota even if the browser is closed.

Private reference materials are never inputs to student self-review. The external AI developer owns model/prompt/evals; this backend provides execution context, authenticated event protocol, durable jobs, fixture adapter and handoff contract.

The full accepted screen matrix, state transitions, error cases and package dependencies remain in the linked plan. External live-provider compatibility is tracked separately from completion of local fixture-backed integration.
