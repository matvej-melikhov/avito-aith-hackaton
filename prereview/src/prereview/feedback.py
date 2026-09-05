"""Черновик обратной связи студенту и сводка ревьюеру."""

from __future__ import annotations

import logging

from prereview.judge.criterion import CriterionResult
from prereview.llm.client import LLMClient
from prereview.llm.prompts import PromptStore

log = logging.getLogger(__name__)
STATUS_RU = {"suggested": "предложен балл", "needs_human": "нужен ревьюер", "not_checked": "не проверено"}


def results_text(results: list[CriterionResult]) -> str:
    rows = []
    for r in results:
        c = r.criterion
        pts = "—" if r.proposed_points is None else f"{r.proposed_points:g}"
        row = f"- {c.title}: {STATUS_RU[r.status]}, {pts} из {c.max_points:g}. {r.reason}"
        if r.student_feedback:
            row += f" Совет: {r.student_feedback}"
        if r.missing:
            row += f" Не найдено: {'; '.join(r.missing[:3])}"
        rows.append(row)
    return "\n".join(rows)


def fallback_feedback(results: list[CriterionResult]) -> str:
    tips = [r.student_feedback.strip() for r in results if r.student_feedback.strip()]
    done = [r.criterion.title for r in results if r.status == "suggested" and r.proposed_points == r.criterion.max_points]
    lines = ["Привет! Посмотрел работу, вот что стоит поправить:"]
    lines += [f"{i}. {t.rstrip('.')}." for i, t in enumerate(tips[:7], 1)]
    if not tips:
        lines.append("1. Замечаний по критериям нет, проверь оформление и выводы.")
    if done:
        lines.append("Хорошо сделано: " + ", ".join(done[:4]) + ".")
    return "\n".join(lines)


def draft_feedback(client: LLMClient, prompts: PromptStore, results: list[CriterionResult]) -> str:
    prompt = prompts.get("feedback")
    system, user = prompt.parts(results=results_text(results))
    try:
        res = client.complete_text(system=system, user=user, tag="feedback", max_tokens=900)
        text = res.value.strip()
        return text if text else fallback_feedback(results)
    except Exception as e:  # noqa: BLE001
        log.warning("feedback draft failed: %s", e)
        return fallback_feedback(results)


def reviewer_summary(results: list[CriterionResult], signal_level: str, cost_rub: float, tokens: int,
                     prompt_versions: dict[str, str], model: str, harness: str) -> str:
    look = [r.criterion.title for r in results if r.status == "needs_human"]
    failed = [r.criterion.title for r in results if r.status == "suggested" and r.proposed_points == 0]
    unchecked = [r.criterion.title for r in results if r.status == "not_checked"]
    total = sum(r.proposed_points or 0 for r in results)
    maximum = sum(r.criterion.max_points for r in results)
    parts = [f"Сводка: предложено {total:g} из {maximum:g}."]
    if look:
        parts.append("Посмотреть самому: " + ", ".join(look[:4]) + ".")
    if failed:
        parts.append("Не выполнено: " + ", ".join(failed[:4]) + ".")
    if unchecked:
        parts.append("Не проверено автоматически: " + ", ".join(unchecked[:4]) + ".")
    parts.append(f"Сигнал ИИ: {signal_level}.")
    versions = ", ".join(f"{k}@{v}" for k, v in sorted(prompt_versions.items()) if k in {"judge_criterion", "self_review"})
    parts.append(f"Стоимость прогона: {cost_rub:.2f} ₽ ({tokens} токенов, модель {model}, исследователь: {harness}). Промпты: {versions}.")
    return " ".join(parts)
