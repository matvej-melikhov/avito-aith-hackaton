"""CLI: локальная проверка, сервис, проверка Harness, evals."""

from __future__ import annotations

import io
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path

import typer

from prereview.artifact.extract import MEDIA_DOCX, MEDIA_MARKDOWN, MEDIA_PDF, MEDIA_ZIP
from prereview.artifact.fetch import digest_of
from prereview.config import get_settings
from prereview.contracts import (
    PublicCriterion,
    ReviewAssistRequest,
    ReviewCriterionView,
    SelfReviewRequest,
)
from prereview.rubric.load import load_assignment
from prereview.rubric.model import criteria_from_assignment

app = typer.Typer(help="prereview: ядро ИИ-проверки домашних работ", no_args_is_help=True)

MEDIA_BY_EXT = {".md": MEDIA_MARKDOWN, ".markdown": MEDIA_MARKDOWN, ".txt": "text/plain",
                ".docx": MEDIA_DOCX, ".pdf": MEDIA_PDF, ".zip": MEDIA_ZIP}


def read_artifact(path: Path) -> tuple[bytes, str]:
    if path.is_dir():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(path.rglob("*")):
                if f.is_file() and ".git" not in f.parts:
                    z.write(f, f"{path.name}-snapshot/{f.relative_to(path).as_posix()}")
        return buf.getvalue(), MEDIA_ZIP
    media = MEDIA_BY_EXT.get(path.suffix.lower())
    if not media:
        raise typer.BadParameter(f"неизвестное расширение {path.suffix}")
    return path.read_bytes(), media


def load_criteria(criteria_file: Path | None, assignment: str | None) -> list[ReviewCriterionView]:
    if criteria_file:
        raw = json.loads(criteria_file.read_text(encoding="utf-8"))
        items = raw["criteria"] if isinstance(raw, dict) else raw
        return [ReviewCriterionView.model_validate(x) for x in items]
    if assignment:
        settings = get_settings()
        return criteria_from_assignment(load_assignment(settings.assignments_dir / f"{assignment}.json"))
    raise typer.BadParameter("нужен --criteria или --assignment")


def table(result, criteria: list[ReviewCriterionView]) -> str:
    by_id = {c.id: c for c in criteria}
    rows = ["| Критерий | Статус | Балл | Увер. | Цитата | Почему |", "|---|---|---|---|---|---|"]
    total = 0.0
    for s in result.suggestions:
        c = by_id[s.criterion_id]
        pts = "—" if s.proposed_points is None else f"{s.proposed_points:g}"
        total += s.proposed_points or 0
        src = s.sources[0] if s.sources else None
        quote = f"`{src.path}:{src.line_start}` {src.quote[:60].replace(chr(10), ' ')!r}" if src else "—"
        rows.append(f"| {c.title[:50]} | {s.status} | {pts} / {c.max_points:g} | {s.confidence} | {quote} | {s.reason[:110].replace('|', '/')} |")
    rows.append(f"\n**Итого предложено:** {total:g} из {sum(c.max_points for c in criteria):g}")
    return "\n".join(rows)


