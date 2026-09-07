#!/usr/bin/env python3
"""Пайплайн оценки: рубрика -> доказательства -> сравнение -> ранжирование.

  python3 docs/archive/grading-experiment/grader/run.py docs/archive/grading-experiment/grader/blind/<task_dir>            # структурный протокол
  python3 docs/archive/grading-experiment/grader/run.py docs/archive/grading-experiment/grader/blind/<task_dir> --naive    # baseline
"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompts
from audit import length_bias, position_agreement
from inventory import inventory, render
from llm import chat_json

CAP = 45_000   # на одну работу в промпте

def load(task_dir):
    cond = open(os.path.join(task_dir, "CONDITION.txt")).read()
    sols = {}
    for p in sorted(glob.glob(os.path.join(task_dir, "solution_*.txt"))):
        sid = os.path.basename(p).split("_")[1][0]
        sols[sid] = open(p).read()
    return cond, sols

def build_rubric(cond, cache):
    if os.path.exists(cache):
        return json.load(open(cache))
    r = chat_json(prompts.RUBRIC_SYS, prompts.RUBRIC_USER.format(condition=cond[:60_000]))
    json.dump(r, open(cache, "w"), ensure_ascii=False, indent=1)
    return r

def rubric_text(r):
    out = [f"Задание: {r.get('task_title','')}  (всего {r.get('total_points','?')} баллов)"]
    for c in r["criteria"]:
        out.append(f"[{c['id']}] ({c['max_points']} б., {c['kind']}, подтверждается: "
                   f"{'/'.join(c.get('evidence_type',['text']))}) {c['text']}\n"
                   f"    полностью: {c.get('full_credit','')}\n"
                   f"    частично:  {c.get('partial_credit','')}")
    return "\n".join(out)

def run_task(task_dir, naive=False, code_facts=""):
    cond, sols = load(task_dir)
    name = os.path.basename(task_dir)

    # работы без текстового слоя (скан, доска Miro) нельзя оценивать текстовой моделью:
    # они получат 0 и уедут в конец рейтинга просто потому, что их не прочитали
    needs_vision = [sid for sid, s in sols.items() if "[[NEEDS_VISION]]" in s]

    if naive:
        scores = {}
        for sid, s in sols.items():
            r = chat_json(prompts.NAIVE_SYS,
                          prompts.NAIVE_USER.format(condition=cond[:30_000], solution=s[:CAP]))
            scores[sid] = r
        ranking = sorted(scores, key=lambda s: -scores[s]["score"])
        return {"task": name, "mode": "naive", "scores": scores, "ranking": ranking}

    rub = build_rubric(cond, os.path.join(task_dir, "_rubric.json"))
    rt = rubric_text(rub)

    ev = {}
    for sid, s in sols.items():
        inv = inventory(s)
        ev[sid] = chat_json(prompts.EVIDENCE_SYS, prompts.EVIDENCE_USER.format(
            rubric=rt, inventory=render(inv), solution=s[:CAP]))
        ev[sid]["_inventory"] = inv

    maxpts = {c["id"]: float(c.get("max_points") or 0) for c in rub["criteria"]}

    def adjudicate(order):
        block = [f"--- РАБОТА {sid} ---\n" + json.dumps(ev[sid], ensure_ascii=False, indent=1)
                 for sid in order]
        v = chat_json(prompts.COMPARE_SYS, prompts.COMPARE_USER.format(
            rubric=rt, evidence_block="\n".join(block),
            code_facts=("\n" + code_facts + "\n") if code_facts else ""),
            max_tokens=6000)
        totals = {sid: 0.0 for sid in sols}
        for c in v.get("per_criterion", []):
            cap = maxpts.get(c.get("id"), float("inf"))
            for sid, val in c.get("scores", {}).items():
                if sid in totals:
                    # модель иногда выходит за максимум критерия — подрезаем по рубрике
                    totals[sid] += min(max(float(val or 0), 0.0), cap)
        v["totals"] = totals
        if not v.get("ranking"):
            v["ranking"] = sorted(totals, key=lambda s: -totals[s])
        v["ranking"] = [s for s in v["ranking"] if s in sols]
        return v

    # два прогона с обратным порядком подачи — контроль позиционного смещения
    order = sorted(sols)
    v1 = adjudicate(order)
    v2 = adjudicate(list(reversed(order)))

    lengths = {sid: len(s) for sid, s in sols.items()}
    merged = {sid: (v1["totals"].get(sid, 0) + v2["totals"].get(sid, 0)) / 2 for sid in sols}
    ranking = sorted(merged, key=lambda s: -merged[s])

    verdict = {
        "ranking": ranking,
        "totals": merged,
        "per_criterion": v1.get("per_criterion", []),
        "ranking_rationale": v1.get("ranking_rationale", ""),
        "audit": {
            "position": position_agreement(v1["ranking"], v2["ranking"]),
            "length": length_bias(merged, lengths),
        },
    }
    if needs_vision:
        verdict["needs_vision"] = needs_vision
        verdict.setdefault("audit", {})["vision"] = {
            "solutions": needs_vision,
            "warning": ("Эти работы без текстового слоя. Их место в рейтинге "
                        "недостоверно, пока не пройден vision-проход."),
        }
    return {"task": name, "mode": "structured", "rubric": rub, "evidence": ev,
            "verdict": verdict, "passes": {"forward": v1, "reversed": v2}}

if __name__ == "__main__":
    td = sys.argv[1]
    naive = "--naive" in sys.argv
    res = run_task(td, naive)
    out = os.path.join(td, "_result_naive.json" if naive else "_result.json")
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"task": res["task"], "mode": res["mode"],
                      "ranking": res.get("ranking") or res["verdict"]["ranking"]},
                     ensure_ascii=False))
