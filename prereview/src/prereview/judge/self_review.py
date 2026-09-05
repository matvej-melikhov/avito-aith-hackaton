"""Самопроверка студента: публичные критерии, без баллов и закрытого контекста."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from prereview.judge.context import JudgeContext
from prereview.judge.schema import SelfFinding, SelfSummary
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


LEAK = re.compile(r"(строк[аи]?\s*\d+[–-]?\d*|line\s*\d+|\d+[–-]\d+\s*строк|`[^`]{12,}`|«[^»]{40,}»|\"[^\"]{40,}\")", re.I)
SUMMARY_PREFIX = "Итог самопроверки: "


def sanitize(feedback: str) -> str:
    """Убирает из подсказки адреса, длинные цитаты и списки: студент видит область, а не чек-лист."""
    text = LEAK.sub("", feedback).strip()
    text = re.sub(r"\s{2,}", " ", text)
    first = re.split(r"(?<=[.!?])\s+", text)[0] if text else ""
    return (first or "Перечитай это требование в условии и сверь с работой.").strip()[:240]


def summarize(ctx: JudgeContext, results: list["SelfResult"]) -> str:
    prompt = ctx.prompts.get("self_review_summary")
    lines = [f"- {r.criterion.title}: {r.status}. {r.feedback}" for r in results]
    system, user = prompt.parts(assignment=ctx.assignment_text[:3000] or "(не передано)", statuses="\n".join(lines))
    try:
        res = ctx.client.complete_structured(system=system, user=user, schema=SelfSummary, tag="self_summary", max_tokens=600)
        text = res.value.summary.strip()
    except LLMError as e:
        log.warning("self summary failed: %s", e)
        text = ""
    if not text:
        attention = sum(1 for r in results if r.status == "needs_attention")
        text = ("Работа в целом собрана, перепроверь требования, помеченные как «нужно внимание», по условию задания."
                if attention else "По видимым требованиям замечаний нет. Перечитай условие ещё раз перед отправкой.") + \
               " Окончательное решение принимает ревьюер."
    return SUMMARY_PREFIX + text[:1500]


def self_check(ctx: JudgeContext, c: Criterion) -> SelfResult:
    if c.check_class == "judgement":
        # Оценочные требования студенту не подсказываем: их смотрит ревьюер.
        return SelfResult(c, "not_checked", "Это оценочное требование, его смотрит ревьюер.")
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
    feedback = sanitize(f.feedback)
    if status == "met" and not verified:
        status = "needs_attention"
        feedback = "Не удалось подтвердить в тексте работы: перечитай это требование в условии."
    if status == "met":
        feedback = f"Требование «{c.title}» в работе видно."
    return SelfResult(c, status, feedback, verified)