@app.command()
def grade(
    path: Path = typer.Argument(..., help="файл md/docx/pdf/zip или каталог репозитория"),
    assignment: str | None = typer.Option(None, "--assignment", "-a", help="slug файла задания из assignments/"),
    criteria: Path | None = typer.Option(None, "--criteria", help="JSON с критериями в формате контракта"),
    purpose: str = typer.Option("reviewer", "--purpose", help="reviewer | self"),
    out: Path | None = typer.Option(None, "--out", "-o", help="куда сохранить JSON результата"),
    fake: bool = typer.Option(False, "--fake", help="заглушка модели вместо DeepSeek"),
    student_text: str = typer.Option("", "--student-text", help="текст задания для студента"),
    no_harness: bool = typer.Option(False, "--no-harness", help="не звать DeepSeek Harness"),
) -> None:
    """Проверить одну работу и напечатать таблицу по критериям."""
    from prereview.pipeline import run_review_assist, run_self_review

    settings = get_settings()
    if fake:
        settings = settings.model_copy(update={"llm_provider": "fake"})
    if no_harness:
        settings = settings.model_copy(update={"harness_enabled": False})
    data, media = read_artifact(path)
    crit = load_criteria(criteria, assignment)
    run_id = uuid.uuid4()
    common = dict(run_id=run_id, attempt=1, input_fingerprint="sha256:" + "0" * 64, artifact_id=uuid.uuid4(),
                  artifact_url=f"file://{path.resolve()}", artifact_digest=digest_of(data), media_type=media,
                  student_text=student_text)
    if purpose == "self":
        req = SelfReviewRequest(criteria=[PublicCriterion(**c.model_dump(include={"id", "key", "title", "max_points"})) for c in crit], **common)
        result, record = run_self_review(req, settings, artifact_bytes=data, assignment_slug=assignment)
        for f in result.findings:
            c = next(x for x in crit if x.id == f.criterion_id)
            typer.echo(f"- {c.title}: {f.status}. {f.feedback} [{f.evidence}]")
    else:
        req = ReviewAssistRequest(review_iteration_id=uuid.uuid4(), criteria=crit, reviewer_guidance="", reference_url=None, **common)
        result, record = run_review_assist(req, settings, artifact_bytes=data, assignment_slug=assignment)
        typer.echo(table(result, crit))
        if result.authorship_signal:
            typer.echo("\n**Сигнал ИИ:** " + result.authorship_signal.explanation.replace("\n", "\n> "))
        if result.feedback_draft:
            typer.echo("\n**Черновик фидбека:**\n" + result.feedback_draft)
    led = record.ledger
    typer.echo(f"\nСтоимость: {led.get('cost_rub', 0):.2f} ₽ ({led.get('cost_usd', 0):.4f} $), токены: {led.get('prompt_tokens', 0)}+{led.get('completion_tokens', 0)}, "
               f"вызовов: {led.get('calls', 0)}, время: {record.elapsed_seconds} с, модель: {record.model}, harness: {record.harness.get('source', '—')}")
    if record.errors:
        typer.echo("Ошибки: " + "; ".join(record.errors))
    if out:
        out.write_text(json.dumps({"request": req.model_dump(mode="json"), "result": result.model_dump(mode="json"),
                                   "record": record.to_dict()}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        typer.echo(f"JSON: {out}")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8100, reload: bool = False) -> None:
    """Поднять HTTP-сервис по контракту workspace v2."""
    import uvicorn

    uvicorn.run("prereview.service.app:app", host=host, port=port, reload=reload)


@app.command("harness-check")
def harness_check(path: Path = typer.Argument(None, help="каталог репозитория для пробы")) -> None:
    """Проверить, что DeepSeek Harness запускается в режиме только чтения."""
    from prereview.artifact.extract import extract
    from prereview.judge.explore import HarnessExplorer
    from prereview.llm.ledger import Ledger, Pricing
    from prereview.llm.prompts import PromptStore
    from prereview.rubric.model import Criterion

    settings = get_settings()
    ledger = Ledger(Pricing.load(settings.pricing_file, settings.usd_rub))
    explorer = HarnessExplorer(settings, ledger, PromptStore(settings.prompts_dir))
    typer.echo(f"dsh доступен: {explorer.available()} ({settings.harness_bin})")
    if path is None:
        return
    data, media = read_artifact(path)
    work = extract(data, media)
    crit = [Criterion(id=uuid.uuid4(), key="go_mod", title="Есть файл go.mod", max_points=1),
            Criterion(id=uuid.uuid4(), key="entrypoint", title="Точка входа в cmd/<service>/main.go", max_points=1)]
    pack = explorer.explore(work, crit, "Проверка структуры Go-проекта.")
    typer.echo(json.dumps(pack.to_dict(), ensure_ascii=False, indent=1))
    typer.echo(ledger.summary())


@app.command()
def evals(slug: str = typer.Option("all", help="system_design_lab1 | go_tasks_1_3 | all"),
          fake: bool = typer.Option(False, "--fake"), repeats: int = typer.Option(1, "--repeats")) -> None:
    """Прогнать корпус hw_examples и собрать отчёт docs/EVALS.md."""
    from prereview.evals import run_evals

    run_evals(slug, fake=fake, repeats=repeats)


if __name__ == "__main__":
    app()
