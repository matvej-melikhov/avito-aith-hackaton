# Data Model: backend core

Все организационные сущности содержат organization_id. Все ссылки между ними проверяют совпадение organization_id. Идентификаторы — UUIDv7; время — UTC; пользовательское отображение использует timezone организации.

## Identity and access

### Organization

| Field | Meaning |
|---|---|
| id | Tenant boundary |
| slug, name | Идентичность и отображение |
| status | active или archived |
| revision | Optimistic concurrency |
| created_at, updated_at | Audit timestamps |

### User

| Field | Meaning |
|---|---|
| id | Глобальный пользователь установки |
| display_name | Отображаемое имя |
| status | active или disabled |
| created_at, updated_at | Audit timestamps |

### ExternalIdentity

| Field | Meaning |
|---|---|
| id, user_id | Локальная связь |
| provider, issuer, subject | Каноническая внешняя идентичность |
| verified_email | Опциональный подтверждённый email |
| status | active или revoked |

Уникальность: provider, issuer, subject.

### OrganizationMembership

| Field | Meaning |
|---|---|
| organization_id, user_id | Область и пользователь |
| roles | Набор methodologist, reviewer, student |
| status | active или archived |
| revision | CAS |
| auth_epoch | Версия немедленного отзыва полномочий |
| revoked_at, revoked_by | Отзыв |

Инвариант: после любой role mutation транзакция блокирует строку Organization, повторно считает active methodologists и оставляет минимум одного. Две разные membership rows не меняются без этой общей точки сериализации.

### Invitation

| Field | Meaning |
|---|---|
| organization_id | Организация |
| role | Только methodologist или reviewer |
| normalized_email | Получатель |
| token_digest | Хеш одноразового token |
| expires_at | Срок |
| issued_by, consumed_by | Audit |
| status | active, consumed, revoked, expired |

Token не хранится открыто. Consume атомарно проверяет email, status и expires_at.

### Session

organization_id, user_id, membership_revision, auth_epoch, token_digest, expires_at и revoked_at. Каждый read, write и worker execution повторно сравнивает auth_epoch с текущим membership. Несовпадение отменяет операцию до чтения или изменения данных.

### AgentAuthorization

| Field | Meaning |
|---|---|
| organization_id, user_id | Представляемый пользователь |
| agent_id | Идентичность агента |
| scopes | Разрешённые операции |
| expires_at, revoked_at | Жизненный цикл |
| revision | CAS |

Авторизация содержит token_digest и закрытый набор scopes. Grant/revoke выполняет только интерактивная Session того же user. Revocation атомарно инвалидирует token и ожидающие CommandReceipt до их выполнения.

## Learning structure

### Course

| Field | Meaning |
|---|---|
| organization_id, id | Tenant key |
| title, description | Локальные данные |
| source_kind | external или standalone |
| status | active или archived |
| revision | CAS |

### CourseRun

| Field | Meaning |
|---|---|
| organization_id, id, course_id | Конкретный поток |
| title | Отображение потока |
| starts_at, ends_at | Период |
| timezone | Интерпретация локальных дедлайнов |
| status | draft, active, archived |
| revision | CAS |

### CourseMembership

| Field | Meaning |
|---|---|
| organization_id, course_run_id, user_id | Область участия |
| kind | student или reviewer |
| source | imported, invitation, self_selected |
| status | active, removed, archived |
| external_version | Версия импорта |
| joined_at, removed_at | История |

Уникальность: organization_id, course_run_id, user_id, kind.

### Homework and HomeworkVersion

Homework хранит стабильную идентичность внутри Course. HomeworkVersion содержит version_number, student_text, max_score, artifact_kinds, estimated_review_minutes, submission_deadline, review_deadline, published_at и revision.

Publication связывает HomeworkVersion с CourseRun. Дедлайн каждой SubmissionVersion копируется из эффективной публикации и не меняется ретроактивно.

### CourseRunHomework

Явно связывает CourseRun, Homework и опубликованную HomeworkVersion. Содержит submission_deadline, review_deadline, status и revision. Все foreign keys включают organization_id; ссылка на сущность другой организации невозможна на уровне БД.

### CriterionSet and Criterion

CriterionSet относится к HomeworkVersion. Criterion содержит stable criterion key, position, title, description, max_points и active. Сумма max_points должна согласовываться с HomeworkVersion.max_score до публикации.

При переводе открытой проверки на новый CriterionSet значения переносятся только по stable criterion key; остальные становятся unset.

## Submission and artifacts

### Submission

Уникальность: organization_id, course_run_id, homework_id, student_id. Хранит current_predeadline_version_id и revision.

### SubmissionVersion

