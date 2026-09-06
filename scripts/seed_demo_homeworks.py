"""Заводит на учебном стенде настоящие задания для демо ядра проверки.

Координатор создаёт и публикует в потоке два задания из файлов prereview/assignments:
лабу 1 системного дизайна (сдача файлом) и Go 1–3 (сдача ссылкой на GitHub). Студенты
сдают работы из публичного корпуса, ревьюер берёт их и запускает помощь модели.

Запуск: uv run --directory prereview python ../scripts/seed_demo_homeworks.py
Нужен поднятый стек (RUNNING.md) с fixtures и REVIEW_PLATFORM_LIVE_PROVIDERS_ENABLED=true.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("SEED_API", "http://127.0.0.1:18000")
ORIGIN = os.environ.get("SEED_ORIGIN", "http://localhost:5174")
RUN_TITLE = os.environ.get("SEED_RUN", "Поток 1")
GO_REPO = os.environ.get("SEED_GO_REPO", "https://github.com/VityaPain/course-go-avito-VityaPain/tree/main")
CORPUS = ROOT / "hw_examples"

REVISION_TARGETS = {"create_homework": "course_run", "create_homework_version": "homework",
                    "record_review_responsibility": "review_iteration"}


class Client:
    def __init__(self, identity: str):
        self.http = httpx.Client(base_url=API, timeout=60, headers={"Origin": ORIGIN})
        r = self.http.post("/api/v1/auth/local/login", json={"identity": identity})
        r.raise_for_status()
        self.profile = r.json()
        self.user_id = self.profile["user_id"]
        print(f"  вход: {self.profile['display_name']}")

    def get(self, path: str, **params):
        r = self.http.get(path, params=params or None)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def v2(self, name: str, path: str, target: str, revision: int, payload: dict):
        body = {"request_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4()), "command_name": name,
                "target_id": target, "expected_revision": revision, "payload": payload}
        r = self.http.post(path, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"{name} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    def v1(self, name: str, path: str, target: str, revision: int, payload: dict):
        body = {"request_id": str(uuid.uuid4()), "idempotency_key": str(uuid.uuid4()), "command_name": name,
                "revision_target": REVISION_TARGETS[name], "target_id": target, "expected_revision": revision,
                "payload": payload}
        r = self.http.post(path, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"{name} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}


def load_assignment(slug: str) -> dict:
    return json.loads((ROOT / "prereview" / "assignments" / f"{slug}.json").read_text(encoding="utf-8"))


def criteria_from(assignment: dict) -> list[dict]:
    return [{"key": k, "title": v.get("title") or k, "description": v.get("description", ""),
             "max_points": v.get("max_points", 0)} for k, v in assignment["criteria"].items()]


def private_from(assignment: dict, guidance: str) -> dict:
    return {
        "criterion_settings": {k: {"score_step": 0.5, "evaluate_quality": v.get("check_class") == "judgement"}
                               for k, v in assignment["criteria"].items()},
        "material_upload_ids": [], "reviewer_guidance": guidance, "reference_upload_id": None,
        "criterion_classes": {k: v.get("check_class", "content") for k, v in assignment["criteria"].items()},
    }


def publish_homework(coord: Client, run: dict, title: str, student_text: str, assignment: dict,
                     artifact_kinds: list[str], pass_score: float, guidance: str) -> dict:
    fresh_run = next(r for r in coord.get("/api/v2/catalog")["course_runs"] if r["id"] == run["id"])
    created = coord.v1("create_homework", f"/api/v1/course-runs/{run['id']}/homeworks", run["id"],
                       fresh_run["revision"], {"title": title})
    hw_id = created["id"]
    history = coord.get(f"/api/v1/homeworks/{hw_id}")
    criteria = criteria_from(assignment)
    version = coord.v1("create_homework_version", f"/api/v1/homeworks/{hw_id}/versions", hw_id,
                       history.get("revision", 0),
                       {"student_text": student_text[:100_000], "max_score": sum(c["max_points"] for c in criteria),
                        "artifact_kinds": artifact_kinds, "estimated_review_minutes": 30, "criteria": criteria})
    ver_id = version["id"]
    coord.v2("save_private_homework", f"/api/v2/homework-versions/{ver_id}/private-details", ver_id, 0,
             private_from(assignment, guidance))
    history = coord.get(f"/api/v1/homeworks/{hw_id}")
    ver = next(v for v in history["versions"] if v["id"] == ver_id)
    now = datetime.now(timezone.utc)
    published = coord.v2("publish_workspace_homework", f"/api/v2/homework-versions/{ver_id}/publish", ver_id,
                         ver.get("revision", 0),
                         {"course_run_id": run["id"], "submission_deadline": (now + timedelta(days=7)).isoformat(),
                          "review_deadline": (now + timedelta(days=10)).isoformat(),
                          "policy": {"self_review_limit": 2, "pass_score": pass_score, "revision_days": 7,
                                     "penalty_per_day": 0.5, "max_resubmissions": 3},
                          "expected_policy_revision": 0})
    print(f"  задание «{title}»: {len(criteria)} критериев, публикация {published['publication_id']}")
    return published


def upload(student: Client, path: Path) -> str:
    media = {"md": "text/markdown", "pdf": "application/pdf",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[path.suffix[1:]]
    view = student.v2("upload_artifact", "/api/v2/uploads", student.user_id, 0,
                      {"filename": path.name, "media_type": media,
                       "content_base64": base64.b64encode(path.read_bytes()).decode(), "private": False})
    return view["id"]


def submit(student: Client, publication_id: str, *, upload_id: str | None = None, url: str | None = None,
           comment: str = "") -> None:
    payload = {"comment": comment}
    payload |= {"upload_id": upload_id} if upload_id else {"artifact_url": url}
    draft = student.v2("save_work_draft", f"/api/v2/course-run-homeworks/{publication_id}/draft", publication_id, 0, payload)
    draft_id, rev = draft["id"], draft["revision"]
    if url:
        prep = student.v2("prepare_work_draft", f"/api/v2/work-drafts/{draft_id}/prepare", draft_id, rev, {})
        for _ in range(60):
            view = student.get(f"/api/v2/preparations/{prep['id']}")
            if view["status"] in {"succeeded", "failed"}:
                break
            time.sleep(2)
        print(f"  снимок GitHub: {view['status']} {view.get('filename') or view.get('error_code') or ''}")
        rev = next(d["revision"] for d in student.get("/api/v2/drafts")["items"] if d["id"] == draft_id)
    student.v2("submit_work_draft", f"/api/v2/work-drafts/{draft_id}/submit", draft_id, rev, {})
    print("  сдано")


def take_and_assist(reviewer: Client, titles: set[str]) -> None:
    items = reviewer.get("/api/v2/works", state="pending_review", limit=100)["items"]
    for w in items:
        if w["title"] not in titles or w["review_iteration_id"]:
            continue
        opened = reviewer.v2("open_work", f"/api/v2/submissions/{w['submission_id']}/open-review", w["submission_id"],
                             w["submission_revision"], {"submission_version_id": w["submission_version_id"]})
        iteration = opened["id"]
        review = reviewer.get(f"/api/v1/review-iterations/{iteration}")
        reviewer.v1("record_review_responsibility", f"/api/v1/review-iterations/{iteration}/responsibility-events", iteration,
                    review.get("revision", 0), {"action": "started"})
        draft = reviewer.get(f"/api/v2/reviews/{iteration}/draft")
        reviewer.v2("start_review_assist", f"/api/v2/reviews/{iteration}/assist", iteration, draft["revision"], {})
        print(f"  взята работа «{w['title']}» студента {w['student_name']}: помощь модели запущена ({iteration})")


def main() -> None:
    if os.environ.get("SEED_ONLY") == "reviewer":
        print("Ревьюер")
        take_and_assist(Client("reviewer-1"), {"Системный дизайн: лабораторная 1", "Go: задания 1–3, сервис курьеров"})
        return
    print("Координатор")
    coord = Client("coordinator")
    catalog = coord.get("/api/v2/catalog")
    run = next(r for r in catalog["course_runs"] if RUN_TITLE in r["title"])
    lab = load_assignment("system_design_lab1")
    lab_text = (CORPUS / "system_design" / "лаба 1" / "условие.md").read_text(encoding="utf-8")
    go = load_assignment("go_tasks_1_3")
    go_text = "\n\n".join((CORPUS / "GO" / f"task{i}.md").read_text(encoding="utf-8") for i in (1, 2, 3))
    lab_pub = publish_homework(coord, run, "Системный дизайн: лабораторная 1", lab_text, lab, ["github", "google_docs"], 4,
                               "Проверяйте по таблице критериев из условия. Диаграммы принимаются в mermaid, PlantUML и картинками.")
    go_pub = publish_homework(coord, run, "Go: задания 1–3, сервис курьеров", go_text, go, ["github"], 25,
                              "Строки из шаблона Авито для hw1–hw3. Тесты курса не запускаются: строки с тестами проверяются по коду.")
    print("Студенты")
    alexey = Client("student-1")
    submit(alexey, lab_pub["publication_id"], upload_id=upload(alexey, CORPUS / "system_design" / "лаба 1" / "хорошее.md"),
           comment="Лаба 1, сервис фитнес-клуба.")
    submit(alexey, go_pub["publication_id"], url=GO_REPO, comment="Задания 1–3 в одном репозитории.")
    maria = Client("student-2")
    submit(maria, lab_pub["publication_id"], upload_id=upload(maria, CORPUS / "system_design" / "лаба 1" / "слабое_2.md"),
           comment="Сдаю диаграммы, остальное допишу.")
    print("Ревьюер")
    reviewer = Client("reviewer-1")
    take_and_assist(reviewer, {"Системный дизайн: лабораторная 1", "Go: задания 1–3, сервис курьеров"})
    print("Готово: результаты появятся на страницах ревью через 1–5 минут.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print("ОШИБКА:", e)
        sys.exit(1)
