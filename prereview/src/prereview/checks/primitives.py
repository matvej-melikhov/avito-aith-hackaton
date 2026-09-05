"""Примитивы формальных проверок. Работают по Work, не исполняют код, не ходят в сеть.

Каждый примитив возвращает CheckOutcome со статусом pass | fail | na | error и адресами.
Параметры приходят из CheckSpec (файл задания). Неизвестный kind или ошибка парсера
дают error, а не fail: ошибка проверки никогда не превращается в провал.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Callable

from prereview.artifact.model import Work, WorkFile

Status = str  # pass | fail | na | error


@dataclass
class Evidence:
    path: str
    line: int
    snippet: str


@dataclass
class CheckOutcome:
    status: Status
    note: str
    evidence: list[Evidence] = field(default_factory=list)


def _files(work: Work, scope: str | list[str] | None) -> list[WorkFile]:
    if not scope:
        return list(work.files)
    globs = [scope] if isinstance(scope, str) else list(scope)
    return [f for f in work.files if any(fnmatch.fnmatch(f.path, g) or fnmatch.fnmatch("/" + f.path, g) for g in globs)]


def _grep(files: list[WorkFile], pattern: str, flags: int = 0, limit: int = 20) -> list[Evidence]:
    rx = re.compile(pattern, flags)
    hits: list[Evidence] = []
    for f in files:
        for i, line in enumerate(f.lines, 1):
            if rx.search(line):
                hits.append(Evidence(f.path, i, line.strip()[:200]))
                if len(hits) >= limit:
                    return hits
    return hits


def file_exists(work: Work, glob: str, **_: object) -> CheckOutcome:
    hits = [f for f in work.files if fnmatch.fnmatch(f.path, glob)]
    if hits:
        return CheckOutcome("pass", f"найдено: {', '.join(f.path for f in hits[:5])}", [Evidence(hits[0].path, 1, hits[0].lines[0][:120])])
    return CheckOutcome("fail", f"нет файла по маске {glob}")


def file_absent(work: Work, glob: str, **_: object) -> CheckOutcome:
    hits = [f for f in work.files if fnmatch.fnmatch(f.path, glob)]
    if hits:
        return CheckOutcome("fail", f"файл не должен присутствовать: {hits[0].path}", [Evidence(hits[0].path, 1, "")])
    return CheckOutcome("pass", f"файлов по маске {glob} нет")


def dir_exists(work: Work, path: str, **_: object) -> CheckOutcome:
    prefix = path.strip("/") + "/"
    hits = [f for f in work.files if f.path.startswith(prefix)]
    if hits:
        return CheckOutcome("pass", f"каталог {path}: {len(hits)} файлов", [Evidence(hits[0].path, 1, "")])
    return CheckOutcome("fail", f"нет каталога {path}")


def count_files(work: Work, glob: str, min: int = 1, max: int | None = None, **_: object) -> CheckOutcome:
    hits = [f for f in work.files if fnmatch.fnmatch(f.path, glob)]
    n = len(hits)
    ok = n >= min and (max is None or n <= max)
    ev = [Evidence(f.path, 1, "") for f in hits[:5]]
    return CheckOutcome("pass" if ok else "fail", f"файлов по маске {glob}: {n} (нужно от {min}{' до ' + str(max) if max else ''})", ev)


def grep(work: Work, pattern: str, scope: str | list[str] | None = None, expect: str = "present",
         min_count: int = 1, max_count: int | None = None, ignore_case: bool = False, **_: object) -> CheckOutcome:
    files = _files(work, scope)
    if not files:
        return CheckOutcome("na", f"нет файлов в области {scope}")
    hits = _grep(files, pattern, re.I if ignore_case else 0, limit=max(max_count or 0, 20) + 1)
    n = len(hits)
    if expect == "absent":
        if n == 0:
            return CheckOutcome("pass", f"вхождений {pattern!r} нет")
        return CheckOutcome("fail", f"найдено {n} вхождений {pattern!r}, а не должно быть", hits[:10])
    ok = n >= min_count and (max_count is None or n <= max_count)
    return CheckOutcome("pass" if ok else "fail", f"вхождений {pattern!r}: {n} (нужно от {min_count})", hits[:10])


def section_exists(work: Work, pattern: str, min: int = 1, **_: object) -> CheckOutcome:
    rx = re.compile(pattern, re.I)
    hits: list[Evidence] = []
    for f in work.files:
        for i, line in enumerate(f.lines, 1):
            if re.match(r"^\s{0,3}#{1,6}\s", line) and rx.search(line):
                hits.append(Evidence(f.path, i, line.strip()[:160]))
    ok = len(hits) >= min
    return CheckOutcome("pass" if ok else "fail", f"заголовков по шаблону {pattern!r}: {len(hits)}", hits[:5])


def min_words(work: Work, n: int, **_: object) -> CheckOutcome:
    words = sum(len(f.text.split()) for f in work.files)
    return CheckOutcome("pass" if words >= n else "fail", f"слов: {words}, нужно не меньше {n}")


def max_words(work: Work, n: int, **_: object) -> CheckOutcome:
    words = sum(len(f.text.split()) for f in work.files)
    return CheckOutcome("pass" if words <= n else "fail", f"слов: {words}, нужно не больше {n}")


def max_pages(work: Work, n: int, **_: object) -> CheckOutcome:
    pages = work.meta.get("pages")
    if pages is None:
        return CheckOutcome("na", "число страниц известно только для PDF")
    return CheckOutcome("pass" if pages <= n else "fail", f"страниц: {pages}, нужно не больше {n}")


def count_images(work: Work, min: int = 1, **_: object) -> CheckOutcome:
    embedded = int(work.meta.get("images_embedded", 0) or 0)
    refs = 0
    for f in work.files:
        refs += len(re.findall(r"!\[[^\]]*\]\([^)]+\)", f.text))
    total = max(embedded, refs)
    return CheckOutcome("pass" if total >= min else "fail", f"изображений: встроенных {embedded}, ссылок {refs}, нужно от {min}")


def count_tables(work: Work, min_rows: int = 1, **_: object) -> CheckOutcome:
    rows = sum(len(re.findall(r"^\s*\|.+\|\s*$", f.text, re.M)) for f in work.files)
    return CheckOutcome("pass" if rows >= min_rows else "fail", f"строк таблиц: {rows}, нужно от {min_rows}")


def count_code_blocks(work: Work, min: int = 1, **_: object) -> CheckOutcome:
    n = sum(len(re.findall(r"```", f.text)) // 2 for f in work.files)
    return CheckOutcome("pass" if n >= min else "fail", f"блоков кода: {n}, нужно от {min}")


def mermaid_blocks(work: Work, min: int = 1, kinds: list[str] | None = None, **_: object) -> CheckOutcome:
    """Блоки mermaid/plantuml; kinds — обязательные типы (C4Context, sequenceDiagram, …)."""
    hits: list[Evidence] = []
    found_kinds: set[str] = set()
    rx_kind = re.compile(r"\b(" + "|".join(map(re.escape, kinds)) + r")\b") if kinds else None
    for f in work.files:
        for i, line in enumerate(f.lines, 1):
            if re.match(r"^\s*```\s*(mermaid|plantuml|puml)\b", line, re.I) or line.strip().startswith("@startuml"):
                hits.append(Evidence(f.path, i, line.strip()[:80]))
            if rx_kind:
                m = rx_kind.search(line)
                if m:
                    found_kinds.add(m.group(1))
                    hits.append(Evidence(f.path, i, line.strip()[:120]))
    missing = [k for k in (kinds or []) if k not in found_kinds]
    ok = len([h for h in hits if h.snippet.startswith(("```", "@startuml"))]) >= min and not missing
    note = f"диаграмм: {sum(1 for h in hits if h.snippet.startswith(('```', '@startuml')))}"
    if kinds:
        note += f"; найдены типы: {', '.join(sorted(found_kinds)) or 'нет'}" + (f"; не хватает: {', '.join(missing)}" if missing else "")
    return CheckOutcome("pass" if ok else "fail", note, hits[:8])


def test_defs(work: Work, min: int = 1, scope: str | list[str] | None = None, **_: object) -> CheckOutcome:
    files = _files(work, scope)
    hits = _grep(files, r"\bdef test_|\bfunc Test[A-Z]|\bit\(|\btest\(", limit=50)
    return CheckOutcome("pass" if len(hits) >= min else "fail", f"определений тестов: {len(hits)}, нужно от {min}", hits[:5])


PRIMITIVES: dict[str, Callable[..., CheckOutcome]] = {
    "file_exists": file_exists,
    "file_absent": file_absent,
    "dir_exists": dir_exists,
    "count_files": count_files,
    "grep": grep,
    "section_exists": section_exists,
    "min_words": min_words,
    "max_words": max_words,
    "max_pages": max_pages,
    "count_images": count_images,
    "count_tables": count_tables,
    "count_code_blocks": count_code_blocks,
    "mermaid_blocks": mermaid_blocks,
    "test_defs": test_defs,
}
