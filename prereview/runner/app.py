"""HTTP-обёртка песочницы. Принимает бандл (бинарник, миграции, манифест) и сценарии, отдаёт факты.

Секретов здесь нет: адрес тестовой базы приходит из окружения контейнера, а процесс студента
получает только отдельную роль и базу на свой сценарий.
"""

from __future__ import annotations

import base64
import os
import shutil
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine import Engine, go_platform

MAX_BUNDLE = 64_000_000
engine = Engine(work_root=os.environ.get("SANDBOX_WORK_DIR") or None,
                pg_admin_dsn=os.environ.get("SANDBOX_PG_DSN") or None)
slots = threading.BoundedSemaphore(int(os.environ.get("SANDBOX_CONCURRENCY", "2")))
app = FastAPI(title="prereview sandbox", docs_url=None, redoc_url=None)


class RunRequest(BaseModel):
    bundle_b64: str = Field(..., description="zip: app, manifest.json, каталоги миграций")
    spec: dict
    timeout: float = 150.0


@app.get("/health")
def health() -> dict:
    goos, goarch = go_platform()
    return {"status": "ok", "goos": goos, "goarch": goarch, "postgres": bool(engine.pg_admin_dsn),
            "tools": {k: bool(v) for k, v in engine.tools.items()},
            "work_dir": engine.work_root, "free_mb": shutil.disk_usage(engine.work_root).free // 1_000_000}


@app.post("/run")
def run(req: RunRequest) -> dict:
    try:
        bundle = base64.b64decode(req.bundle_b64, validate=True)
    except ValueError as e:
        raise HTTPException(400, f"bundle_b64: {e}") from e
    if len(bundle) > MAX_BUNDLE:
        raise HTTPException(413, "bundle too large")
    if not slots.acquire(timeout=30):
        raise HTTPException(503, "sandbox busy")
    try:
        return engine.run(bundle, req.spec, timeout=min(max(req.timeout, 10.0), 300.0)).to_dict()
    finally:
        slots.release()
