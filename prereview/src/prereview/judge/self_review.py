"""Самопроверка студента: публичные критерии, без баллов и закрытого контекста."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from prereview.judge.context import JudgeContext
from prereview.judge.schema import SelfFinding
from prereview.judge.verify import VerifiedQuote, verify_all
from prereview.llm.client import LLMError
from prereview.rubric.model import Criterion

log = logging.getLogger(__name__)


@dataclass
class SelfResult:
    criterion: Criterion
    status: str  # met | needs_attention | not_checked
    feedback: str
    verified: list[VerifiedQuote] = field(default_factory=list)

    @property
    def evidence_text(self) -> str:
        return "; ".join(f"{v.path}: строки {v.line_start}–{v.line_end}" for v in self.verified[:5])


def self_check(ctx: JudgeContext, c: Criterion) -> SelfResult:
    if "needs_vision" in ctx.work.flags:
        return SelfResult(c, "not_checked", "Файл без текстового слоя, автоматическая проверка недоступна.")
    prompt = ctx.prompts.get("self_review")
    system, user = prompt.parts(
        assignment=ctx.assignment_text or "(не передано)", title=c.title,
        max_points=str(c.max_points), inventory=ctx.inventory_text, work=ctx.work_text,
    )
    try:
        res = ctx.client.complete_structured(system=system, user=user, schema=SelfFinding,
                                             tag=f"self:{c.key}", max_tokens=2500)
    except LLMError as e:
        log.warning("self-review %s failed: %s", c.key, e)
        return SelfResult(c, "not_checked", "Автоматическая проверка не сработала, посмотри этот пункт сам.")
    f = res.value
    verified = [v for v in verify_all(ctx.work, f.evidence) if v.ok]
    status = f.status
    feedback = f.feedback.strip() or "Проверь этот пункт."
    if status == "met" and not verified:
        status = "needs_attention"
        feedback = "Не удалось подтвердить цитатой: " + feedback
    return SelfResult(c, status, feedback, verified)
