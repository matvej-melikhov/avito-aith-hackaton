"""Повторный прогон Go-пар отчёта через сервис prereview с включённой песочницей.

Песочница влияет только на Go-репозитории, поэтому перепрогоняются 9 пар (3 репозитория × 3 задания)
с теми же критериями и порогами, что в evaluation.json. Остальные 57 пар берутся из исходного отчёта.
Результат: ai-reviews-sandbox/<run_id>.json, evaluation-sandbox.json, telemetry-sandbox.json.
Дальше: python3 recalculate.py --variant sandbox

Запуск из корня репозитория при поднятом стеке (сервис ai на PREREVIEW_URL, архивы репозиториев
раздаются по ARTIFACT_BASE, доступному из контейнера, например http://host.containers.internal:18555):
    PREREVIEW_URL=http://127.0.0.1:18100 ARTIFACT_BASE=http://host.containers.internal:18555 \
    ARTIFACTS_DIR=/path/to/zips python3 docs/reports/homework-assessment-2026-09-06/rerun_go_sandbox.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
CORPUS = REPO / "hw_examples"
PREREVIEW_URL = os.environ.get("PREREVIEW_URL", "http://127.0.0.1:18100").rstrip("/")
ARTIFACT_BASE = os.environ.get("ARTIFACT_BASE", "http://host.containers.internal:18555").rstrip("/")
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "."))
AI_CONTAINER = os.environ.get("AI_CONTAINER", "workspace-completion-local-ai-1")
POLL_TIMEOUT = int(os.environ.get("POLL_TIMEOUT", "1200"))


def sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def run_record(run_id: str) -> dict | None:
    """Журнал прогона из контейнера сервиса: телеметрия в том же виде, что в исходном отчёте."""
    try:
        out = subprocess.run(["docker", "exec", AI_CONTAINER, "cat", f"/data/runs/{run_id}-1.json"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def one_case(case: dict, task: dict, unit_zip: Path, log: list[str]) -> tuple[dict, dict | None]:
    task_no = case["task_id"].split("_")[-1]
    student_text = (CORPUS / "GO" / f"task{task_no}.md").read_text(encoding="utf-8")
    run_id = str(uuid.uuid4())
    digest = sha256_file(unit_zip)
    fingerprint = "sha256:" + hashlib.sha256(f"{digest}|{case['task_id']}|{run_id}".encode()).hexdigest()
    criteria = [{"id": c["id"], "key": c["key"], "title": c["title"], "description": c.get("description", ""),
                 "max_points": c["max_points"], "score_step": c.get("score_step", 0.5),
                 "evaluate_quality": bool(c.get("evaluate_quality", False)), "position": i + 1}
                for i, c in enumerate(case["criteria"])]
    request = {"contract_version": "2.0.0", "purpose": "reviewer_assist", "run_id": run_id, "attempt": 1,
               "input_fingerprint": fingerprint, "review_iteration_id": str(uuid.uuid4()),
               "artifact_id": str(uuid.uuid4()), "artifact_url": f"{ARTIFACT_BASE}/{unit_zip.name}",
               "artifact_digest": digest, "media_type": "application/zip", "student_text": student_text,
               "criteria": criteria, "reviewer_guidance": ""}
    started = time.time()
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{PREREVIEW_URL}/v2/review-assists/{run_id}", json=request,
                        headers={"Idempotency-Key": f"{run_id}:1"})
        if r.status_code != 200:
            raise RuntimeError(f"{case['pair_id']}: POST {r.status_code} {r.text[:300]}")
        event = r.json()
        while event.get("status") == "running" or r.status_code == 204:
            if time.time() - started > POLL_TIMEOUT:
                raise RuntimeError(f"{case['pair_id']}: не дождались результата за {POLL_TIMEOUT} с")
            time.sleep(10)
            r = client.get(f"{PREREVIEW_URL}/v2/review-assists/{run_id}", params={"attempt": 1})
            if r.status_code == 200:
                event = r.json()
            elif r.status_code != 204:
                raise RuntimeError(f"{case['pair_id']}: GET {r.status_code} {r.text[:300]}")
    if event.get("status") != "succeeded":
        raise RuntimeError(f"{case['pair_id']}: {event.get('status')} {event.get('error_code')} {event.get('error_message')}")
    review = {"id": run_id, "status": "succeeded", "revision": 0, "result": event["result"],
              "error_code": None, "created_at": datetime.now(timezone.utc).isoformat()}
    out_dir = ROOT / "ai-reviews-sandbox"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{run_id}.json"
    data = json.dumps(review, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(data)
    new_case = dict(case)
    new_case.update({"run_id": run_id, "attempt": 1, "input_fingerprint": fingerprint,
                     "review_iteration_id": request["review_iteration_id"], "artifact_sha256": digest,
                     "artifact_format": "zip_neutral", "review_file": f"ai-reviews-sandbox/{run_id}.json",
                     "review_sha256": hashlib.sha256(data).hexdigest(), "rerun": "sandbox"})
    record = run_record(run_id)
    telemetry = None
    if record:
        keys = ("run_id", "attempt", "model", "pipeline_version", "prompt_versions", "started_at", "finished_at",
                "elapsed_seconds", "ledger")
        telemetry = {k: record.get(k) for k in keys}
        telemetry["runtime"] = {k: record.get("runtime", {}).get(k) for k in ("ran", "elapsed", "entrypoint")}
        outs = record.get("runtime", {}).get("outcomes", {})
        telemetry["runtime"]["outcomes"] = {s: sum(1 for o in outs.values() if o["status"] == s)
                                            for s in ("pass", "fail", "na")}
    total = sum((s.get("proposed_points") or 0) for s in event["result"]["suggestions"])
    log.append(f"{case['pair_id']}: {total:g} из {sum(c['max_points'] for c in criteria):g}, "
               f"{time.time() - started:.0f} с, run {run_id}")
    print(log[-1], flush=True)
    return new_case, telemetry


def main() -> None:
    evaluation = json.loads((ROOT / "evaluation.json").read_text(encoding="utf-8"))
    telemetry = json.loads((ROOT / "telemetry.json").read_text(encoding="utf-8"))
    go_cases = [c for c in evaluation["cases"] if c["task_id"].startswith("go_")]
    units = sorted({c["unit_id"] for c in go_cases})
    results: dict[str, tuple[dict, dict | None]] = {}
    errors: list[str] = []
    log: list[str] = []

    def worker(unit: str) -> None:
        unit_zip = ARTIFACTS_DIR / f"{unit}.zip"
        for case in sorted((c for c in go_cases if c["unit_id"] == unit), key=lambda c: c["task_id"]):
            try:
                results[case["pair_id"]] = one_case(case, evaluation["tasks"][case["task_id"]], unit_zip, log)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                print("ОШИБКА", e, flush=True)

    threads = [threading.Thread(target=worker, args=(u,)) for u in units]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        print("Не все пары прошли:", errors)
        sys.exit(1)
    new_eval = dict(evaluation)
    new_eval["cases"] = [results[c["pair_id"]][0] if c["pair_id"] in results else c for c in evaluation["cases"]]
    new_eval["variant"] = {"name": "sandbox", "note": "Go-пары перепрогнаны с песочницей запуска, остальные из исходного отчёта",
                           "rerun_at": datetime.now(timezone.utc).isoformat()}
    (ROOT / "evaluation-sandbox.json").write_text(json.dumps(new_eval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    replaced = {c["run_id"] for c in go_cases}
    new_runs = [r for r in telemetry["runs"] if r["run_id"] not in replaced]
    new_runs += [t for _, t in results.values() if t]
    (ROOT / "telemetry-sandbox.json").write_text(json.dumps({**telemetry, "runs": new_runs}, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    print("Готово:", len(results), "пар перепрогнано")


if __name__ == "__main__":
    main()
