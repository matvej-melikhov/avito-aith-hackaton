"""Промпты живут в prompts/*.md как версионируемые файлы. Версия = хэш содержимого."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

SPLIT = "\n=== USER ===\n"


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str

    def render(self, **values: str) -> str:
        return Template(self.text).safe_substitute(values)

    def parts(self, **values: str) -> tuple[str, str]:
        """Файл делится строкой `=== USER ===` на системную и пользовательскую части."""
        text = self.render(**values)
        if SPLIT in text:
            system, user = text.split(SPLIT, 1)
            return system.strip(), user.strip()
        return text.strip(), ""


class PromptStore:
    def __init__(self, directory: Path):
        self.directory = directory

    @lru_cache(maxsize=64)
    def get(self, name: str) -> Prompt:
        path = self.directory / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return Prompt(name, text, version)

    def versions(self) -> dict[str, str]:
        return {p.stem: self.get(p.stem).version for p in sorted(self.directory.glob("*.md"))}
