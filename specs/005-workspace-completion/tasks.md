# Implementation evidence

- [x] P01 Isolated baseline `1a1a0b4` in `codex/frontend-self-review`; original checkout preserved.
- [x] P02 Extension specification; quota scope and consume-on-result semantics carried forward.
- [x] P03 Versioned contracts, schemas, types and migrations.
- [ ] P04 Full screen navigation and scenario fixtures.
- [x] P05 Real server session boundary and local authentication flow.
- [x] P06 Persistent drafts and immutable prepared artifacts.
- [x] P07 Atomic quota/reservation ledger.
- [x] P08 Durable AI dispatch and fixture component.
- [x] P09 Event ingestion, result projection, fencing/recovery.
- [ ] P10 Student screens С1–С5.
- [x] P11 Scoped lists, pagination, counters and statuses.
- [x] P12 Primary reviewer assignment and advisory priorities.
- [x] P13 Saved preferences, absence and measured statistics.
- [x] P14 Course/course-run CRUD and publication links.
- [x] P15 Assignment/private fields/files/outcome policy.
- [ ] P16 Reviewer screens and coordinator edit capability.
- [ ] P17 Coordinator lists and explicit reminders.
- [ ] P18 Scoped CSV/XLSX export.
- [ ] P19 End-to-end tests and browser QA for all 19 screens.
- [x] P20 AI handoff package; external component compatibility remains an external gate.

## Implementation checkpoint — 2026-09-05

Implementation is in the `backend` checkout. The frontend follows the design pack from `matvej` at `e473d01464975f3d9b5739e26b19d4b9499a8699` and the screen flows, with the agreed authentication, numeric scoring and nonexclusive review changes. The updated pack includes repeated-review, search and course-directory states in addition to the original 19-screen matrix.

Implemented: persistent preparation and draft snapshots; finite self-review quota and revocation fencing; durable reviewer AI with explicit signal choices; real local sessions and idempotent fixture setup; published grade policy; unified student list and pseudonymous search; course/homework editing and criterion configuration; notifications and scoped exports. The v1 published-only review projection remains unchanged; the editor reads its current human draft through the additive v2 draft endpoint.

Verified before this checkpoint: 26 frontend tests and production build; 41 focused backend/local tests before the latest criterion/search additions, followed by their focused regressions; a broad backend run reached 889 passed and exposed two regressions (logout replay and migration walk), both subsequently fixed and independently passed. A fresh complete backend run is in progress. These are concrete implementation checks, not a claim that all browser acceptance scenarios are complete.

Browser evidence so far: real reviewer login, queue/open work, AI fixture suggestions, save/reload of a human draft, publication of a needs-changes outcome with deadline, reviewer preferences and statistics, student login/list/grade status. A seed-only missing quota was found during the student continuation and repaired without resetting used counters.

Remaining acceptance: finish student self-review/resubmission and coordinator screens in the browser, narrow viewport and console checks; verify final contracts/examples and current full-suite result. External AI and live-provider compatibility remain explicitly unverified. RUNNING.md documents the ordinary local login flow, and ai-handoff.md plus contracts/examples describe the external component boundary.
