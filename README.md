<h1 align="center">Avito AI Reviewer</h1>

<p align="center">
Проект хакатона <b>AI Product Hack 2026</b>, кейс Авито «AI Reviewer».
Платформа проверки домашних заданий с ИИ-помощником для ревьюера и координатора образовательных программ Авито. Модель готовит черновик проверки по каждому критерию, подтверждает каждый балл цитатой из работы, код проверяет, что цитата в работе есть, а решение всегда принимает человек.
</p>

<p align="center">
<a href="docs/defense/avito-ai-reviewer-defense.pptx">📊 Презентация (PPTX)</a> / <a href="docs/defense/avito-ai-reviewer-defense.key">📊 Презентация (Keynote)</a> / <a href="docs/demo/">🎬 Демо-ролики</a> / <a href="RUNNING.md">🚀 Запуск</a>
</p>

<p align="center">
<img src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white" alt="Python" />
<img src="https://img.shields.io/badge/FastAPI-0.116-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
<img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white" alt="React" />
<img src="https://img.shields.io/badge/TypeScript-5.9-3178C6?logo=typescript&logoColor=white" alt="TypeScript" />
<img src="https://img.shields.io/badge/Vite-8-646CFF?logo=vite&logoColor=white" alt="Vite" />
<img src="https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white" alt="MySQL" />
<img src="https://img.shields.io/badge/Redis-6.4-DC382D?logo=redis&logoColor=white" alt="Redis" />
<img src="https://img.shields.io/badge/LLM-DeepSeek-412991?logo=openai&logoColor=white" alt="LLM" />
</p>

<p align="center">
<img src="docs/design/banner.png" alt="Avito AI Reviewer Banner" width="100%" />
</p>

---

## В чём идея

У проверки домашних заданий сегодня нет единого состояния: работа лежит в GitHub или Stepik, критерии в условии, статус в Google Sheets, переписка в мессенджере. Ревьюер тратит около часа на работу, координатор до десяти часов в неделю на сверку и напоминания.

Мы не строим «оценщик работ», мы строим **слой управления проверкой**, в котором ИИ готовит черновик, а человек решает. Три правила, которые держит код:

1. **Каждый балл с цитатой, цитату проверяет код.** Модель обязана указать фрагмент работы с адресом «файл и строка». Не нашёл — балла нет, строка уходит ревьюеру с пометкой «нужен человек».
2. **Что можно проверить кодом — проверяется кодом.** Факт сборки `go build` и запуск в изолированной песочнице по сценариям из ТЗ курса значат больше, чем чтение кода моделью.
3. **Решение за человеком.** Система ничего не публикует и не ставит итоговую оценку. Сигнал об использовании ИИ живёт вне баллов.

## Как это выглядит

### Ревьюер

Ревьюер выбирает работу из пула — видит студента, задание, курс, сроки и статус.

![Пул работ ревьюера](docs/demo/screenshots/01-reviewer-pool.png)

Открывает карточку: оценки уже стоят, у каждой цитата из кода с номером строки, рядом факты сборки и запуска, сводка со стоимостью прогона. Слева — сигнал об ИИ (8%, уровень низкий) и черновик ответа студенту.

![Карточка ревью: критерии с цитатами, сигнал об ИИ, ответ студенту](docs/demo/screenshots/02-reviewer-card.png)

Прокручивает список критериев: формальные проверены кодом (зелёная галочка), содержательные моделью с цитатой, оценочные помечены и решаются человеком.

![Критерии с цитатами из кода и проверки кодом](docs/demo/screenshots/03-reviewer-criteria.png)

### Студент

Студентка открывает задание, видит условие, порог зачёта, штраф за просрочку и статус.

![Страница задания студента](docs/demo/screenshots/04-student-task.png)

До сдачи запускает самопроверку — короткий итог без баллов и без сигнала об ИИ: только что стоит перепроверить по условию. Решение о зачёте принимает ревьюер.

![Самопроверка студента: итог и подсказка без баллов](docs/demo/screenshots/05-student-self-review.png)

### Координатор

Координатор видит обзор: сколько домашек в потоках, сколько у ревьюеров, сколько без ревьюера, дедлайны. Ниже — пул проверок и таблица всех работ с фильтрами по статусам.

![Дашборд координатора: метрики, пул проверок, таблица работ](docs/demo/screenshots/06-coordinator-dashboard.png)

Заводит задание один раз: условие в Markdown-редакторе, материалы для студента, настройки штрафа и лимит самопроверок. Дальше ревьюеры разбирают пул сами.

![Мастер создания задания: условие, настройки, критерии](docs/demo/screenshots/07-coordinator-assignment.png)

## Что внутри

| Компонент | Описание | Стек |
|---|---|---|
| [backend/](backend/) | Платформа: организация, курсы, пулы, штрафы, статистика, 103 эндпоинта, 12 MCP-инструментов, 73 таблицы, 663 теста | Python 3.13, FastAPI, SQLAlchemy, MySQL, Redis, MinIO |
| [prereview/](prereview/) | Сервис ИИ-проверки: снимок, защита данных, проверки кодом, судья по критерию, сигнал об ИИ, 34 теста | Python, FastAPI, DeepSeek v4 flash |
| [prereview/runner/](prereview/runner/) | Изолированная песочница без сети для безопасного запуска Go-кода | Python, Postgres 16, goose |
| [frontend/](frontend/) | Кабинеты студента, ревьюера и координатора, своя дизайн-система, 56 тестов | React 19, TypeScript, Vite |
| [prompts/](prompts/) | Восемь инструкций модели как версионируемые файлы, sha256 пишется в журнал прогона | Markdown |
| [deploy/](deploy/) | Compose-стек: платформа, сервис проверки, песочница | Docker Compose |

### Быстрый старт

Нужны Docker с Compose, Python 3.13 с `uv`, Node.js 22 и ключ LLM в `.env` (`DEEPSEEK_API_KEY=...`).

```bash
# Бэкенд, ИИ-сервис и песочница
docker compose -p workspace-completion-local \
  -f deploy/compose.yaml -f deploy/compose.workspace.yaml -f deploy/compose.ai.yaml \
  up --build -d --wait mysql redis minio sandbox-db runner ai api

# Фронтенд (в новом терминале)
npm ci --prefix frontend && BACKEND_URL=http://127.0.0.1:18000 npm --prefix frontend run dev
```

Полная инструкция и разбор ошибок: **[RUNNING.md](RUNNING.md)**.

## Ссылки

- **[final/](final/)** — материалы итоговой сдачи (описание проекта, архитектура, защита данных, экономика)
- **[docs/NUMBERS.md](docs/NUMBERS.md)** — все числа проекта: замеры качества, метрики, источники и допущения
- **[specs/](specs/README.md)** — функциональные и аналитические спецификации с контрактами
- **[docs/demo/](docs/demo/)** — ролики сквозного сценария: [ревьюер](docs/demo/reviewer-assist.mp4), [студент](docs/demo/student-self-review.mp4), [координатор](docs/demo/coordinator-overview.mp4)

## Команда

| Участник | Роль | Зона |
|---|---|---|
| Матвей Мелихов | AI Product | постановка, интервью, критерии успеха, дизайн-система и макеты, сценарий демо, презентация |
| Владимир Губин | AI Engineer | платформа: бэкенд, контракты, модель данных, фронтенд, массовый прогон корпуса |
| Илья Поддуба | AI Engineer | ядро ИИ-проверки, evals и калибровка, песочница, сигнал об ИИ, демо-ролики |
