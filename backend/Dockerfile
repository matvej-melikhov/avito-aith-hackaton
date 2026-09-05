# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.20 AS uv

FROM python:3.13.7-slim-bookworm AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=uv /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

FROM python:3.13.7-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src ./src
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations

FROM runtime AS api
CMD ["python", "-m", "review_platform.main"]

FROM runtime AS worker
CMD ["python", "-m", "review_platform.infrastructure.tasks"]

FROM runtime AS outbox-relay
CMD ["python", "-m", "review_platform.infrastructure.tasks.relay_main"]

FROM runtime AS email-worker
CMD ["python", "-m", "review_platform.infrastructure.tasks.email_main"]

FROM runtime AS bootstrap
CMD ["python", "-m", "review_platform.bootstrap"]

FROM runtime AS operator
CMD ["python", "-m", "review_platform.operator"]

FROM runtime AS mcp
CMD ["python", "-m", "review_platform.mcp"]
