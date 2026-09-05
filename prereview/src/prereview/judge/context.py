"""Общий контекст одного прогона: клиент, промпты, работа, факты."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from prereview.artifact.condense import render_work
from prereview.artifact.inventory import inventory, render
from prereview.artifact.model import Work
from prereview.config import Settings
from prereview.llm.client import LLMClient
from prereview.llm.prompts import PromptStore


@dataclass
class JudgeContext:
    settings: Settings
    client: LLMClient
    prompts: PromptStore
    work: Work  # уже после редактирования ПДн и инъекций
    assignment_text: str = ""
    guidance: str = ""
    facts_text: str = "Формальные проверки для этого задания не заданы."
    flags: list[str] = field(default_factory=list)

    @cached_property
    def inventory(self) -> dict:
        return inventory(self.work)

    @cached_property
    def inventory_text(self) -> str:
        return render(self.inventory)

    @cached_property
    def rendered(self) -> tuple[str, list[str]]:
        return render_work(self.work, self.settings.judge_context_chars)

    @property
    def work_text(self) -> str:
        return self.rendered[0]

    @property
    def condensed(self) -> bool:
        return bool(self.rendered[1])

    def slice(self, paths: list[str], budget: int | None = None) -> tuple[str, list[str]]:
        return render_work(self.work, budget or self.settings.judge_context_chars, only=paths)
