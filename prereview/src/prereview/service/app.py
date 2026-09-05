"""HTTP-сервис по контракту specs/005-workspace-completion/ai-handoff.md.

POST /v2/self-reviews/{run_id}, POST /v2/review-assists/{run_id}: сохранить intent,
скачать снимок, вернуть событие running (200) и выполнить работу в фоне.
GET .../{run_id}?attempt=N: последнее событие или 404, если прогон неизвестен.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from prereview import __version__
from prereview.artifact.fetch import ArtifactError, fetch_artifact
from prereview.config import Settings, get_settings
from prereview.contracts import (
    CONTRACT_VERSION,
    ReviewAssistEvent,
    ReviewAssistRequest,
    SelfReviewEvent,
    SelfReviewRequest,
)
from prereview.pipeline import PipelineError, run_review_assist, run_self_review
from prereview.service.store import Store

log = logging.getLogger("prereview.service")

KINDS = {
    "self-reviews": (SelfReviewRequest, SelfReviewEvent, "self_review"),
    "review-assists": (ReviewAssistRequest, ReviewAssistEvent, "review_assist"),
}


def _store(settings: Settings) -> Store:
    return Store(settings.data_dir / "service.sqlite")


def _artifacts_dir(settings: Settings) -> Path:
    d = settings.data_dir / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _event(request: dict, status: str, result: dict | None = None, error_code: str | None = None) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": request["run_id"],
        "attempt": request["attempt"],
        "input_fingerprint": request["input_fingerprint"],
        "status": status,
        "result": result,
        "error_code": error_code,
    }


def execute(kind: str, run_id: str, attempt: int, settings: Settings) -> None:
    """Фоновая работа: выполнить пайплайн и записать финальное событие. Никогда не бросает."""
    store = _store(settings)
    row = store.get_run(run_id, attempt)
    if row is None:
        return
    last = store.last_event(run_id, attempt)
    if last and last["status"] in {"succeeded", "failed"}:
        return
    store.set_status(run_id, attempt, "running")
    request_model, _, _ = KINDS[kind]
    request = request_model.model_validate(row.request)
    data = Path(row.artifact_path).read_bytes() if row.artifact_path and Path(row.artifact_path).exists() else None
    try:
        if data is None:
            raise PipelineError("invalid_artifact", "снимок не сохранён при приёме запроса")
        if kind == "self-reviews":
            result, _ = run_self_review(request, settings, artifact_bytes=data)  # type: ignore[arg-type]
        else:
            result, _ = run_review_assist(request, settings, artifact_bytes=data)  # type: ignore[arg-type]
        event = _event(row.request, "succeeded", result.model_dump(mode="json"))
    except PipelineError as e:
        log.warning("run %s/%s failed: %s: %s", run_id, attempt, e.code, e)
        event = _event(row.request, "failed", error_code=e.code)
    except Exception as e:  # noqa: BLE001 — техническая ошибка подтверждается событием failed
        log.exception("run %s/%s crashed", run_id, attempt)
        event = _event(row.request, "failed", error_code="unavailable")
    store.append_event(run_id, attempt, event)
    store.set_status(run_id, attempt, event["status"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = _store(settings)
    pending = store.pending()
    if pending:
        log.info("recovering %d pending runs", len(pending))
        loop = asyncio.get_running_loop()
        for row in pending:
            kind = "self-reviews" if row.kind == "self_review" else "review-assists"
            loop.run_in_executor(None, execute, kind, row.run_id, row.attempt, settings)
    yield


app = FastAPI(title="prereview", version=__version__, lifespan=lifespan)


def check_auth(settings: Settings, authorization: str | None) -> None:
    if settings.token is None:
        return
    expected = "Bearer " + settings.token.get_secret_value()
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="bad token")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "version": __version__, "model": s.llm_model, "provider": s.llm_provider,
            "harness": s.harness_enabled}


@app.post("/v2/{kind}/{run_id}")
async def submit(kind: str, run_id: str, request: Request, background: BackgroundTasks,
                 authorization: str | None = Header(default=None),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> Any:
    settings = get_settings()
    check_auth(settings, authorization)
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")
    request_model, event_model, kind_name = KINDS[kind]
    try:
        body = await request.json()
        req = request_model.model_validate(body)
    except (ValueError, ValidationError) as e:
        raise HTTPException(status_code=422, detail=str(e)[:500]) from e
    if str(req.run_id) != run_id:
        raise HTTPException(status_code=409, detail="run_id mismatch")
    store = _store(settings)
    existing = store.get_run(run_id, req.attempt)
    if existing is not None:
        if existing.fingerprint != req.input_fingerprint:
            raise HTTPException(status_code=409, detail="fingerprint mismatch for this run/attempt")
        last = store.last_event(run_id, req.attempt)
        return JSONResponse(last or _event(existing.request, "running"))
    created = store.create_run(run_id, req.attempt, kind_name, req.input_fingerprint, req.model_dump(mode="json"))
    if not created:
        last = store.last_event(run_id, req.attempt)
        return JSONResponse(last or _event(req.model_dump(mode="json"), "running"))
    # Ссылка на снимок живёт 15 минут: скачиваем при приёме, до фоновой работы.
    try:
        data = await asyncio.to_thread(fetch_artifact, req.artifact_url, req.artifact_digest,
                                       max_bytes=settings.max_artifact_bytes, rewrites=settings.url_rewrites)
    except ArtifactError as e:
        event = store.append_event(run_id, req.attempt, _event(req.model_dump(mode="json"), "failed", error_code=e.code))
        store.set_status(run_id, req.attempt, "failed")
        log.warning("artifact for %s/%s rejected: %s", run_id, req.attempt, e)
        return JSONResponse(event)
    path = _artifacts_dir(settings) / f"{run_id}-{req.attempt}-{hashlib.sha256(data).hexdigest()[:12]}.bin"
    path.write_bytes(data)
    store.set_artifact(run_id, req.attempt, str(path))
    event = store.append_event(run_id, req.attempt, _event(req.model_dump(mode="json"), "running"))
    background.add_task(execute, kind, run_id, req.attempt, settings)
    return JSONResponse(event_model.model_validate(event).model_dump(mode="json"))


@app.get("/v2/{kind}/{run_id}")
def lookup(kind: str, run_id: str, attempt: int = Query(...),
           authorization: str | None = Header(default=None)) -> Any:
    settings = get_settings()
    check_auth(settings, authorization)
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")
    store = _store(settings)
    row = store.get_run(run_id, attempt)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown run")
    last = store.last_event(run_id, attempt)
    return JSONResponse(last or _event(row.request, "running"))
