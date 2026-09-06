"""Песочница: движок сценариев на поддельном сервисе, применение фактов запуска к вердиктам, манифест."""

from __future__ import annotations

import io
import json
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

from prereview.artifact.model import Work, WorkFile
from prereview.checks.runtime import (
    RuntimeReport,
    apply_runtime,
    env_roles,
    find_entrypoint,
    find_migrations,
    make_bundle,
)
from prereview.judge.criterion import CriterionResult
from prereview.rubric.load import load_assignment
from prereview.rubric.model import Criterion

RUNNER_DIR = Path(__file__).resolve().parents[1] / "runner"
sys.path.insert(0, str(RUNNER_DIR))
from engine import Engine  # noqa: E402

ASSIGNMENT = Path(__file__).resolve().parents[1] / "assignments" / "go_tasks_1_3.json"

# Поддельный «сервис курьеров» на Python: порт из флага, окружения или .env, /ping и /healthcheck,
# паника при недоступной базе, сообщение при остановке. GOOD=0 ломает тело /ping и обработку сигнала.
FAKE_APP = """#!{python}
import http.server, json, os, signal, socket, sys
GOOD = {good}
def dotenv():
    out = {{}}
    try:
        for line in open(".env", encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\\n").split("=", 1)
                out[k] = v
    except FileNotFoundError:
        pass
    return out
env = dotenv()
args = sys.argv[1:]
if "--port" in args:
    port = int(args[args.index("--port") + 1])
elif os.environ.get("PORT"):
    port = int(os.environ["PORT"])
elif env.get("PORT"):
    port = int(env["PORT"])
else:
    port = 8080
host = os.environ.get("POSTGRES_HOST") or env.get("POSTGRES_HOST")
if host:
    dbport = int(os.environ.get("POSTGRES_PORT") or env.get("POSTGRES_PORT") or 5432)
    try:
        socket.create_connection((host, dbport), timeout=1).close()
    except OSError as e:
        print("panic: db unreachable:", e, flush=True)
        sys.exit(2)
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path == "/ping":
            body = json.dumps({{"message": "pong" if GOOD else "ok"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def do_HEAD(self):
        self.send_response(204 if self.path == "/healthcheck" else 404)
        self.end_headers()
srv = http.server.HTTPServer(("127.0.0.1", port), H)
def stop(*_):
    print("Shutting down service-courier", flush=True)
    sys.exit(0)
if GOOD:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
print("listening on", port, flush=True)
srv.serve_forever()
"""


