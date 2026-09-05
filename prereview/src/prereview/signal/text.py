"""Простые текстовые признаки генерации. Каждый слабый сам по себе, так и подписан."""

from __future__ import annotations

import re
import statistics

from prereview.artifact.model import Work
from prereview.signal.base import Ground

TEMPLATE_PHRASES = [
    "важно отметить", "стоит отметить", "следует отметить", "таким образом", "в заключение",
    "подводя итог", "кроме того", "не менее важно", "следует подчеркнуть", "в современном мире",
    "ключевым аспектом", "играет ключевую роль", "позволяет обеспечить", "является ключевым",
    "в рамках данной", "необходимо отметить", "в целом", "обеспечивает надёжность", "обеспечивает надежность",
    "комплексный подход", "оптимальное решение", "гибкость и масштабируемость",
]
DIALOG = [
    r"(?i)^вот (?:готов|пример|реализац|вариант|решени|обновл)", r"(?i)конечно[!,]", r"(?i)надеюсь, (?:это|данн)",
    r"(?i)если (?:нужно|хотите|потребуется), (?:могу|я могу)", r"(?i)дайте знать", r"(?i)\bhere(?:'s| is) (?:the|an?|your)\b",
    r"(?i)\bcertainly[!,]", r"(?i)\bas an ai\b", r"(?i)\bi hope this helps\b", r"(?i)\blet me know\b",
    r"(?i)^(?:sure|great question)[!,.]", r"(?i)могу (?:также |ещё )?(?:добавить|расширить|дополнить)",
]


def _text_files(work: Work):
    return [f for f in work.files if (f.language in {"markdown", "text", None}) or work.format != "zip"]


def paragraph_uniformity(work: Work, meta: dict) -> Ground | None:
    lens: list[int] = []
    for f in _text_files(work):
        for para in re.split(r"\n\s*\n", f.text):
            words = len(para.split())
            if words >= 25 and not para.lstrip().startswith(("|", "```", "-", "*", "#")):
                lens.append(words)
    if len(lens) < 8:
        return None
    cv = statistics.pstdev(lens) / (statistics.mean(lens) or 1)
    if cv < 0.3:
        return Ground("paragraph_uniformity", "style", "weak",
                      f"Абзацы очень ровные по длине: {len(lens)} абзацев, средняя длина {statistics.mean(lens):.0f} слов, разброс {cv:.2f}.",
                      "Ровные абзацы бывают у аккуратных авторов и в шаблонных отчётах.")
    return None


def em_dash_density(work: Work, meta: dict) -> Ground | None:
    text = "\n".join(f.text for f in _text_files(work))
    if len(text) < 2000:
        return None
    dashes = text.count("—")
    per_k = dashes / (len(text) / 1000)
    if per_k >= 3 and dashes >= 15:
        ev = []
        for f in _text_files(work):
            for i, line in enumerate(f.lines, 1):
                if "—" in line:
                    ev.append((f.path, i, line.strip()[:120]))
                    if len(ev) >= 3:
                        break
            if len(ev) >= 3:
                break
        return Ground("em_dash_density", "style", "weak",
                      f"Много длинных тире: {dashes} на {len(text) // 1000} тыс. символов.",
                      "Редакторы и docx подставляют длинное тире автоматически, это признак типографики, а не автора.", ev)
    return None


def template_phrases(work: Work, meta: dict) -> Ground | None:
    text = "\n".join(f.text for f in _text_files(work)).lower()
    found = {p: text.count(p) for p in TEMPLATE_PHRASES if p in text}
    distinct = len(found)
    total = sum(found.values())
    if distinct >= 5 or total >= 8:
        ev = []
        for f in _text_files(work):
            for i, line in enumerate(f.lines, 1):
                low = line.lower()
                if any(p in low for p in found):
                    ev.append((f.path, i, line.strip()[:120]))
                    if len(ev) >= 4:
                        break
            if len(ev) >= 4:
                break
        strength = "strong" if distinct >= 8 else "weak"
        return Ground("template_phrases", "style", strength,
                      f"Шаблонные связки: {distinct} разных, {total} вхождений ({', '.join(list(found)[:5])}).",
                      "Канцелярские связки встречаются и в живых учебных текстах.", ev)
    return None


def dialog_remnants(work: Work, meta: dict) -> Ground | None:
    ev = []
    for f in work.files:
        for i, line in enumerate(f.lines, 1):
            if any(re.search(p, line.strip()) for p in DIALOG):
                ev.append((f.path, i, line.strip()[:140]))
                if len(ev) >= 5:
                    break
        if len(ev) >= 5:
            break
    if ev:
        return Ground("dialog_remnants", "artifacts", "strong",
                      f"Остатки диалога с ассистентом: {len(ev)} фраз вроде «{ev[0][2][:60]}».",
                      "Фраза могла попасть из шаблона или переписки, проверьте контекст.", ev)
    return None


def markdown_in_docx(work: Work, meta: dict) -> Ground | None:
    if work.format != "docx":
        return None
    ev = []
    for f in work.files:
        for i, line in enumerate(f.lines, 1):
            if re.search(r"\\\*\\\*|\\#{1,3} |^\\- |\\`", line):
                ev.append((f.path, i, line.strip()[:120]))
                if len(ev) >= 5:
                    break
    if len(ev) >= 3:
        return Ground("markdown_in_docx", "artifacts", "strong",
                      f"Markdown-разметка внутри docx как обычный текст ({len(ev)} строк): похоже на вставку из чата.",
                      "Автор мог писать в markdown-редакторе и вставить как текст.", ev)
    return None


def list_heavy(work: Work, meta: dict) -> Ground | None:
    lines = [ln for f in _text_files(work) for ln in f.lines if ln.strip()]
    if len(lines) < 40:
        return None
    bullets = sum(1 for ln in lines if re.match(r"^\s*(?:[-*•]|\d+[.)])\s", ln))
    share = bullets / len(lines)
    if share >= 0.65:
        return Ground("list_heavy", "style", "weak",
                      f"Почти весь текст списками: {bullets} из {len(lines)} непустых строк ({share:.0%}).",
                      "Списки требует и само задание, для технических документов это норма.")
    return None


TEXT_SIGNALS = [paragraph_uniformity, em_dash_density, template_phrases, dialog_remnants, markdown_in_docx, list_heavy]
