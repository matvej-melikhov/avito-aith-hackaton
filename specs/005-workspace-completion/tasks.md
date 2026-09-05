# Implementation evidence

- [x] P01 Isolated baseline `1a1a0b4` in `codex/frontend-self-review`; original checkout preserved.
- [x] P02 Extension specification; quota scope and consume-on-result semantics carried forward.
- [ ] P03 Versioned contracts, schemas, types and migrations.
- [ ] P04 Full screen navigation and scenario fixtures.
- [ ] P05 Real server session boundary and local authentication flow.
- [ ] P06 Persistent drafts and immutable prepared artifacts.
- [ ] P07 Atomic quota/reservation ledger.
- [ ] P08 Durable AI dispatch and fixture component.
- [ ] P09 Event ingestion, result projection, fencing/recovery.
- [ ] P10 Student screens С1–С5.
- [ ] P11 Scoped lists, pagination, counters and statuses.
- [ ] P12 Primary reviewer assignment and advisory priorities.
- [ ] P13 Saved preferences, absence and measured statistics.
- [ ] P14 Course/course-run CRUD and publication links.
- [ ] P15 Assignment/private fields/files/outcome policy.
- [ ] P16 Reviewer screens and coordinator edit capability.
- [ ] P17 Coordinator lists and explicit reminders.
- [ ] P18 Scoped CSV/XLSX export.
- [ ] P19 End-to-end tests and browser QA for all 19 screens.
- [ ] P20 AI handoff package; external component compatibility remains an external gate.

## Continuation checkpoint

Implementation is paused for the user's request to document startup and defer further polishing. The implementation lives in the isolated `codex/frontend-self-review` worktree; documentation was copied to the original checkout as well. This is an implementation checkpoint, not a claim of completed acceptance.

Verified before the pause: 8 MySQL quota/service tests, 4 HTTP/session/upload/self-review/open-review tests, and the 17 frontend tests. Startup verification additionally confirmed API import, one migration head, and upgrade of an empty MySQL database to all runtime tables through `0011_workspace_version_fields`.

Remaining acceptance work must not be reported complete: full 19-screen browser scenarios, real local sign-in setup, external AI adapter/handoff compatibility, completion of additional scored requirements and grading-policy projections, and a fresh full regression/contract regeneration after the latest changes. Generated v2 schemas and TypeScript types were refreshed before the checkpoint. The demo XLSX export and several richer fixture scenarios also still need completion. `RUNNING.md` separates a working demo from a configured authenticated backend.
