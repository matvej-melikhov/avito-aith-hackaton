"""Метрики классификации по строкам рубрики: система против ручной разметки.

Запуск: python prereview/evals/metrics.py [--md]

Каждая строка «работа × критерий» это один пример. Разметка в labels/<slug>.json,
результаты системы в results/<slug>__<работа>[__sandbox].json (формат журнала прогона
с полем criteria или формат evals с полем rows). Строки, где система воздержалась
(нужен человек, не проверено), считаются отдельно: в «мягком» варианте они исключены,
в «строгом» воздержание считается сигналом «требует внимания».

Три взгляда на одну и ту же таблицу:
- «выполнено полностью»: положительный класс это полный балл по строке;
- «балл выше нуля»: положительный класс это любой ненулевой балл;
- «требует внимания» (продуктовый): положительный класс это строка, где по разметке
  балл ниже максимума; система «сигналит», если предложила меньше максимума или воздержалась.
  Здесь recall это доля проблемных строк, которые система не пропустила, а precision это
  доля сигналов, которые оказались по делу.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = HERE / "labels"
RESULTS = HERE / "results"
ASSIGNMENTS = HERE.parent / "assignments"
EPS = 1e-9


def load_rows(slug: str, work: str) -> list[dict] | None:
    for name in (f"{slug}__{work}__sandbox.json", f"{slug}__{work}.json"):
        p = RESULTS / name
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            rows = d.get("criteria") or d.get("rows") or []
            return [{"key": r["key"], "points": r.get("points"), "max": float(r["max"]),
                     "class": r.get("class", "content"), "status": r.get("status")} for r in rows]
    return None


def judgement_keys(slug: str) -> set[str]:
    a = json.loads((ASSIGNMENTS / f"{slug}.json").read_text(encoding="utf-8"))
    return {k for k, v in a["criteria"].items() if v.get("check_class") == "judgement"}


def prf(tp: int, fp: int, fn: int, tn: int) -> dict:
    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / total if total else 0.0
    return {"n": total, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "accuracy": accuracy,
            "precision": precision, "recall": recall, "f1": f1}


def confusion(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for t, p in pairs if t and p)
    fp = sum(1 for t, p in pairs if not t and p)
    fn = sum(1 for t, p in pairs if t and not p)
    tn = sum(1 for t, p in pairs if not t and not p)
    return prf(tp, fp, fn, tn)


def collect(slug: str, *, exclude_judgement: bool) -> dict:
    labels = json.loads((LABELS / f"{slug}.json").read_text(encoding="utf-8"))
    skip = judgement_keys(slug) if exclude_judgement else set()
    examples: list[dict] = []
    works: list[str] = []
    for work, marks in labels.items():
        if work.startswith("_"):
            continue
        rows = load_rows(slug, work)
        if rows is None:
            continue
        works.append(work)
        by_key = {r["key"]: r for r in rows}
        for key, label in marks.items():
            if key in skip or key not in by_key or not isinstance(label, (int, float)):
                continue
            r = by_key[key]
            examples.append({"label": float(label), "points": r["points"], "max": r["max"]})
    decided = [e for e in examples if e["points"] is not None]
    abstained = len(examples) - len(decided)
    exact = sum(1 for e in decided if abs(e["label"] - e["points"]) < EPS)
    half = sum(1 for e in decided if abs(e["label"] - e["points"]) <= 0.5 + EPS)
    full = confusion([(e["label"] >= e["max"] - EPS, e["points"] >= e["max"] - EPS) for e in decided])
    nonzero = confusion([(e["label"] > EPS, e["points"] > EPS) for e in decided])
    attention_soft = confusion([(e["label"] < e["max"] - EPS, e["points"] < e["max"] - EPS) for e in decided])
    attention_strict = confusion([(e["label"] < e["max"] - EPS, e["points"] is None or e["points"] < e["max"] - EPS)
                                  for e in examples])
    return {"slug": slug, "works": works, "n": len(examples), "abstained": abstained, "decided": len(decided),
            "exact": exact, "half": half, "full": full, "nonzero": nonzero,
            "attention_soft": attention_soft, "attention_strict": attention_strict}


def pct(x: float) -> str:
    return f"{100 * x:.0f} %"


def render(md: bool) -> str:
    out: list[str] = []
    for slug in ("system_design_lab1", "go_tasks_1_3"):
        for exclude in (False, True):
            c = collect(slug, exclude_judgement=exclude)
            if not c["works"]:
                continue
            title = f"{slug}, работы: {', '.join(c['works'])}" + (", без оценочных строк" if exclude else ", все строки")
            out.append(f"\n### {title}\n" if md else f"\n== {title}")
            out.append(f"Строк {c['n']}, система воздержалась на {c['abstained']}, точное совпадение балла "
                       f"{c['exact']} из {c['decided']}, в пределах полубалла {c['half']} из {c['decided']}.\n")
            head = "| Взгляд | n | TP | FP | FN | TN | accuracy | precision | recall | F1 |\n|---|---|---|---|---|---|---|---|---|---|"
            out.append(head)
            for name, m in (("выполнено полностью (без воздержаний)", c["full"]),
                            ("балл выше нуля (без воздержаний)", c["nonzero"]),
                            ("требует внимания (без воздержаний)", c["attention_soft"]),
                            ("требует внимания (воздержание = сигнал)", c["attention_strict"])):
                out.append(f"| {name} | {m['n']} | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} | {pct(m['accuracy'])} | "
                           f"{pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f1'])} |")
    return "\n".join(out)


if __name__ == "__main__":
    print(render(md="--md" in sys.argv))
