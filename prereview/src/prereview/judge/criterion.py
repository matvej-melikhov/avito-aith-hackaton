"""Проверка одного критерия: формально кодом или моделью с верификацией цитат.

Правила, которые здесь не нарушаются:
- балл без подтверждённой кодом цитаты не показывается (pass/partial без цитат → needs_human);
- ошибка модели превращается в not_checked, никогда в балл;
- оценочный критерий получает балл только с confidence low;
- при усадке работы отсутствие не считается провалом.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from prereview.checks.run import CheckReport, run_checks
from prereview.judge.context import JudgeContext
from prereview.judge.schema import Judgement, Observations
from prereview.judge.verify import VerifiedQuote, verify_all
from prereview.llm.client import LLMError
from prereview.rubric.model import Criterion

log = logging.getLogger(__name__)

CAUTION = {"not_found": 0, "fail": 1, "partial": 2, "pass": 3}
CONF_DOWN = {"high": "medium", "medium": "low", "low": "low"}


@dataclass
class CriterionResult:
    criterion: Criterion
    status: str  # suggested | needs_human | not_checked
    proposed_points: float | None
    confidence: str
    reason: str
    requirement_met: bool | None = None
    verified: list[VerifiedQuote] = field(default_factory=list)
    dropped: list[VerifiedQuote] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reviewer_note: str = ""
    student_feedback: str = ""
    verdict: str | None = None
    path: str = "model"  # formal | model | not_checked
    repeats: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    check_report: CheckReport | None = None

    @property
    def evidence_lines(self) -> list[str]:
        out = [f"{v.path}:{v.line_start}-{v.line_end} (цитата подтверждена кодом{', ' + v.note if v.note else ''})" for v in self.verified]
        out += [f"цитата отклонена: {d.note}" for d in self.dropped]
        return out


def _class_guidance(ctx: JudgeContext, check_class: str) -> str:
    text = ctx.prompts.get("class_guidance").text
    blocks: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        if line.rstrip().endswith(":") and " " not in line.strip():
            current = line.strip()[:-1]
            blocks[current] = ""
        elif current:
            blocks[current] += line + "\n"
    return blocks.get(check_class, "").strip()


def _criterion_prompt(ctx: JudgeContext, c: Criterion, work_text: str, evidence_pack: str) -> tuple[str, str]:
    prompt = ctx.prompts.get("judge_criterion")
    return prompt.parts(
        class_guidance=_class_guidance(ctx, c.check_class),
        assignment=ctx.assignment_text or "(не передано)",
        guidance=ctx.guidance or "(нет)",
        title=c.title,
        description=c.description or "(нет)",
        max_points=str(c.max_points),
        score_step=str(c.score_step),
        check_class=c.check_class,
        hints=c.hints or "(нет)",
        facts=ctx.facts_text,
        inventory=ctx.inventory_text,
        evidence_pack=evidence_pack,
        work=work_text,
    )


def judge_formal(ctx: JudgeContext, c: Criterion) -> CriterionResult:
    report = run_checks(ctx.work, c)
    ev = [VerifiedQuote(e.path, e.line, e.line, e.snippet or "(файл найден)", "exact") for e in report.evidence[:10]]
    if report.status == "pass":
        points = c.snap(c.points_if_pass if c.points_if_pass is not None else c.max_points)
        return CriterionResult(c, "suggested", points, "high", f"Проверено кодом: {report.note()}", True, ev,
                               path="formal", check_report=report)
    if report.status == "fail":
        return CriterionResult(c, "suggested", 0.0, "high", f"Проверено кодом: {report.note()}", False, ev,
                               student_feedback=f"Проверь требование «{c.title}»: {report.note()}", path="formal",
                               check_report=report)
    return CriterionResult(c, "not_checked", None, "low", f"Формальная проверка не выполнена: {report.note()}",
                           None, reviewer_note="Проверьте вручную: автоматическая проверка недоступна.",
                           path="formal", check_report=report)


def _call(ctx: JudgeContext, c: Criterion, system: str, user: str, n: int) -> Judgement:
    res = ctx.client.complete_structured(
        system=system, user=user, schema=Judgement, tag=f"judge:{c.key}#{n}",
        max_tokens=ctx.settings.judge_max_tokens,
    )
    return res.value


def judge_model(ctx: JudgeContext, c: Criterion, *, work_text: str | None = None,
                evidence_pack: str = "", condensed: bool | None = None) -> CriterionResult:
    text = work_text if work_text is not None else ctx.work_text
    cut = ctx.condensed if condensed is None else condensed
    system, user = _criterion_prompt(ctx, c, text, evidence_pack or "")
    judgements: list[Judgement] = []
    errors: list[str] = []
    for n in range(1, max(1, ctx.settings.judge_repeats) + 1):
        try:
            judgements.append(_call(ctx, c, system, user, n))
        except LLMError as e:
            errors.append(str(e)[:300])
            log.warning("judge %s attempt %d failed: %s", c.key, n, e)
    if not judgements:
        return CriterionResult(c, "not_checked", None, "low",
                               "Модель не дала пригодного ответа, критерий не проверен автоматически.",
                               reviewer_note="Проверьте вручную. " + (errors[0] if errors else ""), path="not_checked",
                               flags=["model_error"])
    return merge(ctx, c, judgements, cut)


def merge(ctx: JudgeContext, c: Criterion, judgements: list[Judgement], condensed: bool) -> CriterionResult:
    verified_sets = [verify_all(ctx.work, j.evidence) for j in judgements]
    primary_idx = 0
    disagreement = len({j.verdict for j in judgements}) > 1
    if disagreement:
        primary_idx = min(range(len(judgements)), key=lambda i: CAUTION[judgements[i].verdict])
    j = judgements[primary_idx]
    verified = [v for v in verified_sets[primary_idx] if v.ok]
    dropped = [v for v in verified_sets[primary_idx] if not v.ok]
    # Подтверждённые цитаты второго прогона тоже полезны ревьюеру.
    for i, vs in enumerate(verified_sets):
        if i == primary_idx:
            continue
        for v in vs:
            if v.ok and all((v.path, v.line_start) != (u.path, u.line_start) for u in verified):
                verified.append(v)
    confidence = j.confidence
    notes: list[str] = []
    flags: list[str] = []
    if dropped:
        confidence = CONF_DOWN[confidence]
        flags.append("unverified_evidence")
        notes.append(f"{len(dropped)} цитат(ы) модели не нашлись в работе и удалены.")
    if disagreement:
        confidence = "low"
        flags.append("repeat_disagreement")
        others = "; ".join(f"повтор {i + 1}: {jj.verdict}, {jj.reason}" for i, jj in enumerate(judgements) if i != primary_idx)
        notes.append(f"Повторы разошлись, взят осторожный вариант. {others}")
    if j.reviewer_note:
        notes.insert(0, j.reviewer_note)

    verdict = j.verdict
    points = c.snap(j.proposed_points)
    status = "suggested"
    requirement_met: bool | None = None
    if verdict in {"pass", "partial"}:
        if not verified:
            status = "needs_human"
            points = None
            flags.append("no_verified_quote")
            notes.append("Модель не привела подтверждённой цитаты, балл не показываем.")
        else:
            requirement_met = True
            if points is None:
                points = c.snap(c.max_points if verdict == "pass" else c.max_points / 2)
            if verdict == "partial" and points == 0:
                points = c.snap(min(c.score_step, c.max_points))
    elif verdict == "fail":
        if condensed and not verified:
            status = "needs_human"
            points = None
            notes.append("Работа показана модели с пропусками, провал не подтверждён.")
        else:
            points = 0.0
            requirement_met = False
    else:  # not_found
        status = "needs_human"
        points = None
        notes.append("В работе не найдено: " + ("; ".join(j.missing) if j.missing else "модель не указала, где искала."))
    if c.check_class == "judgement":
        confidence = "low"
        if status == "suggested":
            notes.append("Оценочный критерий: балл предложен, решение за ревьюером.")
    if "injection_suspect" in ctx.work.flags:
        notes.append("В работе есть текст, похожий на инструкции модели (см. сигнал).")
    return CriterionResult(
        c, status, points, confidence, j.reason, requirement_met, verified, dropped, list(j.missing),
        " ".join(n for n in notes if n).strip(), j.student_feedback, verdict, "model",
        [jj.model_dump() for jj in judgements], flags=flags,
    )


def observe(ctx: JudgeContext, c: Criterion, work_text: str | None = None) -> list[dict]:
    """Наблюдения без балла для оценочных критериев."""
    prompt = ctx.prompts.get("observations")
    system, user = prompt.parts(title=c.title, description=c.description or "(нет)",
                                work=work_text if work_text is not None else ctx.work_text)
    try:
        res = ctx.client.complete_structured(system=system, user=user, schema=Observations,
                                             tag=f"observe:{c.key}", max_tokens=1200)
    except LLMError:
        return []
    out = []
    for o in res.value.observations[:3]:
        vs = [v for v in verify_all(ctx.work, o.evidence) if v.ok]
        out.append({"text": o.text, "evidence": [f"{v.path}:{v.line_start}-{v.line_end}" for v in vs]})
    return out


def judge(ctx: JudgeContext, c: Criterion, **kw: object) -> CriterionResult:
    if c.is_formal:
        return judge_formal(ctx, c)
    if c.check_class == "formal" and not c.checks:
        c = c.model_copy(update={"check_class": "content"})
    return judge_model(ctx, c, **kw)  # type: ignore[arg-type]
