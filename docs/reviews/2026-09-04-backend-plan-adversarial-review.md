# Adversarial review backend plan

**Дата:** 4 сентября 2026 года

**Reviewer:** независимый GPT-5.6 Sol, read-only

**Область:** `specs/001-backend-core/plan.md`, research, data model, quickstart, OpenAPI, JSON Schema и MCP manifest.

## Первый verdict

`NOT READY` для `speckit-tasks`. Найдено шесть P1 и один P2:

1. AI request не передавал неизменяемые тексты задания и критериев и способ чтения артефакта.
2. AI event endpoint принимал request и не фиксировал finality, sequence и семантику критериев.
3. Wire и internal command расходились и допускали неоднозначный actor/CAS contract.
4. REST и MCP не покрывали обязательный вертикальный сценарий.
5. Tenant и auth invariants не имели исполнимого concurrency protocol.
6. Outbox relay мог навсегда потерять claim или повторить внешний эффект.
7. План не задавал числовые лимиты и retention.

## Исправления

- AI request получил snapshots и digests задания и критериев, tenant-scoped artifact download и `jcs-sha256-v1` fingerprint с ID всех версий.
- AI request и event разделены; sequence действует внутри attempt, finality связана со status, полнота критериев проверяется contract tests.
- Wire command не принимает actor. Backend создаёт internal command из авторизации. Каждая команда задаёт command name, revision target, target ID и типизированный payload.
- OpenAPI расширен до полного вертикального сценария, включая operator recovery.
- Добавлен MCP `2026-07-28` manifest с 11 tools, типизированными inputs/outputs и mapping на REST operation IDs.
- Role mutation блокирует Organization и повторно проверяет последнего методиста; auth epoch проверяется перед worker mutation; tenant links имеют composite constraints.
- Outbox использует DB lease, `FOR UPDATE SKIP LOCKED`, stable message ID, reclaim и обязательный reconciliation неизвестного результата.
- Добавлены defaults и ceilings для GitHub artifacts, Google export, S3 staged upload, orphan cleanup и retention.

## Финальный verdict

Все семь findings закрыты. Новых P1 после исправлений не найдено.

**READY для `speckit-tasks`.** Реализация должна начинаться с failing schema, transport, state-machine, concurrency и isolation tests.
