"""ПДн и секреты не уходят в модель. Инъекции в тексте работы вырезаются из срезов и поднимают флаг.

Текст работы для модели — данные, не инструкции. Здесь мы ещё и убираем из данных то,
что похоже на инструкции модели, а ревьюер видит флаг injection_suspect.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from prereview.artifact.model import Work, WorkFile

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")
KEY_PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{35}\b"),  # telegram bot token
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
ASSIGN_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{12,})['\"]?"
)

INJECTION = [
    re.compile(r"(?i)ignore (?:all |the )?(?:previous|prior|above) (?:instructions|prompts?)"),
    re.compile(r"(?i)disregard (?:all |the )?(?:previous|prior|above)"),
    re.compile(r"(?i)you are (?:now )?(?:a |an |the )?(?:grader|reviewer|assistant|ai|llm|model)\b"),
    re.compile(r"(?i)system prompt"),
    re.compile(r"(?i)\bas an ai\b"),
    re.compile(r"(?i)игнорируй (?:все |предыдущие |прошлые )?(?:инструкции|указания|правила)"),
    re.compile(r"(?i)(?:поставь|выстави|засчитай|оцени)\s+(?:на\s+)?(?:максимум|максимальн\w*|высш\w*|все баллы|\d+\s*(?:баллов|балла|из))"),
    re.compile(r"(?i)(?:это|данная|эта) работа (?:заслуживает|достойна) (?:максим|высш|отличн)"),
    re.compile(r"(?i)(?:если ты|ты)\s+(?:модель|нейросеть|ии|ai|llm|бот|ревьюер-бот)\b"),
    re.compile(r"(?i)\bassistant\s*:\s"),
]


@dataclass
class RedactionReport:
    replacements: dict[str, int] = field(default_factory=dict)
    injection_hits: list[dict] = field(default_factory=list)

    @property
    def injection_suspect(self) -> bool:
        return bool(self.injection_hits)

    def summary(self) -> dict:
        return {"replacements": self.replacements, "injection_hits": self.injection_hits[:20]}


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / len(s) * math.log2(c / len(s)) for c in counts.values())


def redact_text(text: str, report: RedactionReport) -> str:
    def bump(kind: str) -> None:
        report.replacements[kind] = report.replacements.get(kind, 0) + 1

    def sub(pattern: re.Pattern, placeholder: str, kind: str, s: str) -> str:
        def repl(_m: re.Match) -> str:
            bump(kind)
            return placeholder

        return pattern.sub(repl, s)

    text = sub(EMAIL, "<EMAIL>", "email", text)
    text = sub(PHONE, "<PHONE>", "phone", text)
    for pat in KEY_PATTERNS:
        text = sub(pat, "<SECRET>", "secret", text)

    def assign_repl(m: re.Match) -> str:
        value = m.group(2)
        if _entropy(value) >= 3.0 or len(value) >= 20:
            bump("secret")
            return f"{m.group(1)}=<SECRET>"
        return m.group(0)

    text = ASSIGN_SECRET.sub(assign_repl, text)
    return text


def scan_injections(path: str, text: str, report: RedactionReport) -> str:
    """Заменяет строки с признаками инструкций модели на пометку, сохраняя номера строк."""
    lines = text.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines, 1):
        hit = next((p for p in INJECTION if p.search(line)), None)
        if hit:
            report.injection_hits.append({"path": path, "line": i, "text": line.strip()[:160]})
            out.append("<REDACTED: suspected instruction to the model>")
        else:
            out.append(line)
    return "\n".join(out)


def redact_work(work: Work) -> tuple[Work, RedactionReport]:
    """Возвращает копию работы для модели и отчёт. Исходная работа не меняется."""
    report = RedactionReport()
    files = []
    for f in work.files:
        text = redact_text(f.text, report)
        text = scan_injections(f.path, text, report)
        files.append(WorkFile(f.path, text, f.language))
    clean = Work(work.format, work.media_type, files, list(work.flags), dict(work.meta))
    if report.injection_suspect and "injection_suspect" not in clean.flags:
        clean.flags.append("injection_suspect")
    return clean, report
