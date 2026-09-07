#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import extract

M = json.load(open("docs/archive/grading-experiment/grader/manifest.json"))
OUT = "docs/archive/grading-experiment/grader/bundles"
os.makedirs(OUT, exist_ok=True)
PER_FILE_CAP = 400_000

def slug(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:80]

rows = []
for tid, t in sorted(M.items()):
    # condition bundle
    cparts = []
    for c in t["condition"]:
        tx = extract(c)
        if tx:
            cparts.append(f"===== FILE: {os.path.basename(c)} =====\n{tx[:PER_FILE_CAP]}")
    cond = "\n\n".join(cparts)
    open(f"{OUT}/{slug(tid)}__CONDITION.txt", "w").write(cond)

    for lvl, files in sorted(t["solutions"].items()):
        parts, nfiles = [], 0
        for f in files:
            tx = extract(f)
            if tx is None or not tx.strip():
                continue
            rel = os.path.relpath(f, t["task_dir"])
            parts.append(f"===== FILE: {rel} =====\n{tx[:PER_FILE_CAP]}")
            nfiles += 1
        body = "\n\n".join(parts)
        path = f"{OUT}/{slug(tid)}__{lvl}.txt"
        open(path, "w").write(body)
        rows.append((tid, lvl, nfiles, len(body), len(cond)))

print(f"{'task':<52} {'lvl':<5} {'files':>5} {'sol_chars':>10} {'cond_chars':>10}")
for r in rows:
    print(f"{r[0][:52]:<52} {r[1]:<5} {r[2]:>5} {r[3]:>10} {r[4]:>10}")
print("\ntotal solution chars:", sum(r[3] for r in rows))
