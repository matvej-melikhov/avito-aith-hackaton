"""Сборка и статическая проверка Go-снимка как факты для судьи.

`go build ./...` и `go vet ./...` не исполняют код студента (CGO выключен, generate не
вызывается), поэтому их можно запускать без песочницы с таймаутом. Это не тесты курса,
но ревьюер ловит несобирающийся код именно запуском, и система должна видеть то же.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from prereview.artifact.model import Work
from prereview.checks.primitives import CheckOutcome, Evidence

ERR_LINE = re.compile(r"^(?:#\s.*\n)?(?P<path>[\w./\\-]+\.go):(?P<line>\d+)(?::\d+)?:\s*(?P<msg>.+)$", re.M)


@dataclass
class GoBuildReport:
    available: bool
    build_ok: bool | None = None
    vet_ok: bool | None = None
    build_output: str = ""
    vet_output: str = ""
    elapsed: float = 0.0
    errors: list[Evidence] = field(default_factory=list)
    note: str = ""

    def facts(self) -> str:
        if not self.available:
            return "Сборка Go не выполнялась: инструмент go недоступен."
        if self.build_ok is None:
            return f"Сборка Go не выполнена: {self.note}"
        lines = [f"- go build ./...: {'успешно' if self.build_ok else 'ОШИБКА'}"]
        if not self.build_ok:
            lines.append("  " + self.build_output.strip()[:1200].replace("\n", "\n  "))
            lines.append("  Строки рубрики, требующие запуска сервера, тестов или миграций, при несобирающемся коде считать невыполненными.")
        if self.vet_ok is not None:
            lines.append(f"- go vet ./...: {'замечаний нет' if self.vet_ok else 'есть замечания'}")
            if not self.vet_ok:
                lines.append("  " + self.vet_output.strip()[:800].replace("\n", "\n  "))
        return "ФАКТЫ СБОРКИ (выполнено кодом):\n" + "\n".join(lines)

    def to_dict(self) -> dict:
        return {"available": self.available, "build_ok": self.build_ok, "vet_ok": self.vet_ok,
                "elapsed": round(self.elapsed, 1), "note": self.note,
                "build_output": self.build_output[-2000:], "vet_output": self.vet_output[-1500:]}


def is_go_project(work: Work) -> bool:
    return work.is_repo and any(f.path == "go.mod" for f in work.files)


def _materialize(work: Work, root: Path) -> None:
    for f in work.files:
        target = root / f.path
        if not str(target.resolve()).startswith(str(root.resolve())):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.text, encoding="utf-8")


def _parse_errors(output: str, limit: int = 8) -> list[Evidence]:
    out: list[Evidence] = []
    for m in ERR_LINE.finditer(output):
        out.append(Evidence(m.group("path").replace("./", ""), int(m.group("line")), m.group("msg")[:200]))
        if len(out) >= limit:
            break
    return out


def go_build(work: Work, *, timeout: int = 180, vet: bool = True) -> GoBuildReport:
    go = shutil.which("go")
    if not go:
        return GoBuildReport(available=False)
    if not is_go_project(work):
        return GoBuildReport(available=True, note="в снимке нет go.mod")
    import time

    started = time.time()
    with tempfile.TemporaryDirectory(prefix="prereview-go-") as tmp:
        root = Path(tmp) / "src"
        root.mkdir()
        _materialize(work, root)
        cache = Path(tmp) / "cache"
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tmp,
            "GOPATH": str(cache / "gopath"),
            "GOCACHE": str(cache / "gocache"),
            "GOMODCACHE": os.environ.get("PREREVIEW_GOMODCACHE") or str(cache / "modcache"),
            "GOFLAGS": "-mod=mod",
            "GOPROXY": os.environ.get("GOPROXY", "https://proxy.golang.org,direct"),
            "GOTOOLCHAIN": "local",
            "CGO_ENABLED": "0",
            "GOTELEMETRY": "off",
        }
        report = GoBuildReport(available=True)
        try:
            build = subprocess.run([go, "build", "./..."], cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            report.note = f"go build не уложился в {timeout} с"
            report.elapsed = time.time() - started
            return report
        report.build_ok = build.returncode == 0
        report.build_output = (build.stderr or build.stdout)[-4000:]
        report.errors = _parse_errors(report.build_output)
        if vet and report.build_ok:
            try:
                v = subprocess.run([go, "vet", "./..."], cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
                report.vet_ok = v.returncode == 0
                report.vet_output = (v.stderr or v.stdout)[-3000:]
                if not report.vet_ok:
                    report.errors += _parse_errors(report.vet_output)
            except subprocess.TimeoutExpired:
                report.vet_output = "go vet не уложился в таймаут"
        report.elapsed = time.time() - started
        return report


def go_build_check(work: Work, **_: object) -> CheckOutcome:
    """Примитив для файла задания: kind = go_build."""
    r = go_build(work)
    if not r.available:
        return CheckOutcome("error", "инструмент go недоступен")
    if r.build_ok is None:
        return CheckOutcome("na" if "go.mod" in r.note else "error", r.note)
    if r.build_ok:
        return CheckOutcome("pass", "go build ./... успешно" + ("" if r.vet_ok in (None, True) else "; go vet с замечаниями"), r.errors)
    return CheckOutcome("fail", "go build ./... с ошибками: " + r.build_output.strip().splitlines()[-1][:160] if r.build_output.strip() else "go build ./... с ошибками", r.errors)
