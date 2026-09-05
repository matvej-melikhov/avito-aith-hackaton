# Запуск проекта

Команды выполняются из корня репозитория в Bash или Zsh. На Windows используйте WSL2.

## Что установить

- Node.js 22.12 или новее и npm — для фронтенда.
- Docker с работающим daemon и Docker Compose — для локальной интеграции.
- Python 3.13 и uv — для backend-тестов и работы с кодом вне контейнера.

## Локальная интеграция: браузер, API, MySQL и MinIO

Этот режим создаёт учебную организацию с двумя студентами, двумя ревьюерами, координатором, двумя потоками и четырьмя сданными работами. Сессии, права, черновики, результаты и очередь фоновых заданий сохраняются на сервере. AI и получение файлов из внешних источников используют явно обозначенные демонстрационные адаптеры.

Запустите только нужные сервисы отдельным Compose-проектом:

```sh
docker compose -p workspace-completion-local \
  -f deploy/compose.yaml -f deploy/compose.workspace.yaml \
  up --build -d --wait mysql redis minio api
```

Образ API содержит Alembic и миграции. При запуске применяются миграции, создаётся бакет и выполняется seed. Повторный запуск сохраняет пользовательские изменения. Seed отказывается заполнять базу с посторонней организацией или пользователями.

В другом терминале запустите обычный фронтенд:

```sh
npm ci --prefix frontend
BACKEND_URL=http://127.0.0.1:18000 npm --prefix frontend run dev
```

Откройте [http://localhost:5173](http://localhost:5173). На странице входа выберите учебного участника. Для смены участника выйдите из текущей сессии. Сервер выдаёт HttpOnly cookie и проверяет действующее членство при следующих запросах; роль не подменяется в браузере.

| Сервис | Адрес |
|---|---|
| Приложение | [localhost:5173](http://localhost:5173) |
| API | [127.0.0.1:18000](http://127.0.0.1:18000) |
| MinIO: скачивание файлов | [127.0.0.1:19000](http://127.0.0.1:19000) |
| Консоль MinIO | [127.0.0.1:19001](http://127.0.0.1:19001) |

Проверьте состояние и логи:

```sh
docker compose -p workspace-completion-local \
  -f deploy/compose.yaml -f deploy/compose.workspace.yaml ps
curl --fail --silent --show-error http://127.0.0.1:18000/health
curl --fail --silent --show-error http://127.0.0.1:18000/ready
docker compose -p workspace-completion-local \
  -f deploy/compose.yaml -f deploy/compose.workspace.yaml logs --tail=100 api
```

API должен вернуть `status: ok` и `status: ready`; контейнеры не должны перезапускаться. Подготовка артефактов, самопроверка, помощь ревьюеру и экспорт выполняются фоновым циклом API. Отдельные сервисы `worker`, `outbox-relay`, `email-worker` и `mcp` для этого сценария не нужны.

После изменения backend-кода повторите команду `up --build`. Данные находятся в volumes проекта `workspace-completion-local` и сохраняются при пересоздании API. Не используйте для учебного seed имя Compose-проекта действующей установки.

## Проверка экранов без бэкенда

```sh
npm ci --prefix frontend
npm --prefix frontend run dev:demo
```

Откройте [http://localhost:5173](http://localhost:5173). Демо работает на браузерных fixtures; его результаты не подтверждают работу серверной авторизации, БД или worker. Для проверки этих сценариев используйте локальную интеграцию выше. Перед переключением режима остановите предыдущий frontend через `Ctrl+C`.

## Установка без учебных данных

Используйте основной Compose-файл без `compose.workspace.yaml`. Скопируйте конфигурацию и настройте подключения:

```sh
test -f deploy/.env || cp deploy/env.example deploy/.env
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait mysql redis minio
docker compose --env-file deploy/.env -f deploy/compose.yaml build api
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  run --rm --no-deps api alembic upgrade head
```

Создайте настроенный S3-бакет и подготовьте организацию, identity provider и доступ пользователей средствами оператора установки. Локальный выбор участника отключён вне сочетания `REVIEW_PLATFORM_ENVIRONMENT=local` и `REVIEW_PLATFORM_WORKSPACE_FIXTURES=true`.

Bootstrap назначает первого методиста существующей организации; он не создаёт организацию и не настраивает провайдера:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  run --rm --no-deps api python -m review_platform.bootstrap --help
```

После подготовки запустите API:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait api
```

Основной Compose-файл передаёт только перечисленные в нём переменные. Дополнительные параметры workspace/AI передавайте через свой Compose override в `services.api.environment`. Для внешнего AI отключите fixtures, задайте `REVIEW_PLATFORM_WORKSPACE_ENABLED=true`, `REVIEW_PLATFORM_WORKSPACE_AI_URL` и при необходимости `REVIEW_PLATFORM_WORKSPACE_AI_TOKEN`. Вне local/test нужны HTTPS и `REVIEW_PLATFORM_LIVE_PROVIDERS_ENABLED=true`. Протокол и границы данных описаны в [AI handoff](specs/005-workspace-completion/ai-handoff.md).

Адрес `REVIEW_PLATFORM_S3_ENDPOINT_URL` используется сервером; `REVIEW_PLATFORM_S3_PUBLIC_ENDPOINT_URL` — для подписанных ссылок, доступных браузеру и потребителю AI. Потребитель должен иметь доступ к адресу ссылки из своей сети.

Встроенный MCP доступен по адресу `/mcp` процесса API и требует действующий токен агента. Отдельный контейнер `mcp` требует дополнительной AI-привязки. Не запускайте все сервисы общей командой `up`, если их конфигурация ещё не подготовлена. Контракты базового API и MCP находятся в [specs/001-backend-core/contracts](specs/001-backend-core/contracts).

## Проверки кода

```sh
uv python install 3.13
uv sync --directory backend --locked
npm ci --prefix frontend
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run check:api
uv run --directory backend pytest -m 'not live' -q
```

Backend-тесты используют собственные контейнеры. Им нужен работающий Docker; подключать их к действующей базе не требуется.

## Если запуск не удался

| Проблема | Что проверить |
|---|---|
| Порт занят | Остановите предыдущий локальный экземпляр или настройте свободные порты в своём override. При смене API-порта обновите `BACKEND_URL`; при смене MinIO-порта — публичный S3 URL; при смене frontend-порта — `REVIEW_PLATFORM_BROWSER_ORIGINS` |
| Seed сообщает о посторонних данных | Проверьте имя Compose-проекта и подключение к БД. Используйте отдельную пустую базу для учебной организации |
| Таблица или колонка не найдена | Пересоберите API и примените миграции к той базе, которую использует приложение |
| `configuration_required` | Проверьте обязательные настройки БД и S3 в окружении API |
| Нет учебных участников на странице входа | Проверьте, что запущен API с `compose.workspace.yaml`, а frontend работает через его proxy |
| `401` или `403` после входа | Войдите заново; проверьте роль, членство, срок сессии и разрешённый Origin |
| Скачивание не работает | Проверьте бакет и доступность публичного S3 URL из браузера |
| AI остаётся в ожидании | Проверьте логи API и долговечный lookup внешнего компонента. Неопределённый исход не означает подтверждённый отказ |

## Остановка с сохранением данных

Остановите frontend через `Ctrl+C`. Для локального fixture-стека:

```sh
docker compose -p workspace-completion-local \
  -f deploy/compose.yaml -f deploy/compose.workspace.yaml down
```

Команда удаляет контейнеры и сеть, сохраняя volumes. Не добавляйте `--volumes`, если нужны сохранённые работы и результаты.
