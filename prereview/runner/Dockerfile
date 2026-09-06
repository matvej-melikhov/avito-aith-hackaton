# Песочница prereview: исполняет собранный бинарник студента по сценариям задания.
# В образе нет ключей и нет кода сервиса, только движок, goose и migrate для миграций.
# Изоляция задаётся в deploy/compose.ai.yaml: внутренняя сеть без выхода наружу,
# read-only корень, tmpfs для работы, без capabilities, лимиты процессов и памяти.
# goose и migrate берутся готовыми релизными бинарниками: сборка из исходников
# не влезает в память маленькой podman-машины.
FROM python:3.13-slim
ARG TARGETARCH=arm64
ARG GOOSE_VERSION=v3.28.0
ARG MIGRATE_VERSION=v4.19.1
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 SANDBOX_WORK_DIR=/work
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && case "$TARGETARCH" in amd64) GOOSE_ARCH=x86_64 ;; *) GOOSE_ARCH="$TARGETARCH" ;; esac \
    && curl -fsSL -o /usr/local/bin/goose "https://github.com/pressly/goose/releases/download/${GOOSE_VERSION}/goose_linux_${GOOSE_ARCH}" \
    && chmod 755 /usr/local/bin/goose \
    && curl -fsSL "https://github.com/golang-migrate/migrate/releases/download/${MIGRATE_VERSION}/migrate.linux-${TARGETARCH}.tar.gz" \
       | tar -xz -C /usr/local/bin migrate \
    && goose --version && migrate -version \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY prereview/runner/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY prereview/runner/engine.py prereview/runner/app.py /app/
WORKDIR /app
USER 65534:65534
EXPOSE 8200
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8200/health')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
