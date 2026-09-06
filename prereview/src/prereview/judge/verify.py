"""Проверка цитат кодом. Нет цитаты в работе — нет основания, значит нет балла.

Алгоритм (docs/PIPELINE.md §7.3): ищем цитату в указанном файле рядом с указанными строками,
потом по всему файлу с нормализацией пробелов и правим адрес, потом по другим файлам.
Не нашли — цитата выбрасывается, уверенность понижается, а pass без единой
подтверждённой цитаты уходит в needs_human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from prereview.artifact.model import Work, WorkFile
from prereview.judge.schema import EvidenceItem

MAX_QUOTE = 4000
MIN_QUOTE = 6  # символов без пробелов


@dataclass
class VerifiedQuote:
    path: str
    line_start: int
    line_end: int
    quote: str
    status: str  # exact | relocated | dropped
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"exact", "relocated"}


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Убирает все пробелы; для каждого символа результата помнит номер исходной строки.

    Так цитата совпадает при любых отступах, переносах и табуляции, а сравнение
    остаётся посимвольным, поэтому ложных совпадений почти нет.
    """
    out: list[str] = []
    lines: list[int] = []
    line = 1
    for ch in text:
        if ch == "\n":
            line += 1
        if ch.isspace():
            continue
        out.append(ch.lower())
        lines.append(line)
    return "".join(out), lines


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


@lru_cache(maxsize=256)
def _file_index(path: str, text: str) -> tuple[str, tuple[int, ...]]:
    norm, lines = _normalize_with_map(text)
    return norm, tuple(lines)


def _find_in_file(f: WorkFile, quote: str, near: int | None) -> tuple[int, int] | None:
    norm_text, line_map = _file_index(f.path, f.text)
    q = _normalize(quote)
    if len(q) < MIN_QUOTE:
        return None
    positions: list[int] = []
    start = 0
    while len(positions) < 50:
        idx = norm_text.find(q, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + 1
    if not positions:
        return None
    if near is not None:
        positions.sort(key=lambda i: abs(line_map[i] - near))
    idx = positions[0]
    return line_map[idx], line_map[min(idx + len(q) - 1, len(line_map) - 1)]


def verify_quote(work: Work, ev: EvidenceItem) -> VerifiedQuote:
    quote = (ev.quote or "")[:MAX_QUOTE]
    start = int(ev.line_start or 0)
    end = int(ev.line_end or start)
    if len(_normalize(quote)) < MIN_QUOTE:
        return VerifiedQuote(ev.path, start, end, quote, "dropped", "пустая цитата")
    claimed = work.file(ev.path)
    candidates: list[WorkFile] = []
    if claimed is not None:
        candidates.append(claimed)
    if len(work.files) == 1 and work.files[0] not in candidates:
        candidates.append(work.files[0])
    candidates.extend(f for f in work.files if f not in candidates)
    for i, f in enumerate(candidates):
        found = _find_in_file(f, quote, start if (i == 0 and start) else None)
        if found is None:
            continue
        ls, le = found
        same_file = f is claimed or (claimed is None and i == 0)
        within = same_file and start and start - 2 <= ls and le <= (end or start) + 2
        if within:
            return VerifiedQuote(f.path, ls, le, quote, "exact")
        note = "адрес исправлен" if same_file else f"найдено в другом файле: {f.path}"
        return VerifiedQuote(f.path, ls, le, quote, "relocated", note)
    return VerifiedQuote(ev.path, start, end, quote, "dropped", "цитата не найдена в работе")


def verify_all(work: Work, items: list[EvidenceItem]) -> list[VerifiedQuote]:
    return [verify_quote(work, ev) for ev in items]
