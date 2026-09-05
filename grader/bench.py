#!/usr/bin/env python3
"""Прогон обоих протоколов по всем заданиям + сверка с эталоном.

  GRADER_API_KEY=... python3 grader/bench.py            # оба протокола
  GRADER_API_KEY=... python3 grader/bench.py --naive    # только baseline
"""
import glob, json, os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run import run_task
import eval as ev

def main():
    only_naive = "--naive" in sys.argv
    only_struct = "--structured" in sys.argv
    tasks = [d for d in sorted(glob.glob("grader/blind/*")) if os.path.isdir(d)]
    out = {"naive": {}, "structured": {}}
    for td in tasks:
        name = os.path.basename(td)
        for mode, flag in (("naive", True), ("structured", False)):
            if only_naive and mode != "naive": continue
            if only_struct and mode != "structured": continue
            try:
                r = run_task(td, naive=flag)
                out[mode][name] = r.get("ranking") or r["verdict"]["ranking"]
                json.dump(r, open(f"{td}/_result_{mode}.json", "w"), ensure_ascii=False, indent=1)
                print(f"[{mode:<10}] {name[:46]:<46} -> {''.join(out[mode][name])}")
            except Exception as e:
                print(f"[{mode:<10}] {name[:46]:<46} !! {type(e).__name__}: {e}")
                traceback.print_exc(limit=1)
    os.makedirs("grader/preds", exist_ok=True)
    for mode, preds in out.items():
        if preds:
            json.dump(preds, open(f"grader/preds/{mode}.json", "w"), ensure_ascii=False, indent=1)
    print("\n" + "=" * 72)
    ev.report("BASELINE: длиннее = лучше", ev.length_baseline())
    for mode, preds in out.items():
        if preds:
            ev.report(f"ПРОТОКОЛ: {mode}", preds)

if __name__ == "__main__":
    main()
