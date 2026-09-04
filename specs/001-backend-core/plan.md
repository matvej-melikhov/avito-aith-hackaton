# Implementation Plan: Backend Core Review Platform

**Branch**: backend | **Date**: 2026-09-04 | **Spec**: [spec.md](spec.md)

**Input**: specs/001-backend-core/spec.md

## Summary

Создать self-hosted backend одной организации на Python/FastAPI. MySQL хранит доменное состояние, версии, audit, operations и transactional outbox. S3 хранит снимки артефактов. Taskiq/Redis исполняет импорт, AI-review и доставки. REST и MCP 2026-07-28 вызывают один application layer. Агент может запросить публикацию, но окончательное действие выполняет человек. Stepik, GitHub, Google Docs, email и AI logic подключаются через замороженные версионируемые contracts и shared fixtures.

## Technical Context

**Language/Version**: Python 3.13

**Primary Dependencies**: FastAPI, Pydantic 2, SQLAlchemy 2.0, asyncmy, Alembic, Taskiq, taskiq-redis, Redis client, boto3, Authlib, MCP Python SDK

**Storage**: MySQL 8.4 LTS для состояния, S3-compatible storage для артефактов, Redis как broker

**Testing**: pytest, AnyIO, HTTPX, Testcontainers, JSON Schema/OpenAPI validation, mock/replay providers, отдельные live tests

**Target Platform**: Linux containers, self-hosted single-tenant

**Project Type**: web service с API, worker и MCP endpoint

**Performance Goals**: обычная mutation отвечает за 1 секунду без ожидания provider; рекомендация работы за 2 секунды; изменение фонового состояния видно за 5 секунд

**Constraints**: human-in-the-loop; AI failure не блокирует; нет silent fallback; tenant boundary во всех sync/async путях; snapshots в S3; Stepik live gate открыт

**Scale/Scope**: одна организация на deployment; несколько Course Run; до 1 000 студентов, 100 ревьюеров и 100 000 submission/review revisions без изменения архитектуры

### Resource limits and retention

| Setting | Default | Hard ceiling |
|---|---:|---:|
| GitHub files | 10 000 | 50 000 |
| Single blob | 10 MiB | 50 MiB |
| Sum of blobs | 100 MiB | 500 MiB |
| Downloaded archive | 100 MiB | 500 MiB |
| Unpacked snapshot | 250 MiB | 1 GiB |
| Google DOCX export | 10 MB provider limit | 10 MB |
| AI artifact download URL | 15 minutes | 60 minutes |
| HTTP command body | 1 MiB | 5 MiB |
| Provider request timeout | 15 seconds | 60 seconds |
| Provider attempts per operation | 5 | 10 |
| Retry backoff | 1 minute initial, 60 minutes max | 24 hours max |
| Concurrent course imports per tenant | 2 | 10 |
| Concurrent AI runs per tenant | 10 | 50 |
| Concurrent deliveries per tenant | 20 | 100 |
| Criteria or review decisions | 500 | 500 |
| Review notes | 500 | 500 |
| Provider roster page | 1 000 | 1 000 |
| Stored provider error text | 2 KiB | 2 KiB |

Artifact bytes remain while Course Run is active and 90 days after archive. Artifact identity, digest, provenance, ReviewRevision, ReviewPublication, successor relations and AuditEvent remain queryable for 365 days after archive. Expiry replaces removed bytes or personal fields with explicit tombstones and never leaves a dangling reference; an operator purge is audited. Operational telemetry remains 90 days; application logs remain 30 days. External credentials remain only while their binding is active. Values are configurable below hard ceilings.

S3 lifecycle: staged upload → byte limit → digest verification → one DB transaction for ArtifactVersion + durable ArtifactPromotion + outbox → idempotent promotion/recovery. GC deletes only objects without a live promotion intent. Metadata, digests, review publications and successor relations remain queryable for the configured history period even when artifact bytes expire.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Plan evidence | Status |
|---|---|---|
| Human Controls Consequences | AI suggestions отделены от ReviewRevision; publish требует human actor | PASS |
| Immutable Inputs and Versioned Contracts | Artifact digest и версии входят в fingerprints; correction/migration создают successor iteration | PASS |
| Private Self-Hosted Boundary | organization ID обязателен в sync/async paths и storage | PASS |
| Observable and Recoverable Operations | attempts, errors, unknown/reconciling и outbox | PASS |
| Executable Specifications | contract/state/limit/revocation tests предшествуют behavior; live tests отделены и имеют явный status | PASS |
| Modular Ownership | backend, AI и каждый provider boundary используют versioned schemas и shared fixtures | PASS |

Design-level constitution checks and adversarial reviews pass. `$speckit-analyze` returned READY and T022 mechanically froze contract set 1.1.0 before runtime implementation.

