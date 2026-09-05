"""Прогон формальных проверок критерия и сводка по классу B."""

from __future__ import annotations

from dataclasses import dataclass, field

from prereview.artifact.model import Work
from prereview.checks.primitives import PRIMITIVES, CheckOutcome, Evidence
from prereview.rubric.model import CheckSpec, Criterion


@dataclass
class CheckReport:
    status: str  # pass | fail | na | error
    outcomes: list[tuple[CheckSpec, CheckOutcome]] = field(default_factory=list)

    @property
    def evidence(self) -> list[Evidence]:
        out: list[Evidence] = []
        for _, o in self.outcomes:
            out.extend(o.evidence)
        return out

    def note(self) -> str:
        return "; ".join(f"{spec.kind}: {o.note}" for spec, o in self.outcomes)


def run_check(work: Work, spec: CheckSpec) -> CheckOutcome:
    fn = PRIMITIVES.get(spec.kind)
    if fn is None:
        return CheckOutcome("error", f"неизвестная проверка {spec.kind}")
    params = {k: v for k, v in spec.model_dump().items() if k not in {"kind", "note"}}
    try:
        return fn(work, **params)
    except Exception as e:  # noqa: BLE001 — ошибка примитива не провал строки
        return CheckOutcome("error", f"{spec.kind}: {type(e).__name__}: {str(e)[:120]}")


def run_checks(work: Work, criterion: Criterion) -> CheckReport:
    outcomes = [(spec, run_check(work, spec)) for spec in criterion.checks]
    statuses = [o.status for _, o in outcomes]
    if not statuses:
        status = "na"
    elif "fail" in statuses:
        status = "fail"
    elif "error" in statuses:
        status = "error"
    elif all(s == "na" for s in statuses):
        status = "na"
    else:
        status = "pass"
    return CheckReport(status, outcomes)


def facts_for_prompt(reports: dict[str, CheckReport], formal_keys: set[str] | None = None) -> str:
    """Факты проверок для промпта судьи.

    Для формальных критериев это вердикт (pass/fail), для остальных только справка:
    счётчики и находки, без слов «провал», чтобы модель не приняла справку за требование.
    """
    formal_keys = formal_keys or set()
    lines = []
    for key, r in reports.items():
        if not r.outcomes:
            continue
        if key in formal_keys:
            lines.append(f"- {key} (формальная проверка, результат {r.status}): {r.note()}")
        else:
            notes = "; ".join(f"{spec.note or spec.kind}: {o.note}" for spec, o in r.outcomes)
            lines.append(f"- {key} (справка, не требование): {notes}")
    if not lines:
        return "Формальные проверки для этого задания не заданы."
    return "ФАКТЫ, ПОСЧИТАННЫЕ КОДОМ (справка для критериев, требование только там, где сказано «формальная проверка»):\n" + "\n".join(lines)
