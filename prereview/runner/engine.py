"""Движок песочницы: запускает собранный бинарник студента по сценариям и записывает факты.

Здесь нет модели и нет секретов сервиса. Движок исполняет чужой код, поэтому в стенде он
живёт в отдельном контейнере: сеть только до тестовой базы, файловая система read-only,
tmpfs для работы, без capabilities, лимиты процессов и памяти (deploy/compose.ai.yaml).
Файл намеренно без зависимостей от пакета prereview: образ песочницы содержит только его.

Сценарии приходят из файла задания. Виды:
- service: запустить бинарник с окружением и аргументами, дождаться порта, прогнать HTTP-пробы,
  остановить сигналом и проверить лог;
- migrations: накатить миграции студента на чистую базу дважды (чистый запуск и повтор).

Каждый сценарий даёт исходы по ссылкам вида «сценарий», «сценарий.проба», «сценарий.stop»,
«сценарий.log», «migrate.first», «migrate.second» со статусом pass | fail | na.
na означает «поведение не удалось проверить» (сервис не стартовал с нашими переменными,
нет миграций, нет базы): это не провал студента, а сигнал ревьюеру посмотреть самому.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from http.client import HTTPConnection, HTTPException
from pathlib import Path

LOG_LIMIT = 64_000
BODY_LIMIT = 64_000
DEFAULT_START_TIMEOUT = 15
DEFAULT_STOP_TIMEOUT = 10
DEFAULT_PROBE_TIMEOUT = 5

# Имена переменных окружения, под которыми студенты обычно ждут настройки базы.
DB_ENV_ALIASES: dict[str, list[str]] = {
    "host": ["POSTGRES_HOST", "DB_HOST", "PG_HOST", "PGHOST", "DATABASE_HOST", "POSTGRES_HOSTNAME"],
    "port": ["POSTGRES_PORT", "DB_PORT", "PG_PORT", "PGPORT", "DATABASE_PORT"],
    "user": ["POSTGRES_USER", "DB_USER", "PG_USER", "PGUSER", "DATABASE_USER", "DB_USERNAME", "POSTGRES_USERNAME"],
    "password": ["POSTGRES_PASSWORD", "DB_PASSWORD", "PG_PASSWORD", "PGPASSWORD", "DATABASE_PASSWORD", "DB_PASS",
                 "POSTGRES_PASS"],
    "dbname": ["POSTGRES_DB", "DB_NAME", "PG_DB", "PGDATABASE", "DATABASE_NAME", "POSTGRES_DBNAME", "DB_DATABASE",
               "POSTGRES_NAME", "PG_DATABASE", "POSTGRES_DATABASE"],
    "dsn": ["DATABASE_URL", "DB_DSN", "DSN", "POSTGRES_DSN", "DB_URL", "POSTGRES_URL", "PG_DSN", "PG_URL",
            "DATABASE_DSN", "POSTGRES_CONN"],
    "sslmode": ["POSTGRES_SSLMODE", "DB_SSLMODE", "PGSSLMODE", "DB_SSL_MODE", "POSTGRES_SSL_MODE"],
}
HTTP_PORT_ALIASES = ["PORT", "HTTP_PORT", "SERVER_PORT", "APP_PORT", "SERVICE_PORT", "LISTEN_PORT"]
# Порты, на которых сервис часто стартует, игнорируя настройку: так «сервер запускается» и «порт из .env»
# проверяются раздельно.
FALLBACK_PORTS = [8080, 8000, 3000, 8081, 9000, 5000, 8888]


def go_platform() -> tuple[str, str]:
    """GOOS и GOARCH этой машины: под них сервис собирает бинарник студента."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return system, arch


@dataclass
class ProbeResult:
    id: str
    method: str
    path: str
    ok: bool | None
    status: int | None
    detail: str
    elapsed_ms: int = 0


