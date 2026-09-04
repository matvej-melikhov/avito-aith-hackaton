# Context traceability: docs → backend core

Источники сверки: docs/platform-screens/entities-and-screens.md, docs/03-platform.md, docs/04-contracts.md, docs/design-doc.md, docs/07-scenario-and-growth.md, docs/09-assignment.md, docs/GRADING_SYSTEM.md и docs/PIPELINE.md.

При конфликте раннего vision и текущих решений приоритет имеют docs/program-scope.md, docs/platforms/review-platform.md и спецификации specs/001–specs/004.

## Сущности

| Сущность из docs | Текущая модель | Статус и решение |
|---|---|---|
| Пользователь | User, ExternalIdentity | Перенесено; студент/методист используют Stepik, ревьюер — подтверждённый email |
| Роль пользователя | OrganizationMembership.roles | Перенесено; роли можно совмещать, отдельного admin нет |
| Профиль ревьюера | ReviewerCourseSelection, AvailabilityPlan | Частично: курсы и план часов входят; темы, internal/external и отпуск отложены |
| Курс | Course | Перенесено |
| Поток | CourseRun | Перенесено и стало обязательной частью ключей сдачи и ревью |
| Задание | Homework, HomeworkVersion | Перенесено; условие создаётся в платформе и версионируется |
| Требование | CriterionSet, Criterion | Перенесено |
| Публикация задания | CourseRunHomework | Перенесено; связывает версию задания, поток и его дедлайны |
| Зачисление на поток | CourseMembership | Перенесено; основной источник — импорт roster Stepik |
| Закрепление ревьюера | ReviewerCourseSelection, AvailabilityPlan | Изменено: ревьюер сам выбирает курсы, часы — мягкий сигнал |
| Домашка | Submission, SubmissionVersion, ArtifactReference, ArtifactVersion | Разделено на логическую сдачу, её версии, ссылку и снимок |
| Взятие | ReviewResponsibility | Перенесено как неэксклюзивная история отдельных started/joined/released/completed events |
| Сигнал об ИИ | AISignal | Перенесено; хранится отдельно и не влияет на балл автоматически |
| Попытка саморевью | — | Осознанно later scope; не нужна обязательному вертикальному сценарию |
| Ревью | ReviewCase, ReviewIteration, ReviewRevision, ReviewPublication | Разделено на историю задания, одну проверку версии, правки и публикацию |
| Вердикт | AICriterionSuggestion, ReviewCriterionDecision | Разделено на предложение AI и окончательное решение человека |
| Дополнительное замечание | ReviewNote | Перенесено; criterion_id может отсутствовать |
| Правка/журнал | AuditEvent | Перенесено и расширено actor, agent, request и trace |
| Организация | Organization | Добавлено для self-hosted tenant boundary и будущего SaaS |
| Внешняя доставка | ExternalDelivery, OutboxMessage | Добавлено для Stepik/GitHub, retry и reconciliation |
| Агентский доступ | AgentAuthorization | Добавлено для MCP |
| Запрос публикации агентом | PublicationRequest | Добавлено как human-approval boundary; агент не создаёт ReviewPublication |

## Экраны и пользовательские возможности

| Ранний экран/возможность | Текущий статус |
|---|---|
| Прикрепление артефакта студентом | Core: GitHub и Google Docs preflight и submit |
| Саморевью студента | Later scope |
| Повторная сдача | Core: новая SubmissionVersion, затем отдельная ReviewIteration |
| Статус домашки | Core через submission, AI, review и delivery states |
| Финальная обратная связь | Core в нашей платформе |
| Два пути регистрации ревьюера | Изменено: reviewer-only через email magic link; методист через Stepik |
| Доступность и темы ревьюера | Часы и курсы входят; темы и отпуск later |
| Ёмкость и пул | Изменено на pull-рекомендацию и мягкий план часов |
| Карточка работы | Core: AI suggestions, human decisions, notes, responsibility history, publication request и публикация человеком |
| Сравнение версий | Core как связь двух ArtifactVersion; визуальная форма определяется frontend |
| Личная статистика ревьюера | Later scope; audit сохраняет будущие данные |
| Архив ревьюера | Данные сохраняются; отдельный экран later |
| Создание курса | Core import Stepik; standalone course только backend extension |
| Создание потока | Core CourseRun |
| Создание задания | Core в нашей платформе |
| Ссылка для Stepik | Core binding; live grade delivery имеет внешний gate |
| Дашборд методиста | Core состояния и исключения; визуальная агрегация frontend |
| Управление ревьюерами | Core: приглашение в организацию; курсы выбирает ревьюер |
| Аварийное распределение | Изменено: методист видит исключение, жёсткого автоматического наказания нет |
| Статистика качества | AI evals и продуктовая аналитика принадлежат другому scope |

## Осознанно не перенесено

- Avito ID как обязательная идентичность ревьюера.
- Автоматическое создание студента из неподписанного параметра ссылки.
- Жёсткая квота работ и эксклюзивное взятие.
- Автоматический штраф за дедлайн.
- Google Sheets как источник истины.
- Саморевью студента в первой реализации.
- Темы экспертизы, отпуск, уведомления и личная статистика как самостоятельные feature.
- Реализация AI-моделей внутри backend.

## Новые сущности, которых не было в раннем vision

- Organization и tenant-scoped constraints.
- Session, Invitation и AgentAuthorization.
- CommandReceipt и auth epoch.
- AIReviewRun и AIReviewAttempt.
- ExternalDelivery и OutboxMessage.
- Неизменяемые ArtifactVersion и contract fingerprints.
- PublicationRequest и successor ReviewIteration для human approval, correction и requirements migration.

Эти добавления появились из требований self-hosted изоляции, совместной работы без locks, MCP, отдельного AI-компонента и надёжной внешней доставки.
