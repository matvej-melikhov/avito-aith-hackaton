"""Сводка сигнала об ИИ: уровень по правилу, основания, ограничения, вопросы для Q&A.

Уровень не сумма баллов: высокий, если есть два независимых основания из разных групп
и нет декларации; средний, если одно сильное или два слабых; иначе низкий.
Порог не калибровался, об этом написано в объяснении. В баллы сигнал не входит.
"""

from __future__ import annotations

import logging

from prereview.artifact.model import Work
from prereview.contracts import AssistEvidence, AuthorshipSignal
from prereview.judge.schema import QAQuestions
from prereview.signal.base import Ground
from prereview.signal.code import CODE_SIGNALS
from prereview.signal.declaration import find_declaration
from prereview.signal.text import TEXT_SIGNALS

log = logging.getLogger(__name__)
LEVEL_RU = {"low": "низкий", "medium": "средний", "high": "высокий"}
SIGNAL_ID = "prereview-signal-v1"


def collect_grounds(work: Work, meta: dict) -> list[Ground]:
    signals = list(TEXT_SIGNALS) + (list(CODE_SIGNALS) if work.is_repo else [])
    grounds: list[Ground] = []
    for fn in signals:
        try:
            g = fn(work, meta)
        except Exception as e:  # noqa: BLE001 — один сломанный сигнал не должен ронять остальные
            log.warning("signal %s failed: %s", fn.__name__, e)
            continue
        if g:
            grounds.append(g)
    if "injection_suspect" in work.flags:
        hits = meta.get("injection_hits") or []
        grounds.append(Ground("injection_suspect", "artifacts", "strong",
                              f"В работе есть текст, похожий на инструкции для модели ({len(hits)} мест).",
                              "Может быть цитатой из задания или шуткой, но ревьюеру стоит взглянуть.",
                              [(h["path"], h["line"], h["text"]) for h in hits[:5]]))
    return grounds


def level_of(grounds: list[Ground], declared: bool) -> str:
    groups = {g.group for g in grounds}
    strong = [g for g in grounds if g.strength == "strong"]
    if len(groups) >= 2 and len(grounds) >= 2 and not declared:
        return "high"
    if strong or len(grounds) >= 2:
        return "medium"
    return "low"


def build_signal(work: Work, meta: dict, *, client=None, prompts=None, results_text: str = "",
                 assignment_text: str = "") -> tuple[AuthorshipSignal, dict]:
    grounds = collect_grounds(work, meta)
    declaration, decl_hits = find_declaration(work, meta.get("student_comment", ""))
    level = level_of(grounds, declaration == "declared")
    not_available = []
    if work.is_repo:
        not_available.append("история git: снимок GitHub приходит без .git, признаки процесса (один коммит перед дедлайном, авторство) недоступны")
    not_available.append("стилометрия относительно прошлых работ студента: нет доступа к предыдущим сдачам")
    questions: list[str] = []
    if client is not None and prompts is not None and (grounds or results_text):
        try:
            prompt = prompts.get("qa_questions")
            system, user = prompt.parts(
                assignment=assignment_text[:3000] or "(не передано)",
                grounds="\n".join(f"- {g.text} Ограничение: {g.limitation}" for g in grounds) or "(оснований нет)",
                results=results_text[:6000] or "(нет)",
            )
            res = client.complete_structured(system=system, user=user, schema=QAQuestions, tag="qa_questions", max_tokens=600)
            questions = [q.strip() for q in res.value.questions[:3] if q.strip()]
        except Exception as e:  # noqa: BLE001
            log.warning("qa questions failed: %s", e)
    # Оценка в процентах: грубая, по уровню и числу оснований, не калибрована. Нужна ревьюеру
    # как ориентир, поэтому диапазоны разнесены: низкий до 25, средний 40–60, высокий 70–90.
    base = {"low": 0.08, "medium": 0.40, "high": 0.70}[level]
    cap = {"low": 0.25, "medium": 0.60, "high": 0.90}[level]
    probability = round(min(cap, base + 0.05 * len(grounds)), 2)
    decl_text = ("студент задекларировал использование ИИ, нарушения нет" if declaration == "declared"
                 else "декларации об использовании ИИ в работе нет")
    if grounds:
        head = f"Оценка модели: {round(probability * 100)}%, уровень {LEVEL_RU[level]}. Основания ниже, каждое с примером из работы; {decl_text}."
    else:
        head = f"Оценка модели: {round(probability * 100)}%, уровень {LEVEL_RU[level]}. Простые признаки генерации в тексте и коде не найдены; {decl_text}."
    lines = [head, "Порог не калибровался на реальном потоке, на балл не влияет, это повод для разговора со студентом."]
    if not_available:
        lines.append("Недоступно: " + "; ".join(not_available) + ".")
    # Основания уходят списком причин: текст основания, пример и адрес.
    evidence = []
    for g in grounds:
        sample = next((snippet.strip() for _, _, snippet in g.evidence if snippet.strip()), "")
        path, line = (g.evidence[0][0], g.evidence[0][1]) if g.evidence else ("", 0)
        reason = g.text if not sample else f"{g.text} Пример: «{sample[:140]}»"
        evidence.append(AssistEvidence(quote=reason[:1000], locator=f"{path}:{line}" if path else g.signal,
                                       path=path if path and ("/" in path or "." in path) else None,
                                       line_start=line if line >= 1 else None, line_end=line if line >= 1 else None))
    for path, line, snippet in decl_hits[:1]:
        evidence.append(AssistEvidence(quote=f"Декларация об использовании ИИ: «{snippet[:140]}»", locator=f"{path}:{line}",
                                       path=path if "." in path else None, line_start=line, line_end=line))
    signal = AuthorshipSignal(id=SIGNAL_ID, probability=probability, explanation="\n".join(lines)[:10000], evidence=evidence[:50])
    record = {"level": level, "probability": probability, "questions": questions, "declaration": declaration, "grounds": [g.to_dict() for g in grounds],
              "not_available": not_available, "questions": questions}
    return signal, record
