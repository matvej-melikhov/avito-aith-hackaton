# Quickstart validation: backend core

Этот guide станет исполнимым после bootstrap backend из tasks.md. До появления кода команды задают обязательный контракт локального окружения.

## Prerequisites

- Docker-compatible container runtime
- Python 3.13
- uv
- свободные локальные порты из deploy/env.example
- без production credentials

## Start

    cp deploy/env.example deploy/.env
    docker compose --env-file deploy/.env -f deploy/compose.yaml up -d mysql redis minio mailpit
    uv sync --directory backend --locked
    docker compose --env-file deploy/.env -f deploy/compose.yaml build api worker outbox-relay email-worker mcp
    docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm api alembic upgrade head
    docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm api python -m review_platform.bootstrap
    docker compose --env-file deploy/.env -f deploy/compose.yaml up -d api worker outbox-relay email-worker mcp

Ожидается: health checks MySQL, Redis, MinIO, API, worker и MCP зелёные; создана одна организация без открытого bootstrap endpoint.

## Code specifications

### Candidate contract schemas

    uv run --directory backend pytest tests/contract -q

Проверяет candidate manifest hashes, exact constitution snapshot, compatibility notes и shared fixtures, 28 mutation-команд (26 REST + local bootstrap + local recovery), 43 OpenAPI paths/46 operations, 12 MCP tools, exact route-command binding, path/target equality, actor/transport boundary, обязательный AI credential binding, artifact envelope, per-CourseRun homework history, complete provenance, fingerprint vectors, criterion completeness, score bounds, attempt sequence и отклонение лишних полей.

### Domain state machines

    uv run --directory backend pytest tests/state -q

Проверяет Course Run, Homework Version, Submission Version, Review Iteration, PublicationRequest, AIReviewRun, publication и ExternalDelivery. Terminal state не регрессирует; correction, новый artifact и requirements migration создают successor iteration.

### Concurrency and isolation

    uv run --directory backend pytest tests/isolation -q

Обязательные сценарии: конкурентное удаление двух методистов, чужой magic link, membership revocation против concurrent REST/MCP read/write/job, две публикации одной revision, agent request без human publish, stale AI event, delayed delivery и cross-tenant read/write/job/cache/object/credential access.

### Integration with local infrastructure

    uv run --directory backend pytest tests/integration -q

Запускает MySQL, Redis, MinIO и mock providers. Проверяет migrations, transactional outbox, Taskiq retries, reconciliation и S3 digest.

### HTTP and MCP parity

    uv run --directory backend pytest tests/contract/test_http_mcp_parity.py -q

После реализации US7 одна матрица проверяет operation mapping, input/output schemas, closed scopes, роли, expected revision target, idempotency и audit для REST и 12 MCP tools 2026-07-28. Она проверяет CourseRun discovery, recommend → open → get review и доказывает, что MCP может только запросить публикацию. Дополнительные fixtures проверяют обязательные headers, version metadata, stateless requests и отказ старого session handshake.

## Fixtures

- GitHub positive account fixture: SupremeSoviet/Consent-2-Publish-Anything
- Google Docs positive DOCX fixture: document 1T046Fmz2xqXlbYykR4KFr6RKECSqtZgwNiG8ACZ2Obw
- Stepik: deterministic mock/replay only
- AI: contract mock emitting success, partial, duplicate, out-of-order, stale and failed events
- Email: Mailpit mailbox for magic links

Offline suites MUST NOT access those live URLs. Recorded fixtures contain no secrets.

## Live gates

Live tests run only with an explicit marker and named sandbox configuration:

    uv run --directory backend pytest tests/live -m live -q

The default test command excludes live tests. A live result records target, time, external identity scope and response evidence without tokens.

`backend/tests/live/gates.json` всегда существует и хранит для каждого provider один из статусов `NOT_RUN`, `BLOCKED`, `PASS` или `FAIL`. Пропущенный live test не переводит capability в verified.

Required gates:

1. GitHub App installation can read the private fixture and publish to a disposable target.
2. Google anonymous export downloads the current fixture as valid DOCX without credentials.
3. Stepik paid or Enterprise sandbox imports a real roster and updates score plus feedback.
4. Email provider delivers one-time magic link to a controlled mailbox.

## Full local gate

    uv run --directory backend pytest -m "not live" -q
    docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm api alembic upgrade head
    docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm api alembic downgrade -1
    docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm api alembic upgrade head

Expected: all offline tests pass, migration round-trip succeeds, no unhandled delivery remains, and no secret appears in captured logs.
