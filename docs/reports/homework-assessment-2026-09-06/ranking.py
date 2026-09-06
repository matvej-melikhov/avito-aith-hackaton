"""Метрики ранжирования: сохраняет ли система порядок «слабая < средняя < хорошая» внутри задания.

Usage: python3 ranking.py [--variant NAME]

Берёт results{-NAME}.csv (балл системы по каждой паре «задание × работа») и evaluation{-NAME}.json
(референсный класс работы). Внутри одного задания сравниваются все пары работ с разными
референсными классами: пара согласована, если работа с более высоким классом получила больший
балл, рассогласована, если меньший, ничья считается за половину. Доля согласованных пар это
pairwise accuracy, для двух классов она равна AUC. Отдельно считается AUC «слабая против
нормальной» по доле от максимума, собранной по всем заданиям, и число заданий, где порядок
воспроизведён полностью. Средние работы здесь участвуют: для ранжирования их метка информативна,
хотя в классификацию отчёта они не входят.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VARIANT = sys.argv[sys.argv.index("--variant") + 1] if "--variant" in sys.argv else ""
SUFFIX = f"-{VARIANT}" if VARIANT else ""
RANK = {"weak": 0, "medium": 1, "normal": 2}


def load() -> list[dict]:
    rows = list(csv.DictReader((ROOT / f"results{SUFFIX}.csv").open(encoding="utf-8")))
    for r in rows:
        r["score"] = float(r["score"])
        r["max_score"] = float(r["max_score"])
        r["ratio"] = r["score"] / r["max_score"] if r["max_score"] else 0.0
        r["rank"] = RANK[r["reference_class"]]
    return rows


def concordance(pairs: list[tuple[dict, dict]], key: str = "score") -> tuple[float, int, int, int]:
    conc = disc = ties = 0
    for a, b in pairs:
        if a["rank"] == b["rank"]:
            continue
        hi, lo = (a, b) if a["rank"] > b["rank"] else (b, a)
        if hi[key] > lo[key] + 1e-9:
            conc += 1
        elif hi[key] < lo[key] - 1e-9:
            disc += 1
        else:
            ties += 1
    n = conc + disc + ties
    return ((conc + 0.5 * ties) / n if n else float("nan")), conc, disc, ties


def main() -> None:
    rows = load()
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    within: list[tuple[dict, dict]] = []
    perfect = tasks_with_pairs = 0
    per_task: list[tuple[str, float, int]] = []
    for task, items in sorted(by_task.items()):
        pairs = [(a, b) for a, b in combinations(items, 2) if a["rank"] != b["rank"]]
        if not pairs:
            continue
        tasks_with_pairs += 1
        acc, conc, disc, ties = concordance(pairs)
        per_task.append((task, acc, len(pairs)))
        within.extend(pairs)
        if disc == 0 and ties == 0:
            perfect += 1
    overall, conc, disc, ties = concordance(within)
    by_kind = {}
    for name, (lo, hi) in {"слабая < средняя": (0, 1), "средняя < хорошая": (1, 2), "слабая < хорошая": (0, 2)}.items():
        sub = [(a, b) for a, b in within if {a["rank"], b["rank"]} == {lo, hi}]
        by_kind[name] = concordance(sub)
    # AUC слабая против нормальной по доле от максимума, все задания вместе.
    weak = [r for r in rows if r["reference_class"] == "weak"]
    normal = [r for r in rows if r["reference_class"] == "normal"]
    auc_pairs = [(w, n) for w in weak for n in normal]
    auc, _, _, _ = concordance(auc_pairs, key="ratio")
    result = {
        "variant": VARIANT or "original",
        "pairs_within_task": len(within),
        "pairwise_accuracy_within_task": overall,
        "concordant": conc, "discordant": disc, "ties": ties,
        "by_kind": {k: {"pairs": v[1] + v[2] + v[3], "accuracy": v[0], "concordant": v[1], "discordant": v[2], "ties": v[3]}
                    for k, v in by_kind.items()},
        "tasks_with_pairs": tasks_with_pairs, "tasks_perfect_order": perfect,
        "auc_weak_vs_normal_pooled_ratio": auc, "auc_pairs": len(auc_pairs),
        "per_task": [{"task": t, "accuracy": a, "pairs": n} for t, a, n in per_task],
    }
    (ROOT / f"ranking{SUFFIX}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "per_task"}, ensure_ascii=False, indent=2))
    bad = [x for x in per_task if x[1] < 1 - 1e-9]
    if bad:
        print("задания с нарушением порядка:", ", ".join(f"{t} ({a:.2f} по {n} парам)" for t, a, n in bad))


if __name__ == "__main__":
    main()
