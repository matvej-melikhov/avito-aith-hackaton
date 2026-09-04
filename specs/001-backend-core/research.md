# Research: backend core

## Runtime

**Decision:** Python 3.13, FastAPI, Pydantic 2, Uvicorn.

**Rationale:** Команда выбрала Python/FastAPI. Один async runtime подходит HTTP, provider adapters и Taskiq. Pydantic-модели служат источником OpenAPI и JSON Schema.

**Alternatives:** Django добавляет ненужный встроенный UI; Go усложняет общий контракт с Python AI-компонентом.

## Storage and migrations

**Decision:** MySQL 8.4 LTS, InnoDB, utf8mb4, SQLAlchemy 2.0 async с asyncmy, Alembic.

**Rationale:** InnoDB даёт транзакции и блокировки для инварианта последнего методиста, CAS-публикаций и outbox. SQLAlchemy 2.0 официально поддерживает asyncmy. Ветка 2.1 пока beta.

**Alternatives:** PostgreSQL удобнее с RETURNING, но противоречит выбору команды. Синхронный driver блокирует async обработчики.

## Background work

**Decision:** Taskiq с Redis broker. MySQL outbox остаётся источником истины; Redis только доставляет сигнал worker.

**Rationale:** Потеря Redis не должна терять AI-запуск или внешнюю публикацию. Outbox записывается вместе с бизнес-событием, relay повторно ставит незавершённые записи.

**Alternatives:** Только Redis не обеспечивает атомарность. Celery тяжелее для первой версии.

## Object storage

**Decision:** S3-compatible storage, MinIO в локальном окружении.

**Rationale:** GitHub archives, DOCX и снимки не должны раздувать MySQL. В БД остаются object key, размер, media type и SHA-256.

## Authentication

**Decision:** Методист и студент входят через Stepik OAuth. Ревьюер входит по email magic link. Первый методист задаётся одноразовой bootstrap-конфигурацией.

**Rationale:** У ревьюера может не быть Stepik. Magic link подтверждает email без постоянного пароля. Внешние refresh tokens хранятся шифрованными.

## REST and MCP

**Decision:** REST/OpenAPI 3.1 для web и component API. MCP 2026-07-28 со stateless HTTP core поверх того же application layer.

**Rationale:** Бизнес-команды, авторизация, idempotency и optimistic concurrency совпадают для web и агента. MCP не вызывает providers в обход core.

Агент может подготовить черновик и создать `PublicationRequest`, но MCP не публикует результат напрямую. Публикация требует отдельной интерактивной session-команды reviewer/methodologist для точной ReviewRevision. AgentAuthorization выдаётся и отзывается только интерактивным пользователем, содержит закрытые scopes и TTL; каждый MCP tool объявляет required role и scope.

## Contract authority and freeze

**Decision:** Файлы в `specs/001-backend-core/contracts/` являются design-time source of truth. Первая согласованная версия замораживается как 1.0.0 вместе с `manifest.json`; runtime copies создаются одной deterministic sync-командой и проверяются по SHA-256. Pydantic и FastAPI OpenAPI обязаны соответствовать замороженным артефактам, но не перезаписывают их автоматически.

**Rationale:** Один канонический набор предотвращает расхождение checked-in JSON/YAML, package data и runtime-generated schemas. После freeze любое изменение требует новой версии, changelog и compatibility/migration rules. До первой реализации контракты исправляются один раз и только затем получают статус frozen.

## Provider contracts

**Decision:** Course import, artifact access, outbound delivery/reconciliation and email use отдельные versioned JSON Schemas and shared fixtures. Python Protocols are adapters to these schemas, not a replacement for them.

**Rationale:** Backend, Stepik, GitHub, Google Docs and email providers can be implemented by different owners without guessing undocumented payloads or authentication behavior.

## Concurrent commands