@dataclass
class ScenarioResult:
    id: str
    kind: str
    title: str
    started: bool | None = None
    ok: bool | None = None
    detail: str = ""
    exit_code: int | None = None
    probes: list[ProbeResult] = field(default_factory=list)
    checks: dict[str, dict] = field(default_factory=dict)  # stop | log | first | second → {ok, detail}
    log_tail: str = ""
    elapsed: float = 0.0


@dataclass
class RunResult:
    ok: bool
    goos: str
    goarch: str
    scenarios: list[ScenarioResult] = field(default_factory=list)
    outcomes: dict[str, dict] = field(default_factory=dict)  # ref → {status: pass|fail|na, detail}
    elapsed: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class _LogReader:
    """Читает stdout и stderr процесса в фоне. Общий лог нужен для показа, раздельный для
    строки «лог старта уходит в stdout» (пакет log в Go по умолчанию пишет в stderr)."""

    def __init__(self, stdout, stderr=None) -> None:
        self.chunks: list[tuple[str, str]] = []
        self.size = 0
        self.lock = threading.Lock()
        self.threads = [threading.Thread(target=self._pump, args=(stdout, "out"), daemon=True)]
        if stderr is not None:
            self.threads.append(threading.Thread(target=self._pump, args=(stderr, "err"), daemon=True))
        for t in self.threads:
            t.start()

    def _pump(self, stream, kind: str) -> None:
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace")
                with self.lock:
                    if self.size < LOG_LIMIT:
                        self.chunks.append((kind, line))
                        self.size += len(line)
        except Exception:  # noqa: BLE001 — поток закрыт вместе с процессом
            pass

    def text(self) -> str:
        with self.lock:
            return "".join(line for _, line in self.chunks)

    def stdout_text(self) -> str:
        with self.lock:
            return "".join(line for kind, line in self.chunks if kind == "out")

    def stderr_text(self) -> str:
        with self.lock:
            return "".join(line for kind, line in self.chunks if kind == "err")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _subst(value, variables: dict[str, object]):
    """${PORT_A} и {courier_id} в строках; строка, целиком равная шаблону, берёт тип значения."""
    if isinstance(value, str):
        m = re.fullmatch(r"\$\{(\w+)\}|\{(\w+)\}", value)
        if m:
            key = m.group(1) or m.group(2)
            if key in variables:
                return variables[key]
        out = value
        for k, v in variables.items():
            out = out.replace("${" + k + "}", str(v)).replace("{" + k + "}", str(v))
        return out
    if isinstance(value, dict):
        return {k: _subst(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, variables) for v in value]
    return value


