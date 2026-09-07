"""Рендер работы для модели с сохранением номеров строк и явными пометками пропусков.

Обрезка «первые N символов» — систематическая ошибка (docs/archive/grading-experiment/GRADING_EXPERIMENT.md §2.5).
Здесь бюджет делится между файлами пропорционально размеру, в каждом файле остаются
начало и конец, а пропуск помечается с числом строк. Номера строк остаются исходными,
поэтому цитаты модели проверяются кодом по тем же адресам.
"""

from __future__ import annotations

from prereview.artifact.model import Work, WorkFile

MIN_FILE_BUDGET = 600
HEAD_FRAC = 0.55


def render_file(f: WorkFile, budget: int) -> tuple[str, bool]:
    """Возвращает (текст с номерами строк, был ли пропуск)."""
    if f.size <= budget:
        return f.numbered(), False
    head_budget = int(budget * HEAD_FRAC)
    tail_budget = budget - head_budget
    lines = f.lines
    n = len(lines)
    head_end = 0
    used = 0
    while head_end < n and used + len(lines[head_end]) + 8 <= head_budget:
        used += len(lines[head_end]) + 8
        head_end += 1
    tail_start = n
    used = 0
    while tail_start > head_end and used + len(lines[tail_start - 1]) + 8 <= tail_budget:
        used += len(lines[tail_start - 1]) + 8
        tail_start -= 1
    if tail_start <= head_end:
        return f.numbered(), False
    skipped = tail_start - head_end
    parts = [f.numbered(1, head_end) if head_end else ""]
    parts.append(f"[… пропущено {skipped} строк ({head_end + 1}–{tail_start}) из-за лимита контекста …]")
    parts.append(f.numbered(tail_start + 1, n))
    return "\n".join(p for p in parts if p), True


def render_work(work: Work, budget: int, *, only: list[str] | None = None) -> tuple[str, list[str]]:
    """Собирает срезы всех (или выбранных) файлов в один текст.

    Возвращает текст и список файлов, которые пришлось ужать.
    """
    files = [f for f in work.files if only is None or f.path in only]
    if not files:
        return "", []
    total = sum(f.size for f in files) or 1
    header_cost = sum(len(f.path) + 24 for f in files)
    available = max(budget - header_cost, MIN_FILE_BUDGET * len(files))
    condensed: list[str] = []
    blocks: list[str] = []
    for f in files:
        share = max(MIN_FILE_BUDGET, int(available * f.size / total))
        body, cut = render_file(f, share)
        if cut:
            condensed.append(f.path)
        blocks.append(f"===== FILE: {f.path} =====\n{body}")
    text = "\n\n".join(blocks)
    if condensed:
        text = (
            f"[ВНИМАНИЕ: {len(condensed)} файл(ов) показаны с пропусками из-за лимита контекста; "
            "пропуски помечены. Отсутствие фрагмента в срезе не значит, что его нет в работе: "
            "в таком случае ставь not_found с низкой уверенностью, а не fail.]\n\n" + text
        )
    return text, condensed