**Decision:** Каждая mutation получает request ID, idempotency key и expected revision. Смена состояния выполняется CAS-транзакцией.

**Rationale:** Несколько редакторов не блокируются и не могут молча затереть друг друга.

| Command | Expected revision target |
|---|---|
| create invitation, start course import | Organization |
| change roles | OrganizationMembership; дополнительно блокируется Organization |
| archive/restore course | Course |
| create homework version | Homework |
| publish homework version | CourseRunHomework |
| select reviewer courses, set availability | OrganizationMembership |
| submit work | Submission |
| open review iteration | ReviewCase |
| save/publish review, start AI | ReviewIteration |
| retry delivery | ExternalDelivery |

Role mutations блокируют строку Organization через SELECT FOR UPDATE и повторно считают active methodologists в той же транзакции. Все tenant links используют composite organization foreign keys. Session и pending command содержат auth epoch, который повторно проверяется перед выполнением.

Installation operator является отдельным actor variant и не подменяется product User. Operator commands содержат installation_operator_id and reason; user/agent commands contain membership revision and auth epoch.

## Review evolution and publication

**Decision:** Published ReviewRevision immutable. Correction and requirements migration create successor ReviewIteration records linked to their predecessor. Migration transfers only matching stable criterion keys; correction begins from an immutable snapshot of the published revision. Review responsibility is append-only history with started, joined, released and completed events and never grants an exclusive lock.

Agent publication is two-step: an agent creates an idempotent PublicationRequest for an exact ReviewRevision; a human session publishes that exact revision and links the resulting ReviewPublication to the request.

**Rationale:** This preserves immutable inputs and human control while allowing safe collaboration, corrections and requirement changes without rewriting history.

## AI boundary

**Decision:** Backend создаёт AIReviewRun по полному input fingerprint; Taskiq worker вызывает внешний AI component через версируемый HTTP/JSON contract. Попытки и события отделены от логического запуска.

**Rationale:** AI разрабатывается отдельно. Fingerprint связывает результат с artifact digest, homework version, criterion set и contract version. Suggestions не изменяют human revision.

Fingerprint `jcs-sha256-v1` — SHA-256 от RFC 8785 JSON Canonicalization Scheme объекта с `contract_version`, `organization_id`, `review_iteration_id`, `artifact_version_id`, `artifact.content_digest`, `homework.version_id`, `homework.digest`, `criteria.set_id` и `criteria.digest`. Backend и AI component проверяют одинаковые contract vectors. Одинаковые байты в разных версиях или итерациях не переиспользуют run.

## External delivery

**Decision:** Transactional outbox, стабильный delivery key, состояния unknown outcome и reconciling, provider-specific reconciliation.

**Rationale:** HTTP timeout не доказывает применение запроса. Слепой retry создаёт дубли или возвращает старый балл.

Relay выбирает outbox rows через FOR UPDATE SKIP LOCKED, выдаёт expiring lease и публикует стабильный message ID. Worker claims DB operation до эффекта. Unknown provider outcome всегда проходит reconciliation до нового вызова.

## Testing

**Decision:** pytest, AnyIO, HTTPX ASGI transport, Testcontainers для MySQL/Redis/MinIO, JSON fixtures, mock providers и отдельные live markers.

**Rationale:** Кодовые спецификации проверяют contracts и state machines. Mock green не считается доказательством внешнего API.

Static schema/freeze tests become green in Foundation. Concrete HTTP/MCP parity tests stay inside the story that owns the corresponding handlers and cannot block earlier stories. Resource-limit, redaction, outbox-lease and revocation tests precede their implementations. A machine-readable live-gate ledger records NOT_RUN, BLOCKED, PASS or FAIL for every provider.

## Process topology

**Decision:** Модульный монолит, отдельные процессы API, worker, outbox relay и MCP, один общий domain/application package.

**Rationale:** Это минимальная топология надёжных фоновых задач без преждевременного деления на микросервисы.
