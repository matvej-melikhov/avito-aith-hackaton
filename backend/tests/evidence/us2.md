# US2 versioned-homework GREEN evidence

Observed locally on 2026-09-05 with Docker context
`lima-avito-aith-hackaton`. No external provider was called.

## TDD baseline

T065-T067 collected 10 tests before implementation. The first combined run was
`1 passed, 9 failed`; failures were the absent homework HTTP routes and absent
homework tables, not import or infrastructure errors.

## Acceptance

```text
uv run --directory backend pytest \
  tests/contract/test_homework_api.py \
  tests/state/test_homework_versioning.py \
  tests/integration/test_homework_publication.py -q --tb=short
# 10 passed in 13.95s
```

The tests prove:

- a draft has no current/published HomeworkVersion;
- full version content and criteria round-trip with exact totals and stable keys;
- old versions and publication rows remain immutable;
- two CourseRuns can select different current versions simultaneously;
- republishing one CourseRun does not change another;
- `HomeworkRequirementsChanged` is written transactionally with exact tenant,
  CourseRun, CourseRunHomework, Homework, previous/current version and
  publication identities;
- command replay does not duplicate Homework, versions, publications, receipts
  or outbox events;
- stale revisions and route/target mismatches return typed conflicts.

## Accumulated gate

```text
uv run --directory backend pytest -m "not live" -q --tb=short
# 339 passed in 67.92s

uv run --directory backend ruff check .
# All checks passed.

uv run --directory backend mypy
# Success: no issues found in 69 source files.

uv run --project backend python scripts/sync_backend_contracts.py --verify
# backend contract set 1.1.0 verified
```

MySQL 8.4 migration walk `0002 → 0003 → 0002 → 0003` passed, `alembic
check` reported no new upgrade operations and the single head is
`0003_homework_versions`.

US2 claims durable requirement-change event emission only. Projection of
affected ReviewIterations and successor creation deliberately remains T124/T132
in US5 and is not claimed here.
