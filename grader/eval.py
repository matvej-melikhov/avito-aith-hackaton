#!/usr/bin/env python3
"""Сверка предсказанного ранжирования с ground truth (weak < mid < good)."""
import glob, itertools, json, os, sys

RANK = {"weak": 0, "mid": 1, "good": 2}
KEY = json.load(open("grader/blind_key.json"))

def truth(task, sid):
    return RANK[KEY[f"{task}/{sid}"]]

def metrics(preds):
    """preds: {task: [sid_best, ..., sid_worst]}"""
    pair_ok = pair_n = exact_ok = exact_n = top_ok = bot_ok = 0
    rows = []
    for task, order in sorted(preds.items()):
        pos = {sid: i for i, sid in enumerate(order)}      # 0 = best
        sids = list(order)
        tp = tn = 0
        for a, b in itertools.combinations(sids, 2):
            ta, tb = truth(task, a), truth(task, b)
            if ta == tb:
                continue
            tn += 1
            better = a if ta > tb else b
            if pos[better] < pos[a if better == b else b]:
                tp += 1
        pair_ok += tp; pair_n += tn
        gold = sorted(sids, key=lambda s: -truth(task, s))
        ex = [truth(task, s) for s in order] == [truth(task, s) for s in gold]
        exact_ok += ex; exact_n += 1
        t1 = truth(task, order[0]) == max(truth(task, s) for s in sids)
        b1 = truth(task, order[-1]) == min(truth(task, s) for s in sids)
        top_ok += t1; bot_ok += b1
        rows.append((task, "".join(order), "".join(gold),
                     f"{tp}/{tn}", "Y" if ex else ".", "Y" if t1 else ".", "Y" if b1 else "."))
    return rows, dict(pair_acc=pair_ok / max(pair_n, 1), pairs=f"{pair_ok}/{pair_n}",
                      exact=f"{exact_ok}/{exact_n}", exact_acc=exact_ok / max(exact_n, 1),
                      top1=top_ok / max(exact_n, 1), bottom1=bot_ok / max(exact_n, 1))

def report(name, preds, reveal=False):
    """reveal=False скрывает эталонный порядок: иначе, читая отчёт, оценщик
    (человек или модель) видит ответы и последующие прогоны перестают быть слепыми."""
    rows, m = metrics(preds)
    print(f"\n=========== {name} ===========")
    gold_col = "gold" if reveal else " -- "
    print(f"{'task':<50} {'pred':<5} {gold_col:<5} {'pairs':>6} {'exact':>5} {'top1':>5} {'worst1':>6}")
    for r in rows:
        g = r[2] if reveal else " -- "
        print(f"{r[0][:50]:<50} {r[1]:<5} {g:<5} {r[3]:>6} {r[4]:>5} {r[5]:>5} {r[6]:>6}")
    print(f"\n  pairwise accuracy : {m['pair_acc']:.1%}  ({m['pairs']})      [random = 50%]")
    print(f"  exact ranking     : {m['exact_acc']:.1%}  ({m['exact']})      [random = 17%]")
    print(f"  finds best        : {m['top1']:.1%}                    [random = 33%]")
    print(f"  finds worst       : {m['bottom1']:.1%}                    [random = 33%]")
    return m

def length_baseline():
    preds = {}
    for td in sorted(glob.glob("grader/blind/*")):
        if not os.path.isdir(td): continue
        task = os.path.basename(td)
        sols = {os.path.basename(p).split("_")[1][0]: os.path.getsize(p)
                for p in glob.glob(f"{td}/solution_*.txt")}
        if len(sols) < 2: continue
        preds[task] = sorted(sols, key=lambda s: -sols[s])
    return preds

if __name__ == "__main__":
    reveal = "--reveal" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--length" in sys.argv:
        report("BASELINE: длиннее = лучше", length_baseline(), reveal)
    else:
        preds = json.load(open(args[0]))
        report(args[1] if len(args) > 1 else args[0], preds, reveal)


def tie_stats(naive_results_glob="grader/blind/*/_result_naive.json"):
    """Сколько раз абсолютная оценка не различает работы (одинаковый балл)."""
    import glob as _g
    tied = tot = 0
    spreads = []
    for p in sorted(_g.glob(naive_results_glob)):
        r = json.load(open(p))
        sc = [v["score"] for v in r["scores"].values()]
        tot += 1
        if len(set(sc)) < len(sc):
            tied += 1
        spreads.append(max(sc) - min(sc))
    return {"tasks": tot, "with_ties": tied,
            "tie_rate": tied / max(tot, 1),
            "median_spread": sorted(spreads)[len(spreads) // 2] if spreads else None}
