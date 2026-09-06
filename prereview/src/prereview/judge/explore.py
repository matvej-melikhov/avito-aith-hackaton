"""Исследователь снимка репозитория: DeepSeek Harness как субагент только для чтения.

Harness получает копию работы уже после редактирования ПДн и инъекций, без .env,
запускается в профиле headless с патчем harness/prereview.cordis.yml (sandbox read-only,
без bash, записи, сети и субагентов), с таймаутом и лимитом вывода. Он только собирает
места и цитаты по критериям. Балл ставит наш судья, цитаты проверяет наш код.
Любой отказ Harness → пустой пакет свидетельств, судья работает по усаженному тексту.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from prereview.artifact.model import Work
from prereview.config import PROJECT_DIR, Settings
from prereview.judge.schema import HarnessCriterionEvidence, HarnessReport
from prereview.judge.verify import VerifiedQuote, verify_all
from prereview.llm.ledger import Ledger, Usage
from prereview.llm.prompts import PromptStore
from prereview.rubric.model import Criterion

log = logging.getLogger(__name__)
PATCH = PROJECT_DIR / "harness" / "prereview.cordis.yml"
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def lenient_json(text: str) -> str:
    """Модель иногда оставляет висячую запятую перед } или ]: убираем её до разбора."""
    try:
        json.loads(text)
        return text
    except ValueError:
        return TRAILING_COMMA.sub(r"\1", text)


@dataclass
class EvidencePack:
    source: str = "none"  # harness | none
    per_criterion: dict[str, HarnessCriterionEvidence] = field(default_factory=dict)
    verified: dict[str, list[VerifiedQuote]] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    elapsed: float = 0.0
    error: str | None = None
    session_id: str | None = None
    summary: str = ""

    def files_for(self, key: str) -> list[str]:
        return sorted({v.path for v in self.verified.get(key, [])})

    def text_for(self, key: str) -> str:
        ev = self.per_criterion.get(key)
        if ev is None:
            return ""
        lines = [f"НАЙДЕНО СУБАГЕНТОМ-ИССЛЕДОВАТЕЛЕМ (проверь по срезу ниже, цитаты уже сверены кодом):",
                 f"- найдено: {'да' if ev.found else 'нет'}; заметка: {ev.notes[:600]}"]
        for v in self.verified.get(key, [])[:6]:
            lines.append(f"- {v.path}:{v.line_start}-{v.line_end}: {v.quote[:300]!r}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "source": self.source, "elapsed": round(self.elapsed, 1), "error": self.error,
            "session_id": self.session_id,
            "usage": {"prompt_tokens": self.usage.prompt_tokens, "completion_tokens": self.usage.completion_tokens},
            "criteria": {k: {"found": v.found, "quotes": len(self.verified.get(k, [])), "notes": v.notes[:300]}
                         for k, v in self.per_criterion.items()},
        }


def materialize(work: Work, root: Path) -> Path:
    """Пишет отредактированную копию работы на диск для субагента."""
    root.mkdir(parents=True, exist_ok=True)
    for f in work.files:
        target = root / f.path
        if not str(target.resolve()).startswith(str(root.resolve())):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.text, encoding="utf-8")
    return root


def _criteria_text(criteria: list[Criterion]) -> str:
    rows = []
    for c in criteria:
        row = f"- criterion_id={c.key}: {c.title}."
        if c.description:
            row += f" {c.description[:400]}"
        if c.hints:
            row += f" Подсказки: {c.hints[:300]}"
        if c.scope:
            row += f" Где смотреть: {', '.join(c.scope)}"
        rows.append(row)
    return "\n".join(rows)


def _session_usage(home: Path, before: set[Path]) -> tuple[Usage, str | None]:
    sessions = home / "sessions"
    if not sessions.exists():
        return Usage(), None
    new = [p for p in sessions.rglob("session.jsonl") if p not in before]
    if not new:
        return Usage(), None
    path = max(new, key=lambda p: p.stat().st_mtime)
    prompt = completion = hit = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") != "assistant/message":
                continue
            usage = (ev.get("data") or {}).get("usage") or {}
            prompt += int(usage.get("inputTokens") or usage.get("prompt_tokens") or usage.get("input") or 0)
            completion += int(usage.get("outputTokens") or usage.get("completion_tokens") or usage.get("output") or 0)
            hit += int(usage.get("cacheReadTokens") or usage.get("cache_hit_tokens") or usage.get("cacheRead") or 0)
    except OSError:
        return Usage(), path.parent.name
    return Usage(prompt, completion, hit), path.parent.name


class HarnessExplorer:
    def __init__(self, settings: Settings, ledger: Ledger, prompts: PromptStore):
        self.settings = settings
        self.ledger = ledger
        self.prompts = prompts

    def available(self) -> bool:
        return self.settings.harness_enabled and shutil.which(self.settings.harness_bin) is not None and PATCH.exists()

    def explore(self, work: Work, criteria: list[Criterion], assignment_text: str) -> EvidencePack:
        pack = EvidencePack()
        if not self.available():
            pack.error = "harness недоступен"
            return pack
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="prereview-harness-") as tmp:
            tmp_path = Path(tmp)
            snapshot = materialize(work, tmp_path / "snapshot")
            # Постоянный дом Harness: профиль и его зависимости ставятся один раз,
            # сессии складываются туда же (в контейнере это том /data).
            home = self.settings.data_dir / "dsh-home"
            home.mkdir(parents=True, exist_ok=True)
            task = self.prompts.get("harness_repo_task").render(
                assignment=assignment_text[:4000] or "(не передано)", criteria=_criteria_text(criteria)
            )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "TMPDIR": tmp,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "DSH_HOME": str(home),
                "DSH_PERMISSION_MODE": "read-only",
                "DEEPSEEK_API_KEY": self.settings.api_key,
            }
            if self.settings.llm_base_url and "deepseek.com" not in self.settings.llm_base_url:
                env["DEEPSEEK_BASE_URL"] = self.settings.llm_base_url
            cmd = [self.settings.harness_bin, "--profile", "headless", "--patch", str(PATCH), task]
            before: set[Path] = set((home / "sessions").rglob("session.jsonl")) if (home / "sessions").exists() else set()
            try:
                proc = subprocess.run(
                    cmd, cwd=snapshot, env=env, capture_output=True, text=True,
                    timeout=self.settings.harness_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                pack.error = f"harness не уложился в {self.settings.harness_timeout_seconds} с"
                pack.elapsed = time.time() - started
                self.ledger.record("harness", self.settings.llm_model, Usage(), pack.elapsed, 1, "timeout")
                return pack
            except OSError as e:
                pack.error = f"harness не запустился: {e}"
                return pack
            pack.elapsed = time.time() - started
            pack.usage, pack.session_id = _session_usage(home, before)
            self.ledger.record("harness", self.settings.llm_model, pack.usage, pack.elapsed, 1,
                               "ok" if proc.returncode == 0 else f"exit {proc.returncode}")
            out = proc.stdout[-self.settings.harness_max_output_bytes:]
            if proc.returncode != 0:
                tail = proc.stderr.strip().splitlines()
                pack.error = "harness exit %d: %s" % (proc.returncode, " | ".join(ln.strip() for ln in tail[-6:])[:900])
                log.warning(pack.error)
                return pack
            blocks = JSON_BLOCK.findall(out)
            if not blocks:
                pack.error = "harness не вернул json-блок"
                return pack
            try:
                report = HarnessReport.model_validate_json(lenient_json(blocks[-1]))
            except ValueError as e:
                pack.error = f"json harness не по схеме: {str(e)[:200]}"
                return pack
            pack.source = "harness"
            pack.summary = JSON_BLOCK.sub("", out).strip()[:2000]
            by_key = {c.key: c for c in criteria}
            for item in report.criteria:
                key = item.criterion_id if item.criterion_id in by_key else next(
                    (k for k in by_key if k in item.criterion_id), item.criterion_id)
                pack.per_criterion[key] = item
                pack.verified[key] = [v for v in verify_all(work, item.evidence) if v.ok]
            return pack
