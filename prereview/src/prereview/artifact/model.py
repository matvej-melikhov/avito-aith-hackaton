"""Работа студента как адресуемый текст: файлы с нумерованными строками."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

Format = Literal["markdown", "docx", "pdf", "zip"]

CODE_EXT = {
    ".go": "go", ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".java": "java", ".kt": "kotlin", ".rs": "rust", ".c": "c", ".cpp": "cpp", ".h": "c",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".sql": "sql", ".sh": "shell", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".json": "json", ".xml": "xml", ".html": "html", ".css": "css",
    ".proto": "proto", ".mod": "gomod", ".sum": "gosum", ".dockerfile": "dockerfile",
    ".ini": "ini", ".env": "env", ".mk": "make", ".makefile": "make", ".txt": "text", ".md": "markdown",
    ".ipynb": "notebook", ".csv": "csv",
}


@dataclass
class WorkFile:
    path: str
    text: str
    language: str | None = None

    @cached_property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    @property
    def size(self) -> int:
        return len(self.text)

    def numbered(self, start: int = 1, end: int | None = None) -> str:
        """Строки с номерами, начиная с 1, как их видит модель."""
        end = min(end or len(self.lines), len(self.lines))
        width = len(str(end))
        return "\n".join(f"{i:>{width}}| {self.lines[i - 1]}" for i in range(max(start, 1), end + 1))


@dataclass
class Work:
    format: Format
    media_type: str
    files: list[WorkFile] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def file(self, path: str) -> WorkFile | None:
        for f in self.files:
            if f.path == path:
                return f
        # Модель иногда пишет путь без верхней папки или с ведущим ./
        norm = path.lstrip("./")
        for f in self.files:
            if f.path.endswith("/" + norm) or f.path == norm:
                return f
        return None

    @property
    def total_chars(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def is_repo(self) -> bool:
        return self.format == "zip"

    def tree(self, limit: int = 400) -> str:
        paths = [f"{f.path} ({f.size} симв.)" for f in self.files[:limit]]
        if len(self.files) > limit:
            paths.append(f"… ещё {len(self.files) - limit} файлов")
        return "\n".join(paths)
