# Implementation evidence

- [x] P01 Isolated baseline `1a1a0b4` in `codex/frontend-self-review`; original checkout preserved.
- [x] P02 Extension specification; quota scope and consume-on-result semantics carried forward.
- [x] P03 Versioned contracts, schemas, types and migrations.
- [x] P04 Full screen navigation and scenario fixtures.
- [x] P05 Real server session boundary and local authentication flow.
- [x] P06 Persistent drafts and immutable prepared artifacts.
- [x] P07 Atomic quota/reservation ledger.
- [x] P08 Durable AI dispatch and fixture component.
- [x] P09 Event ingestion, result projection, fencing/recovery.
- [x] P10 Student screens С1–С5.
- [x] P11 Scoped lists, pagination, counters and statuses.
- [x] P12 Primary reviewer assignment and advisory priorities.
- [x] P13 Saved preferences, absence and measured statistics.
- [x] P14 Course/course-run CRUD and publication links.
- [x] P15 Assignment/private fields/files/outcome policy.
- [x] P16 Reviewer screens and explicit, versioned coordinator corrections.
- [x] P17 Coordinator lists, reviewer counts and explicit reminders.
- [x] P18 Scoped CSV/XLSX export with browser-safe filenames.
- [x] P19 End-to-end scenarios and browser QA across all roles and screen states.
- [x] P20 AI handoff package; external component compatibility remains an external gate.
- [ ] P21 Resolve the design-pack auto-enrollment rule versus the agreed imported/admitted-student rule. No enrollment authority was silently expanded.

## Current acceptance — f44e570

The current design baseline is `f44e57095ec9241056ea5348fd4fcdff505b43fc`. See [acceptance.md](acceptance.md) and its screenshot/measurement manifest for the current evidence and explicit limitations. The full backend regression now passes: **907 passed, 9 gated skips**. Screen coverage distinguishes real local-server states from intercepted read-only C1/C3 design fixtures. P21 remains an unresolved product decision, so this is not an unconditional claim that every behavior described in the design pack is complete.

## Earlier checkpoint — superseded by the acceptance above

Implementation is in the `backend` checkout. The frontend follows the design pack from `matvej` at `e473d01464975f3d9b5739e26b19d4b9499a8699` and the screen flows, with the agreed authentication, numeric scoring and nonexclusive review changes. The updated pack includes repeated-review, search and course-directory states in addition to the original 19-screen matrix.

Implemented: persistent preparation and draft snapshots; finite self-review quota and revocation fencing; durable reviewer AI with explicit signal choices; real local sessions and idempotent fixture setup; published grade policy; unified student list and pseudonymous search; course/homework editing and criterion configuration; notifications and scoped exports. The v1 published-only review projection remains unchanged; the editor reads its current human draft through the additive v2 draft endpoint.

Verified at this checkpoint: 31 frontend tests, generated-contract checks and the production build; 41 workspace/local tests before the feedback pass plus focused criterion, search, queue, correction and migration regressions. The broad backend run reached 897 passed and exposed one migration-walk mismatch; after extending the walk through migrations 0009–0014, the migration and related feedback suite passed in a 14-test run. Ruff and mypy pass across the backend source.

Browser evidence includes real reviewer login, separate work/pool lists, start/continue review, AI fixture suggestions, save/reload of a human draft, publication of a needs-changes outcome with deadline, reviewer preferences and statistics; student login, list, self-review, quota consumption, resubmission and history; coordinator overview/search, course and run forms, homework editor, reviewer counts, pool/registry distinction, immutable correction flow and XLSX export. A seed-only missing quota found during the student continuation was repaired without resetting used counters.

Final browser acceptance covered the login, student list, self-review, resubmission and history; separate reviewer work and pool pages, reviewer settings, statistics, start/continue actions, draft persistence and publication; coordinator overview, search, courses, course/run modals, homework wizard, pool, registry, immutable published review correction and XLSX export. The same local run covered a 760px viewport and a fresh console with no warnings or errors. The feedback pass unified the profile location, clarified work actions, separated pool and registry semantics, added reviewer counts and removed duplicate participation controls. External AI and live-provider compatibility remain explicitly unverified. RUNNING.md documents the ordinary local login flow, and ai-handoff.md plus contracts/examples describe the external component boundary.
