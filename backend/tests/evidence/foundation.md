# Foundation GREEN evidence

Observed locally on 2026-09-04 in Docker context
`lima-avito-aith-hackaton` (Lima VM `avito-aith-hackaton`, 4 CPU, 8 GiB RAM,
40 GiB disk). No live provider endpoint or production credential was used.

## TDD baseline

Before T023, the static frozen-contract slice passed with `131 passed` while
T017-T021 failed with `30 failed`. Every failure ended at the named
`Foundation behavior not implemented` boundary; collection, imports and local
infrastructure were healthy.

## Observed GREEN commands

```text
uv sync --directory backend --locked
# Resolved 92 packages; checked 90 packages.

uv run --directory backend pytest -m "not live" -q --tb=short
# 203 passed in 16.19s

uv run --directory backend pytest \
  tests/contract/test_command_boundary.py \
  tests/state/test_foundational_operations.py \
  tests/isolation/test_outbox_leasing.py \
  tests/isolation/test_foundational_tenant_boundaries.py \
  tests/isolation/test_foundational_redaction.py -q --tb=short
# 30 passed in 10.01s using isolated MySQL and MinIO fixtures.

uv run --directory backend pytest \
  tests/integration/test_foundation_relay.py -q --tb=short
# 1 passed; a real MySQL outbox row produced only a stable tenant-scoped Redis signal.

uv run --directory backend ruff check .
# All checks passed.

uv run --directory backend mypy
# Success: no issues found in 45 source files.

uv run --project backend python scripts/sync_backend_contracts.py --verify
# backend contract set 1.1.0 verified

docker compose --env-file deploy/env.example -f deploy/compose.yaml config --quiet
# exit 0

uv run --directory backend python -c \
  'import review_platform.main, review_platform.infrastructure.tasks.__main__, review_platform.infrastructure.tasks.relay_main, review_platform.infrastructure.tasks.email_main'
# exit 0

git diff --check
# exit 0
```

## Migration walk

Against the Compose MySQL 8.4 service:

```text
uv run --directory backend alembic downgrade base
uv run --directory backend alembic upgrade head
uv run --directory backend alembic check
# No new upgrade operations detected.
uv run --directory backend alembic heads
# 0001_foundation (head)
```

The real MySQL integration suite also proves one-winner concurrent receipt
reservation, rollback without hidden commits, tenant CAS/FK isolation and
expired final outbox lease recovery to `action_required`.

## Architecture review

An independent read-only pass initially found five P1 gaps: a disguised
in-memory runtime, incompatible SQL/application ports, command/actor ambiguity,
an exhausted final-lease crash hole and a client-controlled operation tenant.
All five were fixed and re-reviewed as RESOLVED. Production source contains no
`_Probe*` or `_foundation_*` state. Operation reads derive organization only
from a server-owned authenticated `RequestActor`; the forged-tenant-header ASGI
test returns 401.

External live gates remain unexecuted and are not claimed by this Foundation
checkpoint.
