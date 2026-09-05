# Запуск проекта

Выполняйте команды из корня репозитория в Bash или Zsh. На Windows используйте WSL2. В корне должны находиться каталоги `frontend`, `backend` и `deploy`.

## Подготовка

Установите:

- Node.js 22.12 или новее и npm;
- Python 3.13 и uv;
- Docker с работающим daemon и Docker Compose с поддержкой `--wait`.

Проверьте инструменты:

```sh
node --version
npm --version
uv --version
docker info
docker compose version
```

Для проверки экранов достаточно Node.js и npm. Docker, Python и uv нужны для бэкенда и его тестов.

## Фронтенд без бэкенда

```sh
npm ci --prefix frontend
npm --prefix frontend run dev:demo
```

Откройте [http://localhost:5173](http://localhost:5173).

Переключайте роль в шапке приложения. Демо использует данные в памяти вкладки, не вызывает внешнюю AI-модель и сбрасывается при перезагрузке страницы. Ключи AI, Stepik и GitHub не требуются.

Для остановки нажмите `Ctrl+C` в терминале сервера.

## Основной локальный стек

### 1. Подготовьте конфигурацию

```sh
test -f deploy/.env || cp deploy/env.example deploy/.env
uv python install 3.13
uv sync --directory backend --locked
```

Оставьте `REVIEW_PLATFORM_LIVE_PROVIDERS_ENABLED=false` для локальной проверки.

Не запускайте все сервисы одной командой `docker compose up`: отдельный сервис `mcp` требует дополнительной настройки. Используйте перечисленные ниже имена сервисов.

### 2. Запустите инфраструктуру

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait mysql redis minio mailpit
```

### 3. Примените миграции

Для конфигурации из `deploy/env.example`:

```sh
export LOCAL_REVIEW_DATABASE_URL='mysql+asyncmy://review_platform:local-dev-only@127.0.0.1:13306/review_platform'
REVIEW_PLATFORM_DATABASE_URL="$LOCAL_REVIEW_DATABASE_URL" uv run --directory backend alembic upgrade head
REVIEW_PLATFORM_DATABASE_URL="$LOCAL_REVIEW_DATABASE_URL" uv run --directory backend alembic current
```

Если в `deploy/.env` изменены `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_PORT` или `MYSQL_DATABASE`, обновите `LOCAL_REVIEW_DATABASE_URL`. Специальные символы в логине и пароле должны быть URL-кодированы.

Для команд на хосте используйте `127.0.0.1` и опубликованный порт MySQL. Имя `mysql` и порт `3306` предназначены для соединений между контейнерами. Не подменяйте ими адрес подключения `uv`.

Миграции выполняйте из репозитория: контейнерный образ приложения не содержит `alembic.ini` и каталога миграций.

### 4. Соберите приложения и создайте бакет

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml build api worker outbox-relay email-worker
```

Создайте настроенный бакет, если его ещё нет:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps -T api python - <<'PY'
import os
import boto3
from botocore.exceptions import ClientError

client = boto3.client(
    "s3",
    endpoint_url=os.environ["REVIEW_PLATFORM_S3_ENDPOINT_URL"],
    region_name=os.environ["REVIEW_PLATFORM_S3_REGION"],
    aws_access_key_id=os.environ["REVIEW_PLATFORM_S3_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["REVIEW_PLATFORM_S3_SECRET_ACCESS_KEY"],
)
bucket = os.environ["REVIEW_PLATFORM_S3_BUCKET"]
try:
    client.head_bucket(Bucket=bucket)
except ClientError as error:
    if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
        raise
    client.create_bucket(Bucket=bucket)
print("Бакет доступен")
PY
```

### 5. Запустите API и фоновые процессы

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --wait api worker outbox-relay email-worker
```

### 6. Проверьте запуск

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
curl --fail --silent --show-error http://localhost:18000/health
curl --fail --silent --show-error http://localhost:18000/ready
```

У запущенных сервисов должны быть состояния `running` или `healthy`, без цикла перезапусков. В ответах API ожидаются `"status":"ok"` и `"status":"ready"`. Значение `configuration_required` означает, что серверу не хватает конфигурации.

| Сервис | Адрес по умолчанию |
|---|---|
| API | http://localhost:18000 |
| Встроенный MCP | http://localhost:18000/mcp |
| Консоль MinIO | http://localhost:19001 |
| Локальная почта Mailpit | http://localhost:18025 |

Успешный запуск процессов не создаёт организацию, пользователей и сессии и не настраивает внешние интеграции.

## Фронтенд с настоящим API

Остановите demo-сервер, если он занимает порт 5173.

```sh
test -f frontend/.env.local || cp frontend/.env.example frontend/.env.local
npm ci --prefix frontend
npm --prefix frontend run dev
```

В `frontend/.env.local` задайте адрес API:

```dotenv
BACKEND_URL=http://127.0.0.1:18000
```

Если изменили `API_PORT`, измените порт в `BACKEND_URL` и перезапустите frontend. Браузер отправляет запросы на `/api`, Vite перенаправляет их бэкенду. Ошибки API не заменяются демо-данными.

Для входа и работы с данными нужны подготовленная организация, настроенный identity provider и доступ пользователя. Ответ `401` до входа не означает, что API не запустился. Для проверки экранов без этих настроек используйте `dev:demo`; для проверки серверных сценариев — автоматические тесты с локальными fixtures.

Bootstrap назначает первого методиста **уже существующей** организации. Он не создаёт организацию и не настраивает провайдеров. Перед его использованием получите у оператора установки ID организации и точные `provider`, `issuer`, `subject` разрешённой учётной записи. Проверьте обязательные аргументы:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps api python -m review_platform.bootstrap --help
```

Не запускайте bootstrap без аргументов и не создавайте произвольные учётные записи или credentials для обхода настройки входа.

## MCP

### Встроенный MCP

Используйте `http://localhost:18000/mcp` после запуска `api`. Отдельный контейнер для этого не нужен. Отсутствие AI-привязки не мешает запуску встроенного MCP, но запуск AI-ревью требует настроенного внешнего AI-компонента.

Для вызовов получите действующий токен авторизованного агента. Пример чтения курсов с токеном в переменной `MCP_TOKEN`:

```sh
: "${MCP_TOKEN:?Задайте действующий токен агента}"
curl --fail --silent --show-error http://localhost:18000/mcp \
  -H "Authorization: Bearer $MCP_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: list_courses' \
  --data '{}'
```

Токену нужны разрешённая роль и scope `courses:read`. Транспорт принимает аргументы инструмента в теле запроса и имя инструмента в заголовке `Mcp-Name`; не отправляйте вместо этого JSON-RPC `initialize` или session handshake. Список инструментов и их аргументы находятся в `specs/001-backend-core/contracts/mcp-tools.json`.

### Отдельный контейнер MCP

Запускайте его только при необходимости отдельного процесса и порта. Сначала получите существующие идентификатор и версию привязки внешнего AI-компонента и сохраните их в переменных терминала:

- `REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_ID` — UUID привязки;
- `REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_VERSION` — положительная целочисленная версия.

Это ссылки на серверную конфигурацию, а не API-ключ модели. Запись должна принадлежать нужной организации, иметь provider `ai_review` и быть активной. Готового публичного REST-маршрута для создания такой привязки нет; её должен подготовить оператор интеграции.

Проверьте наличие параметров и передайте их контейнеру явно:

```sh
: "${REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_ID:?Задайте UUID существующей AI-привязки}"
: "${REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_VERSION:?Задайте версию AI-привязки}"
export REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_ID REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_VERSION
uv run --directory backend python -c 'from review_platform.mcp.__main__ import configured_ai_binding_from_environment; configured_ai_binding_from_environment(required=True)'
docker compose --env-file deploy/.env -f deploy/compose.yaml run --build --rm --no-deps --service-ports \
  -e "REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_ID=$REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_ID" \
  -e "REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_VERSION=$REVIEW_PLATFORM_AI_REVIEW_CREDENTIAL_BINDING_VERSION" \
  mcp
```

Параметры должны быть экспортированы в окружение для проверки через `uv`. В одном лишь `deploy/.env` их недостаточно: Compose передаёт приложению только явно настроенные переменные. Проверка конфигурации подтверждает формат ID и версии, а не наличие записи в базе или доступность AI-компонента.

В другом терминале:

```sh
curl --fail --silent --show-error http://localhost:18001/health
```

Адрес отдельного MCP — `http://localhost:18001/mcp`. Для остановки нажмите `Ctrl+C` в терминале этого контейнера. Если AI-привязка ещё не подготовлена, используйте встроенный MCP и не запускайте отдельный сервис.

## Проверка изменений

```sh
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run check:api
uv run --directory backend pytest -m 'not live' -q
```

Backend-тестам нужен работающий Docker: они поднимают собственные тестовые контейнеры. Не подключайте тесты к рабочей базе и не выполняйте downgrade существующей базы для проверки миграций.

## Если запуск не удался

| Проблема | Действие |
|---|---|
| `mcp` перезапускается с ошибкой об AI binding | Остановите только этот сервис: `docker compose --env-file deploy/.env -f deploy/compose.yaml stop mcp`. Используйте встроенный MCP либо выполните отдельную настройку выше |
| Порт уже занят | Остановите предыдущий экземпляр или измените соответствующий порт в `deploy/.env`. Для API также обновите `BACKEND_URL`; для frontend можно передать `--port 5174` после команды запуска |
| `uv` не соединяется с `mysql` | Используйте адрес хоста `127.0.0.1` и `MYSQL_PORT` в `LOCAL_REVIEW_DATABASE_URL` |
| Таблица не найдена | Примените `alembic upgrade head` к той базе, которую использует API |
| `NoSuchBucket` | Выполните создание бакета после запуска MinIO |
| `401` или `403` | Проверьте сессию, роль, scope токена и настройку входа. Для внешних браузерных запросов также проверьте разрешённый Origin; не отключайте проверку прав |
| Внешний провайдер не настроен | Продолжайте проверку экранов в demo-режиме; подключайте провайдера после подготовки его конфигурации |

Логи конкретного сервиса:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=100 api
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=100 worker outbox-relay email-worker
```

## Остановка

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml stop
```

Чтобы удалить контейнеры и сеть, сохранив данные:

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml down
```

Не добавляйте `--volumes`, если данные MySQL и MinIO нужно сохранить. Frontend остановите отдельно через `Ctrl+C`.