| Field | Meaning |
|---|---|
| organization_id, id, submission_id | Идентичность |
| sequence | Порядок версий |
| homework_version_id | Эффективные требования |
| artifact_reference_id | Сданная ссылка |
| submitted_at | Серверное время |
| effective_deadline | Зафиксированный дедлайн |
| phase | before_deadline или revision |
| status | validating, ready, access_error, pending_review, superseded |
| revision | CAS |

До дедлайна новая ready-версия заменяет current_predeadline_version_id. После дедлайна она остаётся pending до открытия ReviewIteration.

### ArtifactReference

Provider-neutral original URL, provider, mutable locator, capability status и last_checked_at.

Успешный preflight создаёт или переиспользует tenant-scoped ArtifactReference и возвращает его opaque ID. Недоступный или неподдерживаемый артефакт не выдаёт пригодный для submit ID.

### ArtifactVersion

Immutable provider identity: reference_id, provider_version, content_digest, object_key, media_type, byte_size, captured_at и metadata. Уникальность reference_id, content_digest.

## Human review

### ReviewCase

Уникальность: organization_id, course_run_id, homework_id, student_id. Связывает все SubmissionVersion и ReviewIteration одного прохождения задания.

### ReviewIteration

| Field | Meaning |
|---|---|
| organization_id, id, review_case_id | Идентичность |
| iteration_number | Монотонный номер |
| submission_version_id | Проверяемая сдача |
| artifact_version_id | Неизменяемое содержимое |
| homework_version_id, criterion_set_id | Требования |
| responsible_reviewer_id | Информационный ответственный |
| status | queued, in_review, ready_to_publish, published, canceled |
| current_revision_id | Текущая человеческая ревизия |
| revision | CAS |
| predecessor_iteration_id | Предыдущая итерация при correction или requirements migration |
| origin | initial, resubmission, correction или requirements_migration |
| requirements_changed_at | Момент появления более новых требований |

Инварианты: одна active ReviewIteration на ReviewCase; проверяемые версии не меняются. New artifact требует new SubmissionVersion и ReviewIteration.

Correction создаёт successor iteration со snapshot опубликованной ReviewRevision. Requirements migration создаёт successor iteration с новой HomeworkVersion/CriterionSet и переносит только решения по совпадающим stable criterion keys. Predecessor никогда не изменяется.

### ReviewRevision

Immutable revision: iteration_id, revision_number, author_user_id, base_revision_id, criterion scores, feedback, total_score, state draft или published, created_at.

Публикация требует expected current revision. Текущий итог — published revision ReviewIteration с максимальным iteration_number.

### ReviewCriterionDecision

Окончательное человеческое решение по одному Criterion внутри ReviewRevision: criterion_id, AI suggestion ID при наличии, points, decision, reason и выбранные evidence references. Уникальность: review_revision_id, criterion_id. Published revision содержит ровно одно решение для каждого активного критерия.

### ReviewNote

Дополнительное замечание ревьюера. Оно может быть связано с Criterion или относиться ко всей работе. Содержит review_revision_id, optional criterion_id, text, author и position.

### AvailabilityPlan and ReviewerCourseSelection

AvailabilityPlan содержит reviewer_id, planned_minutes, until_at, revision. Это advisory signal.

ReviewerCourseSelection содержит course_run_id, reviewer_id и active. Рекомендация сортирует eligible work по review deadline, continuity, submission time, затем advisory load.

### ReviewResponsibility

Информационная история того, кто начал или продолжил проверку: review_case_id, optional review_iteration_id, reviewer_id, action started, joined, released или completed, occurred_at и actor_id. Сущность не даёт эксклюзивного права и не блокирует других ревьюеров или методистов.

## AI boundary

### AIReviewRun

| Field | Meaning |
|---|---|
| organization_id, id, review_iteration_id | Идентичность |
| input_fingerprint | Уникальный канонический fingerprint |
| artifact_version_id, content_digest | Работа |
| homework_version_id, criterion_set_id | Требования |
| contract_version | Схема обмена |
| status | pending, running, partial, succeeded, retryable_failed, action_required, stale |
| current_attempt_no | Текущая попытка |
| created_at, finished_at | Timing |

Уникальность: organization_id, input_fingerprint.

`input_fingerprint` вычисляется как SHA-256 от RFC 8785 canonical JSON с contract version, organization ID, ReviewIteration ID, ArtifactVersion ID и digest, HomeworkVersion ID и digest, CriterionSet ID и digest. AI request содержит неизменяемые snapshots задания и критериев и короткоживущий tenant-scoped URL чтения артефакта.

### AIReviewAttempt

run_id, attempt_number, status, last_sequence, started_at, finished_at, error_code и sanitized_error. Sequence монотонна внутри attempt. Событие старой попытки сохраняется в истории, но не меняет текущее состояние run; event_id уникален глобально.

Final succeeded event обязан содержать ровно один suggestion для каждого criterion из request. Повторы criterion ID, неизвестные ID, неполный список и балл в `not_checked` отклоняются contract validation.

