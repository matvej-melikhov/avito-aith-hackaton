"""Оркестратор одного прогона: снимок → Work → проверки → судья → сигнал → черновик → результат.

Порядок стадий и правила из docs/PIPELINE.md. Любая стадия, кроме получения снимка,
отказывает частично: критерий получает not_checked, а не балл.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from prereview import __version__
from prereview.artifact.extract import ExtractionError, UnsupportedFormat, extract
from prereview.artifact.fetch import ArtifactError, fetch_artifact
from prereview.artifact.model import Work
from prereview.artifact.ocr import apply_ocr
from prereview.artifact.redact import RedactionReport, redact_work
from prereview.checks.gobuild import go_build, is_go_project
from prereview.checks.run import facts_for_prompt, run_checks
from prereview.checks.runtime import RuntimeReport, apply_runtime, run_runtime
from prereview.config import Settings
from prereview.contracts import (
    AssistEvidence,
    PublicCriterion,
    ReviewAssistRequest,
    ReviewAssistResult,
    ReviewCriterionView,
    ReviewerSuggestion,
    SelfReviewFinding,
    SelfReviewRequest,
    SelfReviewResult,
    validate_assist_result,
    validate_self_review_result,
)
from prereview.feedback import draft_feedback, results_text, reviewer_summary
from prereview.judge.context import JudgeContext
from prereview.judge.criterion import CriterionResult, judge, judge_formal, judge_model, observe
from prereview.judge.explore import EvidencePack, HarnessExplorer
from prereview.judge.self_review import SelfResult, self_check, summarize
from prereview.llm.client import LLMClient, make_client
from prereview.llm.ledger import Ledger, Pricing
from prereview.llm.prompts import PromptStore
from prereview.rubric.load import find_assignment
from prereview.rubric.model import AssignmentFile, Criterion, build_rubric
from prereview.signal.report import build_signal

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class RunRecord:
    run_id: str
    attempt: int
    purpose: str
    started_at: str
    finished_at: str = ""
    model: str = ""
    pipeline_version: str = __version__
    prompt_versions: dict = field(default_factory=dict)
    assignment: str | None = None
    work: dict = field(default_factory=dict)
    redaction: dict = field(default_factory=dict)
    harness: dict = field(default_factory=dict)
    build: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)
    criteria: list[dict] = field(default_factory=list)
    signal: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__

    def save(self, data_dir: Path) -> Path:
        runs = data_dir / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        path = runs / f"{self.run_id}-{self.attempt}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return path


@dataclass
class Prepared:
    work: Work
    clean: Work
    redaction: RedactionReport
    assignment: AssignmentFile | None
    rubric: list[Criterion]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_work_bytes(artifact_bytes: bytes | None, url: str, digest: str, media_type: str,
                    settings: Settings) -> tuple[Work, bytes]:
    try:
        data = artifact_bytes if artifact_bytes is not None else fetch_artifact(
            url, digest, max_bytes=settings.max_artifact_bytes, rewrites=settings.url_rewrites)
    except ArtifactError as e:
        raise PipelineError(e.code, str(e)) from e
    try:
        return extract(data, media_type, max_file_bytes=settings.max_file_bytes), data
    except UnsupportedFormat as e:
        raise PipelineError("unsupported_format", str(e)) from e
    except ExtractionError as e:
        raise PipelineError("invalid_artifact", str(e)) from e


def load_work(artifact_bytes: bytes | None, url: str, digest: str, media_type: str, settings: Settings) -> Work:
    return load_work_bytes(artifact_bytes, url, digest, media_type, settings)[0]


def prepare(work: Work, criteria: list[ReviewCriterionView] | list[PublicCriterion], settings: Settings,
            assignment_slug: str | None = None) -> Prepared:
    clean, redaction = redact_work(work)
    assignment = find_assignment(settings.assignments_dir, assignment_slug or settings.assignment,
                                 [c.key for c in criteria])
    rubric = build_rubric(criteria, assignment)
    return Prepared(work, clean, redaction, assignment, rubric)


def _assignment_text(student_text: str, assignment: AssignmentFile | None) -> str:
    text = (student_text or "").strip()
    if assignment and assignment.student_text_hint:
        text = (text + "\n\n" + assignment.student_text_hint).strip()
    return text[:12000]


def _suggestion(r: CriterionResult, extra_note: str = "") -> ReviewerSuggestion:
    c = r.criterion
    sources = [
        AssistEvidence(quote=v.quote[:10000], locator=f"{v.path}:{v.line_start}-{v.line_end}", path=v.path,
                       line_start=v.line_start, line_end=max(v.line_end, v.line_start))
        for v in r.verified[:20]
    ]
    note = " ".join(x for x in [r.reviewer_note, extra_note] if x).strip() or None
    requirement_met = r.requirement_met if (c.evaluate_quality or r.requirement_met is not None) else None
    if r.status == "not_checked":
        requirement_met = None
    return ReviewerSuggestion(
        requirement_met=requirement_met,
        sources=sources,
        criterion_id=c.id,
        status=r.status,  # type: ignore[arg-type]
        proposed_points=r.proposed_points if r.status != "not_checked" else None,
        reason=(r.reason or "Без обоснования.")[:10000],
        evidence=r.evidence_lines[:50],
        confidence=r.confidence,  # type: ignore[arg-type]
        reviewer_note=note[:10000] if note else None,
        student_feedback=(r.student_feedback or None),
    )


def run_review_assist(request: ReviewAssistRequest, settings: Settings, *, client: LLMClient | None = None,
                      artifact_bytes: bytes | None = None, assignment_slug: str | None = None,
                      save: bool = True) -> tuple[ReviewAssistResult, RunRecord]:
    started = time.time()
    record = RunRecord(str(request.run_id), request.attempt, "reviewer_assist", _now())
    pricing = Pricing.load(settings.pricing_file, settings.usd_rub)
    ledger = Ledger(pricing)
    client = client or make_client(settings, ledger)
    prompts = PromptStore(settings.prompts_dir)
    record.model = client.model
    record.prompt_versions = prompts.versions()

    work, data = load_work_bytes(artifact_bytes, request.artifact_url, request.artifact_digest, request.media_type, settings)
    if "needs_vision" in work.flags:
        work = apply_ocr(work, data, settings, ledger)
    prep = prepare(work, request.criteria, settings, assignment_slug)
    record.assignment = prep.assignment.slug if prep.assignment else None
    record.work = {k: v for k, v in work.meta.items() if k != "skipped"} | {"format": work.format, "flags": work.flags,
                                                                             "skipped": len(work.meta.get("skipped", []))}
    record.redaction = prep.redaction.summary()
    record.flags = list(prep.clean.flags)

    assignment_text = _assignment_text(request.student_text, prep.assignment)
    ctx = JudgeContext(settings, client, prompts, prep.clean, assignment_text, request.reviewer_guidance or "")

    # Формальные проверки: для formal-критериев это вердикт, для остальных факты в промпт.
    reports = {c.key: run_checks(prep.clean, c) for c in prep.rubric if c.checks}
    ctx.facts_text = facts_for_prompt(reports, {c.key for c in prep.rubric if c.is_formal})
    # Go-снимок собираем и прогоняем vet: несобирающийся код ревьюер ловит запуском.
    # Сборка и песочница работают по исходному тексту: замена секретов плейсхолдерами нужна только
    # модели, а в коде она ломает синтаксис (например, `Password = cfg.DBPassword`).
    build = None
    if settings.go_build_enabled and is_go_project(prep.work):
        build = go_build(prep.work, timeout=settings.go_build_timeout_seconds, settings=settings)
        record.build = build.to_dict()
        ctx.facts_text = build.facts() + "\n\n" + ctx.facts_text
        if build.build_ok is False:
            record.flags.append("build_failed")

    # Песочница: собранный бинарник гоняется по сценариям задания параллельно с Harness,
    # факты запуска попадают в промпт судьи и решают строки рубрики о поведении сервиса.
    runtime: RuntimeReport | None = None
    runtime_pool: ThreadPoolExecutor | None = None
    runtime_future = None
    if (build is not None and build.build_ok and prep.assignment and prep.assignment.runtime
            and settings.runtime_enabled and settings.runner_url):
        runtime_pool = ThreadPoolExecutor(max_workers=1)
        runtime_future = runtime_pool.submit(run_runtime, prep.work, prep.assignment.runtime, settings)

    def collect_runtime() -> RuntimeReport | None:
        nonlocal runtime
        if runtime_future is None or runtime is not None:
            return runtime
        try:
            runtime = runtime_future.result()
        except Exception as e:  # noqa: BLE001 — песочница не должна ронять проверку
            runtime = RuntimeReport(available=True, error=f"{type(e).__name__}: {str(e)[:200]}")
        finally:
            if runtime_pool is not None:
                runtime_pool.shutdown(wait=False)
        record.runtime = runtime.to_dict()
        if runtime.error:
            record.errors.append("runtime: " + runtime.error)
        if runtime.ran:
            record.flags.append("runtime_checked")
        facts = runtime.facts()
        if facts:
            ctx.facts_text = facts + "\n\n" + ctx.facts_text
        return runtime

    results: list[CriterionResult] = []
    if "needs_vision" in prep.clean.flags or "no_text_files" in prep.clean.flags:
        why = ("В файле нет текстового слоя (скан или экспорт доски), автоматическая проверка недоступна."
               if "needs_vision" in prep.clean.flags else "В снимке нет текстовых файлов.")
        for c in prep.rubric:
            results.append(CriterionResult(c, "not_checked", None, "low", why, reviewer_note="Проверьте работу вручную.",
                                           path="not_checked", flags=["needs_vision"]))
        pack = EvidencePack()
    else:
        pack = EvidencePack()
        model_criteria = [c for c in prep.rubric if not c.is_formal]
        if prep.clean.is_repo and model_criteria:
            explorer = HarnessExplorer(settings, ledger, prompts)
            pack = explorer.explore(prep.clean, model_criteria, assignment_text)
            if pack.error:
                record.errors.append(f"harness: {pack.error}")
        record.harness = pack.to_dict()
        collect_runtime()

        def one(c: Criterion) -> CriterionResult:
            if c.is_formal:
                return judge_formal(ctx, c)
            if prep.clean.is_repo:
                files = pack.files_for(c.key)
                # Подсказки scope из файла задания добавляются к найденному Harness:
                # оценочные строки («тонкие handlers») требуют видеть весь слой, а не одну цитату.
                if c.scope:
                    import fnmatch

                    for f in prep.clean.files:
                        if any(fnmatch.fnmatch(f.path, g) for g in c.scope) and f.path not in files:
                            files.append(f.path)
                if files:
                    text, cut = ctx.slice(files)
                    r = judge_model(ctx, c, work_text=text, evidence_pack=pack.text_for(c.key), condensed=bool(cut))
                else:
                    r = judge_model(ctx, c, evidence_pack=pack.text_for(c.key))
            else:
                r = judge(ctx, c)
            if c.check_class == "judgement" and r.status != "not_checked":
                r.observations = observe(ctx, c, work_text=None if not prep.clean.is_repo else ctx.work_text)
                if r.observations:
                    obs = "; ".join(f"{o['text']} ({', '.join(o['evidence'])})" if o["evidence"] else o["text"]
                                    for o in r.observations)
                    r.reviewer_note = (r.reviewer_note + " Наблюдения: " + obs).strip()
            return r

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, prep.rubric))

    collect_runtime()
    if runtime is not None and runtime.ran:
        results = [apply_runtime(r, runtime) for r in results]

    # Сигнал об ИИ: вне баллов.
    meta = {"student_comment": "", "injection_hits": prep.redaction.injection_hits}
    signal, signal_record = build_signal(prep.clean, meta, client=client, prompts=prompts,
                                         results_text=results_text(results), assignment_text=assignment_text)
    record.signal = signal_record

    feedback = draft_feedback(client, prompts, results) if results else None
    build_note = ""
    if "ocr_transcribed" in prep.clean.flags:
        build_note = f"PDF без текстового слоя распознан моделью ({prep.clean.meta.get('ocr_pages')} стр.), цитаты относятся к распознанному тексту. "
    if build is not None and build.build_ok is not None:
        build_note = ("Сборка go build: успешно. " if build.build_ok else "Сборка go build НЕ ПРОХОДИТ: " + build.build_output.strip().splitlines()[-1][:160] + ". ")
        if build.vet_ok is False:
            build_note += "go vet с замечаниями. "
    if runtime is not None:
        build_note += runtime.summary() if runtime.ran else f"Запуск в песочнице не выполнен: {runtime.note or runtime.error}. "
    summary = build_note + reviewer_summary(results, signal_record.get("level", "low"), ledger.cost_rub,
                               ledger.prompt_tokens + ledger.completion_tokens, record.prompt_versions,
                               client.model, pack.source if pack.source != "none" else ("нет: " + (pack.error or "не нужен")))
    suggestions = [_suggestion(r, summary if i == 0 else "") for i, r in enumerate(results)]
    result = ReviewAssistResult(authorship_signal=signal, feedback_draft=feedback, suggestions=suggestions)
    problems = validate_assist_result(result, request.criteria)
    if problems:
        record.errors.append("validation: " + "; ".join(problems))
        result = _repair(result, request.criteria)
        problems = validate_assist_result(result, request.criteria)
        if problems:
            raise PipelineError("invalid_result", "; ".join(problems))

    record.criteria = [
        {"key": r.criterion.key, "class": r.criterion.check_class, "class_source": r.criterion.class_source,
         "status": r.status, "points": r.proposed_points, "max": r.criterion.max_points, "confidence": r.confidence,
         "verdict": r.verdict, "path": r.path, "verified": len(r.verified), "dropped": len(r.dropped),
         "flags": r.flags, "reason": r.reason, "repeats": r.repeats, "runtime": r.runtime_lines}
        for r in results
    ]
    record.ledger = ledger.summary()
    record.elapsed_seconds = round(time.time() - started, 1)
    record.finished_at = _now()
    if save:
        record.save(settings.data_dir)
    return result, record


def _repair(result: ReviewAssistResult, criteria: list[ReviewCriterionView]) -> ReviewAssistResult:
    """Правим то, что бэкенд отверг бы: лишние или нарушающие правила строки → not_checked."""
    by_id = {c.id: c for c in criteria}
    fixed: list[ReviewerSuggestion] = []
    seen: set = set()
    for s in result.suggestions:
        if s.criterion_id not in by_id or s.criterion_id in seen:
            continue
        seen.add(s.criterion_id)
        c = by_id[s.criterion_id]
        bad = (s.proposed_points is not None and s.proposed_points > c.max_points) or \
              (s.status == "not_checked" and s.proposed_points is not None) or \
              (c.evaluate_quality and s.proposed_points is not None and s.requirement_met is None) or \
              (s.requirement_met is False and (s.proposed_points or 0) != 0)
        if bad:
            s = s.model_copy(update={"status": "not_checked", "proposed_points": None, "requirement_met": None,
                                     "reason": "Результат не прошёл проверку правил и снят: " + s.reason})
        fixed.append(s)
    for cid, c in by_id.items():
        if cid not in seen:
            fixed.append(ReviewerSuggestion(criterion_id=cid, status="not_checked", proposed_points=None,
                                            reason="Критерий не был проверен.", confidence="low"))
    return result.model_copy(update={"suggestions": fixed})


def run_self_review(request: SelfReviewRequest, settings: Settings, *, client: LLMClient | None = None,
                    artifact_bytes: bytes | None = None, assignment_slug: str | None = None,
                    save: bool = True) -> tuple[SelfReviewResult, RunRecord]:
    started = time.time()
    record = RunRecord(str(request.run_id), request.attempt, "student_self_review", _now())
    pricing = Pricing.load(settings.pricing_file, settings.usd_rub)
    ledger = Ledger(pricing)
    client = client or make_client(settings, ledger)
    prompts = PromptStore(settings.prompts_dir)
    record.model = client.model
    record.prompt_versions = prompts.versions()

    work, data = load_work_bytes(artifact_bytes, request.artifact_url, request.artifact_digest, request.media_type, settings)
    if "needs_vision" in work.flags:
        work = apply_ocr(work, data, settings, ledger)
    prep = prepare(work, request.criteria, settings, assignment_slug)
    record.assignment = prep.assignment.slug if prep.assignment else None
    record.work = {"format": work.format, "flags": work.flags, "chars": work.meta.get("chars")}
    record.redaction = prep.redaction.summary()
    # Самопроверка не получает закрытых указаний и эталона: только текст задания.
    ctx = JudgeContext(settings, client, prompts, prep.clean, (request.student_text or "")[:12000], "")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results: list[SelfResult] = list(pool.map(lambda c: self_check(ctx, c), prep.rubric))
    # Студент получает итог и грубые статусы без цитат и адресов: самопроверка это
    # проверка полноты, а не оракул оценки. Итог едет в первой строке результата.
    summary = summarize(ctx, results) if any(r.status != "not_checked" for r in results) else ""
    findings = []
    for i, r in enumerate(results):
        feedback = r.feedback[:1000]
        if i == 0 and summary:
            feedback = summary + "\n" + feedback
        findings.append(SelfReviewFinding(criterion_id=r.criterion.id, status=r.status,  # type: ignore[arg-type]
                                          feedback=feedback[:10000], evidence=""))
    result = SelfReviewResult(findings=findings)
    problems = validate_self_review_result(result, request.criteria)
    if any("не совпадает" in p or "дубликат" in p for p in problems):
        raise PipelineError("invalid_result", "; ".join(problems))
    record.errors += problems
    record.criteria = [{"key": r.criterion.key, "status": r.status, "verified": len(r.verified),
                        "class": r.criterion.check_class} for r in results]
    record.flags.append("self_review_summary" if summary else "self_review_no_summary")
    record.ledger = ledger.summary()
    record.elapsed_seconds = round(time.time() - started, 1)
    record.finished_at = _now()
    if save:
        record.save(settings.data_dir)
    return result, record


def new_run_id() -> uuid.UUID:
    return uuid.uuid4()