class Postgres:
    """Тестовая база: отдельная роль и база на каждый сценарий, после сценария всё удаляется."""

    def __init__(self, admin_dsn: str | None):
        self.admin_dsn = admin_dsn
        self.created: list[str] = []

    @property
    def available(self) -> bool:
        return bool(self.admin_dsn)

    def _params(self) -> dict[str, str]:
        from urllib.parse import urlsplit

        u = urlsplit(self.admin_dsn or "")
        return {"host": u.hostname or "127.0.0.1", "port": str(u.port or 5432), "user": u.username or "postgres",
                "password": u.password or "", "dbname": (u.path or "/postgres").lstrip("/") or "postgres"}

    def _exec(self, sql: str, dbname: str | None = None) -> str:
        p = self._params()
        db = dbname or p["dbname"]
        try:
            import psycopg  # type: ignore

            with psycopg.connect(host=p["host"], port=p["port"], user=p["user"], password=p["password"], dbname=db,
                                 autocommit=True, connect_timeout=5) as conn:
                cur = conn.execute(sql)
                try:
                    return "\n".join(" ".join(str(x) for x in row) for row in cur.fetchall())
                except psycopg.ProgrammingError:
                    return ""
        except ImportError:
            env = dict(os.environ, PGPASSWORD=p["password"])
            r = subprocess.run(["psql", "-h", p["host"], "-p", p["port"], "-U", p["user"], "-d", db, "-At", "-v",
                                "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True, timeout=20, env=env)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:300])
            return r.stdout.strip()

    def create(self) -> dict[str, str]:
        name = "run_" + secrets.token_hex(4)
        password = secrets.token_urlsafe(12)
        self._exec(f"CREATE ROLE {name} LOGIN PASSWORD '{password}'")
        self._exec(f"CREATE DATABASE {name} OWNER {name}")
        self.created.append(name)
        p = self._params()
        return {"host": p["host"], "port": p["port"], "user": name, "password": password, "dbname": name}

    def tables(self, dbname: str) -> list[str]:
        try:
            out = self._exec("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1", dbname)
        except Exception:  # noqa: BLE001
            return []
        return [x for x in out.splitlines() if x]

    def cleanup(self) -> None:
        for name in self.created:
            for sql in (f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"DROP ROLE IF EXISTS {name}"):
                try:
                    self._exec(sql)
                except Exception:  # noqa: BLE001 — уборка не должна ронять отчёт
                    pass
        self.created.clear()


def dsn_of(p: dict[str, str]) -> str:
    return f"postgres://{p['user']}:{p['password']}@{p['host']}:{p['port']}/{p['dbname']}?sslmode=disable"


def db_env(p: dict[str, str], extra_roles: dict[str, str]) -> dict[str, str]:
    values = {"host": p["host"], "port": p["port"], "user": p["user"], "password": p["password"],
              "dbname": p["dbname"], "dsn": dsn_of(p), "sslmode": "disable"}
    env: dict[str, str] = {}
    for role, names in DB_ENV_ALIASES.items():
        for n in names:
            env[n] = values[role]
    for name, role in extra_roles.items():
        if role in values and re.fullmatch(r"[A-Z][A-Z0-9_]*", name or ""):
            env[name] = values[role]
    return env


def unreachable_db() -> dict[str, str]:
    return {"host": "127.0.0.1", "port": str(free_port()), "user": "nobody", "password": "nothing", "dbname": "nodb"}


class Engine:
    def __init__(self, work_root: str | None = None, pg_admin_dsn: str | None = None,
                 tools: dict[str, str] | None = None):
        self.work_root = work_root or tempfile.gettempdir()
        self.pg_admin_dsn = pg_admin_dsn
        self.tools = tools or {"goose": shutil.which("goose") or "", "migrate": shutil.which("migrate") or ""}

    # ------------------------------------------------------------------ запуск
    def run(self, bundle: bytes, spec: dict, *, timeout: float = 150.0) -> RunResult:
        started = time.time()
        goos, goarch = go_platform()
        result = RunResult(ok=True, goos=goos, goarch=goarch)
        workdir = Path(tempfile.mkdtemp(prefix="sbx-", dir=self.work_root))
        pg = Postgres(self.pg_admin_dsn)
        try:
            bundle_dir = workdir / "bundle"
            bundle_dir.mkdir()
            try:
                with zipfile.ZipFile(io.BytesIO(bundle)) as z:
                    for info in z.infolist():
                        target = (bundle_dir / info.filename).resolve()
                        if not str(target).startswith(str(bundle_dir.resolve())):
                            continue
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(z.read(info))
            except zipfile.BadZipFile as e:
                result.ok, result.error = False, f"bundle: {e}"
                return result
            app = bundle_dir / "app"
            if not app.exists():
                result.ok, result.error = False, "bundle: нет файла app"
                return result
            app.chmod(0o755)
            manifest = {}
            if (bundle_dir / "manifest.json").exists():
                manifest = json.loads((bundle_dir / "manifest.json").read_text("utf-8"))
            variables: dict[str, object] = {"PORT_A": free_port(), "PORT_B": free_port()}
            for sc in spec.get("scenarios", []):
                left = timeout - (time.time() - started)
                sid = str(sc.get("id", "scenario"))
                if left < 5:
                    result.scenarios.append(ScenarioResult(sid, sc.get("kind", "service"), sc.get("title", sid),
                                                           ok=None, detail="не хватило времени бюджета запуска"))
                    continue
                kind = sc.get("kind", "service")
                sdir = workdir / f"sc_{sid}"
                sdir.mkdir()
                for src in bundle_dir.iterdir():
                    if src.name in {"app", "manifest.json"}:
                        continue
                    if src.is_dir():
                        shutil.copytree(src, sdir / src.name)
                    else:
                        shutil.copy2(src, sdir / src.name)
                if kind == "migrations":
                    result.scenarios.append(self._migrations(sc, sdir, manifest, pg, min(left, 60)))
                else:
                    result.scenarios.append(self._service(sc, app, sdir, manifest, pg, variables, min(left, 60)))
            result.outcomes = self._outcomes(result.scenarios, spec)
        except Exception as e:  # noqa: BLE001 — движок не должен ронять сервис
            result.ok, result.error = False, f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            pg.cleanup()
            shutil.rmtree(workdir, ignore_errors=True)
            result.elapsed = round(time.time() - started, 1)
        return result

    # ------------------------------------------------------------------ service
    def _service(self, sc: dict, app: Path, sdir: Path, manifest: dict, pg: Postgres,
                 variables: dict[str, object], budget: float) -> ScenarioResult:
        t0 = time.time()
        sid = str(sc.get("id", "scenario"))
        res = ScenarioResult(sid, "service", sc.get("title", sid))
        env_vars: dict[str, str] = {str(k): str(_subst(v, variables)) for k, v in (sc.get("env") or {}).items()}
        extra_roles = manifest.get("env_roles") or {}
        for name, role in extra_roles.items():
            if role == "http_port" and "PORT" in env_vars:
                env_vars.setdefault(name, env_vars["PORT"])
        db_mode = sc.get("db")
        db_params = None
        if db_mode == "unreachable":
            db_params = unreachable_db()
        elif db_mode in {"fresh", "migrated"}:
            # Без тестовой базы сценарий всё равно запускается: решение задания 1 базы не требует,
            # а решение задания 2 не стартует и честно даст «не проверено».
            db_note = ""
            if not pg.available:
                db_note = "тестовая база недоступна, запуск без базы"
            else:
                try:
                    db_params = pg.create()
                except Exception as e:  # noqa: BLE001
                    db_note = f"не удалось создать тестовую базу ({str(e)[:120]}), запуск без базы"
            if db_params and db_mode == "migrated":
                ok, note = self._migrate(sdir, manifest, db_params)
                if not ok:
                    db_note = f"миграции перед сценарием не накатились ({note[:160]})"
                    # Без схемы пробы к базе бессмысленны: за миграции отвечают свои строки рубрики,
                    # а CRUD остаётся «не проверено», чтобы не наказывать дважды.
                    if sc.get("probes"):
                        res.detail = db_note + ", пробы не выполнялись"
                        res.elapsed = round(time.time() - t0, 1)
                        return res
            if db_note:
                res.detail = db_note + ". "
        if db_params:
            env_vars = db_env(db_params, extra_roles) | env_vars
        env_mode = sc.get("env_mode", "both")
        if env_mode in {"file", "both"}:
            (sdir / ".env").write_text("".join(f"{k}={v}\n" for k, v in env_vars.items()), encoding="utf-8")
        proc_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(sdir), "TMPDIR": str(sdir), "LANG": "C.UTF-8"}
        if env_mode in {"process", "both"}:
            proc_env |= env_vars
        args = [str(a) for a in _subst(sc.get("args") or [], variables)]
        # Запасные порты, занятые ещё до старта (чужие процессы на машине), стартом сервиса не считаются.
        busy = {p for p in FALLBACK_PORTS if port_open(p, 0.1)}
        try:
            proc = subprocess.Popen([str(app), *args], cwd=sdir, env=proc_env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            res.started, res.detail = False, f"бинарник не запустился: {e}"
            res.elapsed = round(time.time() - t0, 1)
            return res
        log = _LogReader(proc.stdout, proc.stderr)
        try:
            listen = sc.get("expect_listen")
            start_timeout = float(sc.get("start_timeout", DEFAULT_START_TIMEOUT))
            if listen is not None:
                expected = int(_subst(listen, variables))
                not_listen = sc.get("expect_not_listen")
                other = int(_subst(not_listen, variables)) if not_listen is not None else None
                fallbacks = ([other] if other is not None else []) + [p for p in FALLBACK_PORTS if p not in busy]
                found = self._wait_listen(proc, expected, start_timeout, fallbacks)
                if found is None:
                    res.started = False
                    res.exit_code = proc.poll()
                    reason = (f"процесс завершился с кодом {res.exit_code}" if res.exit_code is not None
                              else f"порт {expected} не открылся за {start_timeout:g} с")
                    res.detail += f"сервис не стартовал ({reason}). Лог: {self._tail(log.text())}"
                    return res
                port = found
                res.started = True
                res.ok = True
                if port == expected:
                    res.detail += f"стартовал за {time.time() - t0:.1f} с и слушает заданный порт {port}"
                    res.checks["port"] = {"ok": True, "detail": f"слушает заданный порт {port}: как в ТЗ"}
                else:
                    res.detail += f"стартовал за {time.time() - t0:.1f} с, но слушает порт {port} вместо заданного {expected}"
                    res.checks["port"] = {"ok": False, "detail": f"слушает порт {port} вместо заданного {expected}: настройка порта не применяется, НЕ по ТЗ"}
                if other is not None and other != port:
                    if port_open(other):
                        res.checks["port"] = {"ok": False, "detail": res.checks["port"]["detail"] + f"; порт {other} тоже открыт"}
                    else:
                        res.checks["port"]["detail"] += f", порт {other} закрыт"
                out_text, err_text = log.stdout_text().strip(), log.stderr_text().strip()
                res.checks["stdout"] = {"ok": bool(out_text), "detail": (
                    f"лог старта в stdout: «{self._tail(out_text, 120)}»" if out_text else
                    (f"при старте stdout пуст, в stderr: «{self._tail(err_text, 120)}»" if err_text else
                     "при старте ни stdout, ни stderr ничего не получили"))}
                captured: dict[str, object] = {}
                for p in sc.get("probes") or []:
                    res.probes.append(self._probe(p, port, variables | captured, captured))
                stop = sc.get("stop")
                if stop:
                    self._stop(proc, log, stop, res)
            elif sc.get("expect_exit_within") is not None:
                self._expect_exit(proc, log, sc, res, variables, t0)
            else:
                found = self._wait_listen(proc, int(variables["PORT_A"]), start_timeout, [p for p in FALLBACK_PORTS if p not in busy])
                res.started = found is not None
                res.ok = res.started
                res.detail += f"стартовал, порт {found}" if found else "не стартовал"
        finally:
            self._kill(proc)
            res.log_tail = self._tail(log.text(), 1500)
            res.elapsed = round(time.time() - t0, 1)
        return res

    def _expect_exit(self, proc, log, sc: dict, res: ScenarioResult, variables: dict, t0: float) -> None:
        """Сценарий, где ожидается завершение процесса (например, паника без базы)."""
        limit = float(sc.get("expect_exit_within"))
        deadline = time.time() + limit
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)
        code = proc.poll()
        res.exit_code = code
        res.started = True  # процесс запускался; оценивается именно его завершение
        took = time.time() - t0
        text = log.text()
        if code is None:
            listening = port_open(int(variables["PORT_A"])) or port_open(8080)
            if listening:
                res.ok = False
                res.detail = f"сервис стартовал и слушает порт, хотя база недоступна: паники или остановки нет за {limit:g} с"
            else:
                res.ok = None
                res.detail = f"процесс жив {limit:g} с, порт не слушает и не завершился: возможно, ретраи подключения. Лог: {self._tail(text)}"
            return
        nonzero = sc.get("expect_exit_nonzero", True)
        if nonzero:
            res.ok = code != 0
            how = "паника" if "panic:" in text else ("завершение с ошибкой" if code != 0 else "тихое завершение")
            res.detail = (f"{how}, код {code}, через {took:.1f} с"
                          + ("" if res.ok else ": ожидался ненулевой код")
                          + f". Лог: {self._tail(text, 300)}")
        else:
            res.ok = code == 0
            res.detail = f"завершился с кодом {code}"

    def _wait_listen(self, proc, port: int, timeout: float, fallbacks: list[int] | None = None) -> int | None:
        """Ждёт, пока процесс откроет заданный порт или один из запасных. Возвращает открытый порт."""
        candidates = [port] + [p for p in (fallbacks or []) if p != port]
        deadline = time.time() + timeout
        while time.time() < deadline:
            for p in candidates:
                if port_open(p):
                    return p
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        for p in candidates:
            if port_open(p):
                return p
        return None

    def _probe(self, p: dict, port: int, variables: dict, captured: dict) -> ProbeResult:
        pid = str(p.get("id", p.get("path", "probe")))
        method = str(p.get("method", "GET")).upper()
        path = str(_subst(p.get("path", "/"), variables))
        body = None
        headers = {}
        if "json" in p:
            body = json.dumps(_subst(p["json"], variables), ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        t0 = time.time()
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=float(p.get("timeout", DEFAULT_PROBE_TIMEOUT)))
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            raw = resp.read(BODY_LIMIT)
            conn.close()
        except (OSError, HTTPException) as e:
            return ProbeResult(pid, method, path, False, None, f"{method} {path}: нет ответа ({type(e).__name__})",
                               int((time.time() - t0) * 1000))
        text = " ".join(raw.decode("utf-8", "replace").split())
        parsed = None
        try:
            parsed = json.loads(text) if text else None
        except ValueError:
            parsed = None
        problems: list[str] = []
        expect_status = p.get("expect_status")
        if expect_status is not None:
            allowed = expect_status if isinstance(expect_status, list) else [expect_status]
            if status not in allowed:
                problems.append(f"код {status}, ожидался {' или '.join(str(a) for a in allowed)}")
        if p.get("expect_empty_body") and method != "HEAD" and text:
            problems.append("тело ответа не пустое")
        if "expect_json" in p:
            if parsed != p["expect_json"]:
                problems.append(f"тело {text[:80] or '(пусто)'}, ожидалось {json.dumps(p['expect_json'], ensure_ascii=False)}")
        if "expect_json_includes" in p:
            want = p["expect_json_includes"]
            if not isinstance(parsed, dict) or any(parsed.get(k) != v for k, v in want.items()):
                problems.append(f"в теле нет полей {json.dumps(want, ensure_ascii=False)}: {text[:80] or '(пусто)'}")
        if p.get("expect_json_array") and not isinstance(parsed, list):
            problems.append(f"ожидался JSON-массив, получено {text[:60] or '(пусто)'}")
        if p.get("expect_json_object") and not isinstance(parsed, dict):
            problems.append(f"ожидался JSON-объект, получено {text[:60] or '(пусто)'}")
        for name, key in (p.get("capture") or {}).items():
            if isinstance(parsed, dict):
                key = str(key).lstrip("$.")
                if key in parsed:
                    captured[name] = parsed[key]
        shown = ""
        if text and status != 204 and method != "HEAD":
            shown = f", тело {text[:80]}"
        if problems:
            return ProbeResult(pid, method, path, False, status,
                               f"{method} {path} → {status}{shown}: {'; '.join(problems)}: НЕ по ТЗ",
                               int((time.time() - t0) * 1000))
        return ProbeResult(pid, method, path, True, status, f"{method} {path} → {status}{shown}: как в ТЗ",
                           int((time.time() - t0) * 1000))

    def _stop(self, proc, log, stop: dict, res: ScenarioResult) -> None:
        sig_name = str(stop.get("signal", "SIGTERM")).upper()
        sig = getattr(signal, sig_name, signal.SIGTERM)
        before = len(log.text())
        t0 = time.time()
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        limit = float(stop.get("expect_exit_within", DEFAULT_STOP_TIMEOUT))
        deadline = time.time() + limit
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.05)
        exited = proc.poll() is not None
        took = time.time() - t0
        if exited and proc.returncode is not None and proc.returncode < 0:
            # Отрицательный код: процесс убит самим сигналом, обработчика в программе нет.
            res.checks["stop"] = {"ok": False, "detail": f"{sig_name}: процесс убит сигналом без обработки (код {proc.returncode}), "
                                  "корректного завершения нет"}
        elif exited:
            res.checks["stop"] = {"ok": True, "detail": f"{sig_name}: завершился сам за {took:.1f} с (код {proc.returncode})"}
        else:
            res.checks["stop"] = {"ok": False, "detail": f"{sig_name}: не завершился за {limit:g} с, снят принудительно"}
            self._kill(proc)
        time.sleep(0.2)
        after = log.text()[before:]
        # log: любое сообщение при остановке (строка рубрики); log_exact: точный текст из ТЗ, если задан.
        res.checks["log"] = {"ok": bool(after.strip()),
                             "detail": ("после сигнала в лог выведено: " + self._tail(after, 200)) if after.strip()
                             else "после сигнала в лог ничего не выведено"}
        expect = stop.get("expect_log")
        if expect:
            found = str(expect).lower() in log.text().lower()
            res.checks["log_exact"] = {"ok": found, "detail": (f"в логе есть текст из ТЗ «{expect}»" if found else
                                       f"текста из ТЗ «{expect}» в логе нет")}

    # --------------------------------------------------------------- migrations
    def _migrations(self, sc: dict, sdir: Path, manifest: dict, pg: Postgres, budget: float) -> ScenarioResult:
        t0 = time.time()
        sid = str(sc.get("id", "migrate"))
        res = ScenarioResult(sid, "migrations", sc.get("title", sid))
        mig = manifest.get("migrations") or []
        if not mig:
            res.detail = "в снимке не найдены файлы миграций (goose или migrate)"
            res.elapsed = round(time.time() - t0, 1)
            return res
        if not pg.available:
            res.detail = "тестовая база недоступна, миграции не прогонялись"
            res.elapsed = round(time.time() - t0, 1)
            return res
        try:
            params = pg.create()
        except Exception as e:  # noqa: BLE001
            res.detail = f"не удалось создать тестовую базу: {str(e)[:200]}"
            res.elapsed = round(time.time() - t0, 1)
            return res
        first_ok, first = self._migrate(sdir, manifest, params)
        res.checks["first"] = {"ok": first_ok, "detail": "чистая база: " + first}
        if first_ok:
            second_ok, second = self._migrate(sdir, manifest, params)
            res.checks["second"] = {"ok": second_ok, "detail": "повторный запуск: " + second}
        else:
            res.checks["second"] = {"ok": None, "detail": "повтор не выполнялся: первый прогон упал"}
        tables = pg.tables(params["dbname"])
        res.started = True
        res.ok = first_ok and bool(res.checks["second"]["ok"])
        res.detail = f"{mig[0]['tool']} ({mig[0]['dir']}): " + ("таблицы: " + ", ".join(tables) if tables else "таблиц нет")
        res.elapsed = round(time.time() - t0, 1)
        return res

    def _migrate(self, sdir: Path, manifest: dict, params: dict[str, str]) -> tuple[bool, str]:
        mig = (manifest.get("migrations") or [None])[0]
        if not mig:
            return False, "миграции не найдены"
        tool = mig.get("tool", "goose")
        directory = sdir / mig.get("dir", "migrations")
        if not directory.exists():
            return False, f"каталог {mig.get('dir')} не попал в бандл"
        binary = self.tools.get(tool) or shutil.which(tool) or ""
        if not binary:
            return False, f"инструмент {tool} недоступен в песочнице"
        dsn = dsn_of(params)
        if tool == "goose":
            cmd = [binary, "-dir", str(directory), "postgres", dsn, "up"]
        else:
            cmd = [binary, "-path", str(directory), "-database", dsn, "up"]
        try:
            r = subprocess.run(cmd, cwd=sdir, capture_output=True, text=True, timeout=60,
                               env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(sdir)})
        except subprocess.TimeoutExpired:
            return False, f"{tool} up не уложился в 60 с"
        out = (r.stdout + r.stderr).strip()
        tail = self._tail(out, 240) or "(без вывода)"
        return r.returncode == 0, (f"{tool} up успешно: {tail}" if r.returncode == 0 else f"{tool} up с ошибкой (код {r.returncode}): {tail}")

    # ------------------------------------------------------------------ outcomes
    @staticmethod
    def _outcomes(scenarios: list[ScenarioResult], spec: dict | None = None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        declared: dict[str, list[str]] = {}
        for sc in (spec or {}).get("scenarios", []):
            sid = str(sc.get("id", "scenario"))
            if sc.get("kind", "service") == "migrations":
                declared[sid] = [f"{sid}.first", f"{sid}.second"]
            else:
                declared[sid] = [f"{sid}.port", f"{sid}.stop", f"{sid}.log", f"{sid}.log_exact", f"{sid}.stdout"] + [f"{sid}.{p.get('id')}" for p in sc.get("probes") or []]
        for s in scenarios:
            if s.kind == "migrations":
                if not s.checks:
                    out[s.id] = {"status": "na", "detail": f"миграции: {s.detail}"}
                    out[f"{s.id}.first"] = out[s.id]
                    out[f"{s.id}.second"] = out[s.id]
                    continue
                out[s.id] = {"status": "pass" if s.ok else "fail", "detail": f"миграции {s.detail}"}
                for key in ("first", "second"):
                    c = s.checks.get(key)
                    if c is None or c.get("ok") is None:
                        out[f"{s.id}.{key}"] = {"status": "na", "detail": (c or {}).get("detail", "не выполнялось")}
                    else:
                        out[f"{s.id}.{key}"] = {"status": "pass" if c["ok"] else "fail", "detail": c["detail"]}
                continue
            if s.ok is None:
                na = {"status": "na", "detail": f"сценарий «{s.title}»: {s.detail}"}
                out[s.id] = na
                for key in ("port", "stop", "log", "log_exact", "stdout"):
                    out[f"{s.id}.{key}"] = na
                for p in s.probes:
                    out[f"{s.id}.{p.id}"] = na
                continue
            out[s.id] = {"status": "pass" if s.ok else "fail", "detail": f"сценарий «{s.title}»: {s.detail}"}
            for key, c in s.checks.items():
                out[f"{s.id}.{key}"] = {"status": "pass" if c.get("ok") else "fail", "detail": c.get("detail", "")}
            for p in s.probes:
                out[f"{s.id}.{p.id}"] = {"status": "pass" if p.ok else "fail", "detail": p.detail}
        # Объявленные, но не выполненные исходы (сценарий пропущен или оборван) помечаются явно.
        for s in scenarios:
            for ref in declared.get(s.id, []):
                out.setdefault(ref, {"status": "na", "detail": f"сценарий «{s.title}»: {s.detail or 'не выполнялся'}"})
        return out

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _kill(proc) -> None:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _tail(text: str, limit: int = 400) -> str:
        text = (text or "").strip()
        if len(text) <= limit:
            return text.replace("\n", " | ")
        return "…" + text[-limit:].replace("\n", " | ")