### AICriterionSuggestion and AISignal

Suggestions immutable и отделены от ReviewRevision. На каждый ожидаемый criterion_id хранится status suggested, needs_human или not_checked; proposed_points nullable; reason, evidence, confidence, reviewer_note, student_feedback, flags.

AISignal хранит level, evidence, limitations и questions. Он не влияет на total_score автоматически.

## Publication and operations

### ReviewPublication

organization_id, review_iteration_id, review_revision_id, publication_request_id nullable, publication_version, published_by, published_at, status и revision. `published_by` всегда ссылается на интерактивного reviewer/methodologist, не на agent identity.

### PublicationRequest

Tenant-scoped идемпотентный запрос агента на публикацию exact ReviewRevision: organization_id, review_iteration_id, review_revision_id, requested_by_user_id, agent_id, agent_authorization_id, idempotency_key, status pending, confirmed, rejected, expired или superseded, expires_at, confirmed_by, confirmed_at и revision. Он не меняет текущий результат и не создаёт ExternalDelivery. Human publish атомарно подтверждает подходящий pending request и связывает его с ReviewPublication.

### ExternalDelivery

| Field | Meaning |
|---|---|
| organization_id, publication_id, destination | Логический ключ |
| payload_version, payload_digest | Отправленное представление |
| state | pending, processing, retryable_failed, unknown_outcome, reconciling, succeeded, action_required, superseded |
| attempt_count, next_attempt_at | Retry |
| external_id, external_url | Reconciliation |
| last_error_code, sanitized_error | Диагностика |
| revision | CAS |

Уникальность: organization_id, publication_id, destination, payload_version.

### OutboxMessage

organization_id, message_id, aggregate_type, aggregate_id, event_type, payload_version, payload, available_at, lease_owner, lease_token, lease_expires_at, enqueue_state, completed_at, attempts, max_attempts и last_error. Создаётся в одной транзакции с domain mutation.

Relay выбирает сообщения через FOR UPDATE SKIP LOCKED, назначает ограниченный lease и публикует message_id в Redis. Истёкший lease можно получить снова. Worker атомарно claims соответствующую DB operation по стабильному message/delivery key и повторно проверяет organization, auth_epoch и актуальность версии. Неоднозначный внешний результат переводится в unknown_outcome, затем reconciling; обычный retry до reconciliation запрещён.

### CommandReceipt

organization_id, idempotency_key, request_id, command_name, target_id, expected_revision, actor snapshot, status и result reference. Уникальность organization_id, idempotency_key. Wire actor не принимается от клиента: ApplicationCommand создаётся сервером из текущей session или agent authorization.

### Operation and OperationAttempt

Operation — tenant-scoped наблюдаемая identity для course import, artifact capture, AI review и delivery orchestration: kind, input_version, state, attempts, created_at, updated_at, finished_at, error_code и sanitized_error. OperationAttempt хранит номер, worker identity, started_at, finished_at, outcome и sanitized_error. GET operation читает эту модель независимо от конкретного worker.

### RequestActor

Discriminated union: `user` содержит user_id, membership_revision и auth_epoch; `agent` дополнительно содержит agent_id и agent_authorization_id; `installation_operator` содержит installation_operator_id и reason и допустим только для bootstrap/recovery commands.

### AuditEvent

organization_id, actor_type, actor_user_id, installation_operator_id, agent_id, agent_authorization_id, action, entity_type, entity_id, before_revision, after_revision, request_id, trace_id, outcome, sanitized_details, occurred_at.

## State transitions

| Aggregate | Allowed transitions |
|---|---|
| CourseRun | draft → active → archived; archived → active |
| Invitation | active → consumed, revoked или expired |
| SubmissionVersion | validating → ready или access_error; ready → pending_review или superseded |
| ReviewIteration | queued → in_review → ready_to_publish → published; active → canceled |
| AIReviewRun | pending → running → partial → succeeded; running/partial → retryable_failed → running; any nonterminal → action_required или stale |
| ExternalDelivery | pending → processing → succeeded; processing → retryable_failed или unknown_outcome; unknown_outcome → reconciling → succeeded/retryable_failed/action_required |
| PublicationRequest | pending → confirmed, rejected, expired или superseded |

Terminal state не регрессирует. Superseded delivery и stale AI result остаются читаемыми, но не действуют.

## Storage lifecycle

S3 object key начинается с organization ID и ArtifactVersion ID. Запись проходит staged upload, проверку размера и SHA-256, создание DB row, затем promotion; незавершённые staged objects старше 24 часов удаляет garbage collector.

Все tenant-owned foreign keys используют organization_id как часть candidate key и composite foreign key. Миграционные negative tests обязаны отклонять cross-tenant links.
