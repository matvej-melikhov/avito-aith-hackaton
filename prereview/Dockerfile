# AI-сервис prereview: Python + pandoc + poppler + Node (DeepSeek Harness как read-only исследователь).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DSH_HOME=/data/dsh-home
RUN apt-get update && apt-get install -y --no-install-recommends \
      pandoc poppler-utils curl ca-certificates git \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @deepseek-ai/dsh@0.1.2-rc.1 \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/

WORKDIR /app/prereview
COPY prereview/pyproject.toml prereview/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY prereview/ ./
COPY prompts/ /app/prompts/
RUN uv sync --locked --no-dev

ENV PREREVIEW_PROMPTS_DIR=/app/prompts \
    PREREVIEW_ASSIGNMENTS_DIR=/app/prereview/assignments \
    PREREVIEW_PRICING_FILE=/app/prereview/pricing.json \
    PREREVIEW_DATA_DIR=/data \
    PATH="/app/prereview/.venv/bin:$PATH"
VOLUME ["/data"]
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8100/health')"
CMD ["uvicorn", "prereview.service.app:app", "--host", "0.0.0.0", "--port", "8100"]