def bundle_with_fake_app(good: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("app")
        info.external_attr = 0o100755 << 16
        z.writestr(info, FAKE_APP.format(python=sys.executable, good=1 if good else 0))
        z.writestr("manifest.json", json.dumps({"entrypoint": "cmd/app/main.go", "migrations": [], "env_roles": {}}))
    return buf.getvalue()


@pytest.fixture(scope="module")
def spec() -> dict:
    return load_assignment(ASSIGNMENT).runtime.model_dump()


def test_engine_confirms_behaviour_of_good_service(spec, tmp_path):
    result = Engine(work_root=str(tmp_path)).run(bundle_with_fake_app(True), spec, timeout=120)
    assert result.ok, result.error
    o = result.outcomes
    assert o["env_port"]["status"] == "pass" and o["env_port.port"]["status"] == "pass"
    assert o["env_port.ping"]["status"] == "pass" and "как в ТЗ" in o["env_port.ping"]["detail"]
    assert o["env_port.healthcheck"]["status"] == "pass"
    assert o["env_port.stop"]["status"] == "pass"
    assert o["env_port.log"]["status"] == "pass"
    assert o["dotenv_only"]["status"] == "pass"
    assert o["flag_port.port"]["status"] == "pass" and "закрыт" in o["flag_port.port"]["detail"]
    assert o["db_down"]["status"] == "pass" and "паника" in o["db_down"]["detail"]
    # Без тестовой базы миграции не проверяются, а сервис всё равно запускается:
    # поддельный сервис стартует и отвечает 404 на CRUD, это честный провал проб.
    assert o["migrate.first"]["status"] == "na"
    assert "без базы" in o["crud"]["detail"]
    assert o["crud.post_create"]["status"] == "fail" and "НЕ по ТЗ" in o["crud.post_create"]["detail"]


def test_engine_catches_wrong_body_and_missing_shutdown_log(spec, tmp_path):
    result = Engine(work_root=str(tmp_path)).run(bundle_with_fake_app(False), spec, timeout=120)
    o = result.outcomes
    assert o["env_port.ping"]["status"] == "fail" and "НЕ по ТЗ" in o["env_port.ping"]["detail"]
    assert o["env_port.healthcheck"]["status"] == "pass"
    assert o["env_port.log"]["status"] == "fail"
    assert o["env_port.stop"]["status"] == "pass"  # сигнал по умолчанию убивает процесс: остановился


def test_engine_rejects_broken_bundle(tmp_path):
    result = Engine(work_root=str(tmp_path)).run(b"not a zip", {"scenarios": []})
    assert not result.ok and "bundle" in result.error


def _criterion(key: str, refs: list[str], max_points: float = 1.0) -> Criterion:
    return Criterion(id=uuid.uuid4(), key=key, title=key, max_points=max_points, score_step=0.5, runtime=refs)


def _report(outcomes: dict[str, tuple[str, str]]) -> RuntimeReport:
    r = RuntimeReport(available=True, ran=True, entrypoint="cmd/app/main.go", goos="linux", goarch="arm64")
    r.outcomes = {k: {"status": s, "detail": d} for k, (s, d) in outcomes.items()}
    return r


def test_apply_runtime_overrides_model_in_both_directions():
    report = _report({"env_port.ping": ("pass", "GET /ping → 200: как в ТЗ"),
                      "crud.get_bad_id": ("fail", "GET /courier/abc → 500: НЕ по ТЗ"),
                      "crud.get_list": ("pass", "GET /couriers → 200: как в ТЗ"),
                      "migrate.first": ("na", "тестовая база недоступна")})
    # Модель провалила, запуск подтвердил: полный балл, уверенность высокая, заметка ревьюеру.
    r = CriterionResult(_criterion("ping", ["env_port.ping"]), "suggested", 0.0, "medium", "нет /ping", False,
                        verdict="fail", repeats=[{"verdict": "fail"}])
    r = apply_runtime(r, report)
    assert (r.status, r.proposed_points, r.confidence, r.requirement_met) == ("suggested", 1.0, "high", True)
    assert "runtime_overrides_model" in r.flags and r.path == "runtime"
    assert r.evidence_lines[0].startswith("запуск: GET /ping")
    # Модель поставила максимум, запуск показал частичное выполнение: балл режется.
    r = CriterionResult(_criterion("crud_get", ["crud.get_list", "crud.get_bad_id"], 2.0), "suggested", 2.0, "high",
                        "оба GET есть", True, verdict="pass", repeats=[{"verdict": "pass"}])
    r = apply_runtime(r, report)
    assert r.verdict == "partial" and r.proposed_points == 1.0 and "НЕ по ТЗ" in r.student_feedback
    # Шаг балла не позволяет выразить часть: решает ревьюер.
    r = CriterionResult(_criterion("crud_get_small", ["crud.get_list", "crud.get_bad_id"], 0.5), "suggested", 0.5,
                        "high", "есть", True, verdict="pass")
    assert apply_runtime(r, report).status == "needs_human"
    # Поведение не проверено: вердикт модели остаётся, ревьюер видит причину.
    r = CriterionResult(_criterion("migrations_clean", ["migrate.first"]), "suggested", 1.0, "low", "по коду ок", True,
                        verdict="pass")
    r = apply_runtime(r, report)
    assert r.proposed_points == 1.0 and "runtime_na" in r.flags and "не подтвердил" in r.reviewer_note


def test_manifest_helpers_find_entrypoint_migrations_and_env_names():
    work = Work("zip", "application/zip", files=[
        WorkFile("go.mod", "module x\n"),
        WorkFile("cmd/tools/main.go", "package main\n\nfunc main() {}\n"),
        WorkFile("cmd/service-courier/main.go", "package main\n\nfunc main() {}\n"),
        WorkFile("internal/config/config.go", 'package config\nvar a = os.Getenv("PORT")\nvar b = os.Getenv("POSTGRES_HOST")\n'
                                              'var c = os.Getenv("DB_PASS")\nvar d = os.Getenv("PG_DATABASE")\nvar e = os.Getenv("LOG_LEVEL")\n'),
        WorkFile("migrations/20250101_init.sql", "-- +goose Up\nCREATE TABLE couriers();\n-- +goose Down\n"),
        WorkFile("db/schema/0001_x.up.sql", "CREATE TABLE a();\n"),
    ])
    assert find_entrypoint(work) == "cmd/service-courier/main.go"
    mig = find_migrations(work)
    assert mig[0] == {"dir": "migrations", "tool": "goose", "files": 1}
    assert {"dir": "db/schema", "tool": "migrate", "files": 1} in mig
    roles = env_roles(work)
    assert roles == {"PORT": "http_port", "POSTGRES_HOST": "host", "DB_PASS": "password", "PG_DATABASE": "dbname"}
    data = make_bundle(b"\x7fELF", work, "cmd/service-courier/main.go", mig, roles)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert {"app", "manifest.json", "migrations/20250101_init.sql", "db/schema/0001_x.up.sql"} <= names
        assert z.getinfo("app").external_attr >> 16 & 0o111


def test_assignment_rejects_unknown_runtime_refs(tmp_path):
    data = json.loads(ASSIGNMENT.read_text("utf-8"))
    data["criteria"]["ping"]["runtime"] = ["env_port.nope"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="env_port.nope"):
        load_assignment(bad)


def test_runner_health_reports_platform():
    from fastapi.testclient import TestClient

    import app as runner_app

    client = TestClient(runner_app.app)
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["goos"] in {"linux", "darwin"} and body["goarch"] in {"arm64", "amd64"}
    assert client.post("/run", json={"bundle_b64": "***", "spec": {}}).status_code == 400