## Project Structure

### Documentation (this feature)

    specs/001-backend-core/
    ├── plan.md
    ├── research.md
    ├── data-model.md
    ├── context-traceability.md
    ├── requirements-traceability.md
    ├── quickstart.md
    ├── contracts/
    │   ├── manifest.json
    │   ├── openapi.yaml
    │   ├── command.schema.json
    │   ├── artifact.schema.json
    │   ├── ai-review.schema.json
    │   ├── course-import.schema.json
    │   ├── artifact-provider.schema.json
    │   ├── delivery.schema.json
    │   ├── email.schema.json
    │   ├── identity-provider.schema.json
    │   └── mcp-tools.json
    ├── contract-fixtures/
    │   ├── ai-fingerprint-v1.1.0.json
    │   ├── ai-events-v1.1.0.json
    │   ├── course-import-v1.1.0.json
    │   ├── artifact-provider-v1.1.0.json
    │   ├── delivery-v1.1.0.json
    │   ├── email-v1.1.0.json
    │   └── identity-provider-v1.1.0.json
    ├── contract-compatibility.md
    ├── constitution-snapshot.md
    ├── checklists/
    │   └── requirements.md
    └── tasks.md

### Source Code

    backend/
    ├── pyproject.toml
    ├── alembic.ini
    ├── migrations/
    ├── src/review_platform/
    │   ├── api/
    │   ├── application/
    │   │   └── foundation_runtime.py
    │   ├── domain/
    │   ├── infrastructure/
    │   │   ├── db/
    │   │   ├── object_storage/
    │   │   ├── tasks/
    │   │   ├── auth/
    │   │   └── providers/
    │   ├── mcp/
    │   ├── contracts/
    │   ├── settings.py
    │   └── main.py
    └── tests/
        ├── contract/
        ├── state/
        ├── integration/
        ├── isolation/
        ├── live/
        └── fixtures/

    deploy/
    ├── compose.yaml
    ├── env.example
    └── mailpit/

**Structure Decision**: модульный монолит. API, worker, outbox relay, email worker и MCP — отдельные процессы с общими domain/application модулями. `application/foundation_runtime.py` является только composition adapter над реальными application/infrastructure components и не хранит in-memory domain state. Providers зависят от ports ядра; ядро providers не импортирует.

**Contract Decision**: `specs/001-backend-core/contracts/` — design-time source of truth. Version 1.1.0 is a frozen breaking pre-implementation correction over committed 1.0.0. `manifest.json` фиксирует frozen status и SHA-256 для schemas, fixtures, compatibility notes и byte-identical constitution snapshot. Bootstrap/recovery остаются local operator commands; REST mutations используют exact route/target contracts. T022 выполнила только mechanical candidate-to-frozen transition без semantic edits. Runtime package data синхронизируется deterministic command; Pydantic/FastAPI conformance не меняет canonical files.

**Implementation Order**: contract and SC traceability freeze → foundational persistence (`CommandReceipt`, `AuditEvent`, `OutboxMessage`, `Operation`) → US1-US3 → shared review spine → US4/US5 → delivery recovery → MCP agent transport. Static contract checks block Foundation; concrete MCP parity does not block stories before handlers exist.

## Phase 0: Research Result

Все технические неизвестные разрешены в [research.md](research.md). Точные версии зависимостей фиксируются lockfile при bootstrap. Выбрана стабильная SQLAlchemy 2.0 вместо prerelease 2.1.

## Phase 1: Design

- [data-model.md](data-model.md) определяет tenant keys, aggregates, state transitions и invariants.
- [context-traceability.md](context-traceability.md) связывает прежние сущности и экраны с текущей моделью и явно отмечает later scope.
- [contracts/openapi.yaml](contracts/openapi.yaml) определяет web/component interface.
- JSON Schemas определяют command, artifact, AI и provider boundaries.
- [contracts/manifest.json](contracts/manifest.json) описывает frozen contract set 1.1.0; последующие semantic changes требуют новой версии и compatibility/migration notes.
- [requirements-traceability.md](requirements-traceability.md) до implementation связывает FR-001..FR-085 и SC-001..SC-023 с fixture/sandbox, owner и воспроизводимой командой.
- [quickstart.md](quickstart.md) связывает команды проверки с acceptance gates.

## Post-Design Constitution Check

Все шесть принципов отражены в design artifacts, adversarial reviews завершены, `$speckit-analyze` вернул READY, а T022 механически заморозила contracts до runtime implementation. Stepik automatic delivery остаётся live gate. Google DOCX fixture подтверждён. GitHub fixture доступен текущему account, но GitHub App installation остаётся отдельным gate.

## Complexity Tracking

Нарушений, требующих обоснования, нет.
