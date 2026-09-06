"""Факты запуска: сервис собирает бинарник из снимка, песочница гоняет его по сценариям задания.

Сторона сервиса: найти точку входа и миграции, узнать имена переменных окружения студента,
собрать бинарник под платформу песочницы (сборка не исполняет код), отправить бандл в песочницу
и превратить ответ в факты для судьи и в вердикты по строкам рубрики.

Правило: факт запуска сильнее чтения кода. Проба «как в ТЗ» подтверждает строку, проба
«НЕ по ТЗ» опровергает, а «не удалось проверить» (сервис не стартовал с нашими переменными,
нет базы, нет миграций) ничего не меняет и только помечается ревьюеру.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from prereview.artifact.model import Work
from prereview.checks.gobuild import _materialize, go_env
from prereview.config import Settings
from prereview.judge.criterion import CriterionResult
from prereview.rubric.model import RuntimeSpec

ENTRY_PREFER = ("service", "server", "app", "api", "main", "courier")
GETENV = re.compile(r'"([A-Z][A-Z0-9_]{2,})"')
YAML_KEY = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9_]{2,})\s*[:=]", re.M)
DB_WORDS = re.compile(r"DB|PG|POSTGRES|DATABASE|DSN")


@dataclass
class RuntimeReport:
    available: bool
    ran: bool = False
    note: str = ""
    entrypoint: str = ""
    goos: str = ""
    goarch: str = ""
    compile_ok: bool | None = None
    compile_output: str = ""
    migrations: list[dict] = field(default_factory=list)
    env_roles: dict[str, str] = field(default_factory=dict)
    scenarios: list[dict] = field(default_factory=list)
    outcomes: dict[str, dict] = field(default_factory=dict)
    elapsed: float = 0.0
    error: str = ""

    # ------------------------------------------------------------- для промпта
    def facts(self) -> str:
        if not self.available:
            return ""
        if not self.ran:
            return f"ФАКТЫ ЗАПУСКА: запуск в песочнице не выполнен ({self.note or self.error})."
        lines = [f"- Точка входа {self.entrypoint}, сборка для {self.goos}/{self.goarch}: успешно."]
        for s in self.scenarios:
            parts = [f"Сценарий «{s.get('title') or s.get('id')}»: {s.get('detail') or 'без результата'}."]
            for key in ("first", "second"):
                c = (s.get("checks") or {}).get(key)
                if c:
                    parts.append(c.get("detail", ""))
            for p in s.get("probes") or []:
                parts.append(p.get("detail", ""))
            for key in ("port", "stdout", "stop", "log", "log_exact"):
                c = (s.get("checks") or {}).get(key)
                if c:
                    parts.append(c.get("detail", ""))
            lines.append("- " + " ".join(x for x in parts if x))
        return ("ФАКТЫ ЗАПУСКА (бинарник собран из снимка и запущен кодом в песочнице; факт запуска сильнее "
                "чтения кода: «как в ТЗ» подтверждает требование, «НЕ по ТЗ» опровергает, «не стартовал» "
                "значит поведение не проверено):\n" + "\n".join(lines))

    def summary(self) -> str:
        if not self.ran:
            return ""
        total = sum(1 for o in self.outcomes.values() if o["status"] != "na")
        passed = sum(1 for o in self.outcomes.values() if o["status"] == "pass")
        na = sum(1 for o in self.outcomes.values() if o["status"] == "na")
        text = f"Запуск в песочнице: {passed} из {total} проверок поведения как в ТЗ"
        if na:
            text += f", {na} не удалось проверить"
        return text + ". "

    def outcome_for(self, refs: list[str]) -> tuple[str, int, int, list[str]]:
        """Сводный исход по ссылкам критерия: pass | partial | fail | na, сколько прошло, детали."""
        details: list[str] = []
        statuses: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            o = self.outcomes.get(ref)
            if o is None:
                statuses.append("na")
                details.append(f"{ref}: сценарий не выполнялся")
                continue
            statuses.append(o["status"])
            if o["detail"] not in seen:
                seen.add(o["detail"])
                details.append(o["detail"])
        if not statuses or "na" in statuses:
            return "na", 0, len(statuses), details
        passed = statuses.count("pass")
        if passed == len(statuses):
            return "pass", passed, len(statuses), details
        if passed == 0:
            return "fail", 0, len(statuses), details
        return "partial", passed, len(statuses), details

    def to_dict(self) -> dict:
        return {"available": self.available, "ran": self.ran, "note": self.note, "entrypoint": self.entrypoint,
                "goos": self.goos, "goarch": self.goarch, "compile_ok": self.compile_ok,
                "compile_output": self.compile_output[-1500:], "migrations": self.migrations,
                "env_roles": self.env_roles, "scenarios": self.scenarios, "outcomes": self.outcomes,
                "elapsed": round(self.elapsed, 1), "error": self.error}


# ------------------------------------------------------------------ манифест снимка
def find_entrypoint(work: Work) -> str | None:
    mains = [f for f in work.files if f.path.endswith("main.go") and "package main" in f.text and "func main(" in f.text]
    if not mains:
        return None
    under_cmd = [f for f in mains if re.fullmatch(r"cmd/[^/]+/main\.go", f.path)]
    if under_cmd:
        under_cmd.sort(key=lambda f: (0 if any(w in f.path.lower() for w in ENTRY_PREFER) else 1, f.path))
        return under_cmd[0].path
    for candidate in ("cmd/main.go", "main.go"):
        if any(f.path == candidate for f in mains):
            return candidate
    mains.sort(key=lambda f: (f.path.count("/"), f.path))
    return mains[0].path


def find_migrations(work: Work) -> list[dict]:
    goose_dirs: dict[str, int] = {}
    migrate_dirs: dict[str, int] = {}
    for f in work.files:
        if not f.path.endswith(".sql"):
            continue
        directory = f.path.rsplit("/", 1)[0] if "/" in f.path else "."
        if "+goose" in f.text:
            goose_dirs[directory] = goose_dirs.get(directory, 0) + 1
        elif f.path.endswith(".up.sql"):
            migrate_dirs[directory] = migrate_dirs.get(directory, 0) + 1
    out = [{"dir": d, "tool": "goose", "files": n} for d, n in sorted(goose_dirs.items(), key=lambda x: -x[1])]
    out += [{"dir": d, "tool": "migrate", "files": n} for d, n in sorted(migrate_dirs.items(), key=lambda x: -x[1])]
    return out


def env_roles(work: Work) -> dict[str, str]:
    """Имена переменных окружения студента и их роль: host, port, user, password, dbname, dsn, http_port."""
    names: set[str] = set()
    for f in work.files:
        if f.path.endswith(".go"):
            names.update(GETENV.findall(f.text))
        elif f.path.endswith((".yml", ".yaml", ".env.example", ".env.sample", ".env.dist", "Makefile")):
            names.update(YAML_KEY.findall(f.text))
    roles: dict[str, str] = {}
    for n in sorted(names):
        if n in {"PORT", "HTTP_PORT", "SERVER_PORT", "APP_PORT", "SERVICE_PORT", "LISTEN_PORT", "HTTP_ADDR"}:
            roles[n] = "http_port"
            continue
        if not DB_WORDS.search(n):
            continue
        if "SSL" in n:
            roles[n] = "sslmode"
        elif "HOST" in n:
            roles[n] = "host"
        elif "PORT" in n:
            roles[n] = "port"
        elif "PASS" in n:
            roles[n] = "password"
        elif "USER" in n:
            roles[n] = "user"
        elif re.search(r"DSN|URL|URI|CONN", n):
            roles[n] = "dsn"
        elif re.search(r"NAME|DB$|DATABASE|_DB_", n) or n in {"POSTGRES_DB", "PGDATABASE"}:
            roles[n] = "dbname"
    return roles


# ------------------------------------------------------------------ сборка и бандл
def compile_binary(work: Work, entrypoint: str, goos: str, goarch: str, settings: Settings,
                   timeout: int) -> tuple[bool, str, bytes | None]:
    import shutil

    go = shutil.which("go")
    if not go:
        return False, "инструмент go недоступен", None
    with tempfile.TemporaryDirectory(prefix="prereview-rt-") as tmp:
        root = Path(tmp) / "src"
        root.mkdir()
        _materialize(work, root)
        env = go_env(settings, tmp) | {"GOOS": goos, "GOARCH": goarch}
        pkg = "./" + entrypoint.rsplit("/", 1)[0] if "/" in entrypoint else "."
        out = Path(tmp) / "app"
        try:
            r = subprocess.run([go, "build", "-trimpath", "-o", str(out), pkg], cwd=root, env=env,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"сборка бинарника не уложилась в {timeout} с", None
        if r.returncode != 0:
            return False, (r.stderr or r.stdout)[-2000:], None
        return True, "", out.read_bytes()


def make_bundle(binary: bytes, work: Work, entrypoint: str, migrations: list[dict], roles: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo("app")
        info.external_attr = 0o100755 << 16
        z.writestr(info, binary)
        z.writestr("manifest.json", json.dumps({"entrypoint": entrypoint, "migrations": migrations,
                                                "env_roles": roles}, ensure_ascii=False))
        for m in migrations:
            prefix = "" if m["dir"] == "." else m["dir"] + "/"
            for f in work.files:
                if f.path.startswith(prefix) and f.path.endswith(".sql") and (prefix or "/" not in f.path):
                    z.writestr(f.path, f.text)
    return buf.getvalue()


# ------------------------------------------------------------------ прогон
def run_runtime(work: Work, spec: RuntimeSpec, settings: Settings) -> RuntimeReport:
    started = time.time()
    url = (settings.runner_url or "").rstrip("/")
    if not settings.runtime_enabled or not url:
        return RuntimeReport(available=False)
    report = RuntimeReport(available=True)
    entry = find_entrypoint(work)
    if not entry:
        report.note = "в снимке нет package main с func main"
        report.elapsed = time.time() - started
        return report
    report.entrypoint = entry
    report.migrations = find_migrations(work)
    report.env_roles = env_roles(work)
    try:
        with httpx.Client(timeout=10) as client:
            health = client.get(url + "/health").json()
    except (httpx.HTTPError, ValueError) as e:
        report.error = f"песочница недоступна: {type(e).__name__}"
        report.elapsed = time.time() - started
        return report
    report.goos, report.goarch = health.get("goos", "linux"), health.get("goarch", "amd64")
    ok, output, binary = compile_binary(work, entry, report.goos, report.goarch, settings,
                                        settings.go_build_timeout_seconds)
    report.compile_ok, report.compile_output = ok, output
    if not ok or binary is None:
        report.note = "бинарник не собрался: " + output.strip().splitlines()[-1][:200] if output.strip() else "бинарник не собрался"
        report.elapsed = time.time() - started
        return report
    bundle = make_bundle(binary, work, entry, report.migrations, report.env_roles)
    payload = {"bundle_b64": base64.b64encode(bundle).decode(), "spec": spec.model_dump(),
               "timeout": settings.runtime_timeout_seconds}
    try:
        with httpx.Client(timeout=settings.runtime_timeout_seconds + 30) as client:
            resp = client.post(url + "/run", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        report.error = f"песочница не ответила: {type(e).__name__}: {str(e)[:200]}"
        report.elapsed = time.time() - started
        return report
    if not data.get("ok", False):
        report.error = "песочница: " + str(data.get("error") or "неизвестная ошибка")
        report.elapsed = time.time() - started
        return report
    report.ran = True
    report.scenarios = data.get("scenarios", [])
    report.outcomes = data.get("outcomes", {})
    report.elapsed = time.time() - started
    return report


# ------------------------------------------------------------------ применение к вердиктам
def apply_runtime(r: CriterionResult, report: RuntimeReport) -> CriterionResult:
    c = r.criterion
    if not c.runtime or not report.ran:
        return r
    status, passed, total, details = report.outcome_for(c.runtime)
    r.runtime_lines = [f"запуск: {d}" for d in details][:12]
    if status == "na":
        r.flags.append("runtime_na")
        r.reviewer_note = (r.reviewer_note + " Запуск в песочнице поведение не подтвердил: " + "; ".join(details)[:600]).strip()
        return r
    model_said = None
    if r.path == "model" and r.verdict:
        model_said = f"По коду модель считала «{r.verdict}»: {r.reason}"
    notes = [n for n in [r.reviewer_note] if n]
    full = c.snap(c.points_if_pass if c.points_if_pass is not None else c.max_points)
    if status == "pass":
        r.status, r.proposed_points, r.confidence, r.requirement_met, r.verdict = "suggested", full, "high", True, "pass"
        r.reason = "Подтверждено запуском: " + "; ".join(details)
        r.student_feedback = ""
        if model_said and r.repeats and all(j.get("verdict") not in {"pass"} for j in r.repeats):
            notes.append(model_said + " Запуск показал, что требование выполнено.")
            r.flags.append("runtime_overrides_model")
    elif status == "fail":
        r.status, r.proposed_points, r.confidence, r.requirement_met, r.verdict = "suggested", 0.0, "high", False, "fail"
        r.reason = "Проверено запуском: " + "; ".join(details)
        r.student_feedback = "Исправь поведение сервиса: " + "; ".join(d for d in details if "НЕ по ТЗ" in d or "нет" in d)[:300]
        if model_said and r.repeats and any(j.get("verdict") == "pass" for j in r.repeats):
            notes.append(model_said + " Запуск показал иное.")
            r.flags.append("runtime_overrides_model")
    else:
        points = c.snap(c.max_points * passed / total)
        if c.max_points <= c.score_step:
            r.status, r.proposed_points, r.confidence, r.requirement_met, r.verdict = "needs_human", None, "high", None, "partial"
            notes.append("Часть проб прошла, а шаг балла не позволяет поставить часть: решите сами.")
        else:
            if points is not None and points >= c.max_points:
                points = c.snap(c.max_points - c.score_step)
            if not points:
                points = c.snap(min(c.score_step, c.max_points))
            r.status, r.proposed_points, r.confidence, r.requirement_met, r.verdict = "suggested", points, "high", True, "partial"
        r.reason = f"Проверено запуском: как в ТЗ {passed} из {total}: " + "; ".join(details)
        r.student_feedback = "Исправь поведение сервиса: " + "; ".join(d for d in details if "НЕ по ТЗ" in d)[:300]
        if model_said and r.repeats and any(j.get("verdict") == "pass" for j in r.repeats):
            notes.append(model_said + " Запуск показал, что выполнено частично.")
            r.flags.append("runtime_overrides_model")
    r.reviewer_note = " ".join(notes).strip()
    r.path = "runtime"
    r.flags.append("runtime")
    return r
