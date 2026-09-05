"""Декларация об использовании ИИ: курсы просят её указывать, сигнал сверяется с ней."""

from __future__ import annotations

import re

from prereview.artifact.model import Work

DECL = re.compile(
    r"(?i)(?:использовал\w*|применял\w*|с помощью|при помощи|помог\w*|сгенерирован\w*|generated (?:by|with)|"
    r"assisted by|with help of)\s+(?:[\w\-]+\s+){0,3}?(?:ии\b|нейросет\w*|chatgpt|gpt\b|claude|deepseek|copilot|gemini|llm|"
    r"языков\w+ модел\w*|ai\b)"
    r"|(?:ии|chatgpt|claude|deepseek|copilot|gemini)\s+(?:использовал\w*|помог\w*|применял\w*)"
)


def find_declaration(work: Work, extra_text: str = "") -> tuple[str, list[tuple[str, int, str]]]:
    """Возвращает (declared | not_declared, места)."""
    hits: list[tuple[str, int, str]] = []
    for i, line in enumerate(extra_text.splitlines(), 1):
        if DECL.search(line):
            hits.append(("комментарий к сдаче", i, line.strip()[:140]))
    for f in work.files:
        if f.language not in {"markdown", "text", None} and not f.path.lower().endswith((".md", ".txt")):
            continue
        for i, line in enumerate(f.lines, 1):
            if DECL.search(line):
                hits.append((f.path, i, line.strip()[:140]))
                if len(hits) >= 5:
                    break
    return ("declared" if hits else "not_declared"), hits
