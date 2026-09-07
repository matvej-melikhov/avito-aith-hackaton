#!/usr/bin/env python3
"""Create a blinded eval set: random ids, scrubbed level words, shuffled order."""
import glob, hashlib, json, os, random, re, sys

import sys
B   = sys.argv[1] if len(sys.argv) > 1 else "docs/archive/grading-experiment/grader/bundles"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs/archive/grading-experiment/grader/blind"
os.makedirs(OUT, exist_ok=True)
random.seed(20260904)

# words that leak the label, in file paths and body text
LEAK = re.compile(r"(Слабое|Среднее|Хорошее|слабое|среднее|хорошее|Слабо|weak|mid|good)\s*(решение|_\d)?", re.I)

tasks = {}
for p in sorted(glob.glob(f"{B}/*__*.txt")):
    b = os.path.basename(p)[:-4]
    if b.endswith("__CONDITION"):
        continue
    t, lvl = b.rsplit("__", 1)
    tasks.setdefault(t, {})[lvl] = p

key = {}
for t, d in sorted(tasks.items()):
    items = [(lvl, p) for lvl, p in d.items()
             if os.path.getsize(p) > 200 and "[[NEEDS_VISION]]" not in open(p).read()[:200]]
    if len(items) < 2:
        continue
    random.shuffle(items)
    tdir = os.path.join(OUT, t)
    os.makedirs(tdir, exist_ok=True)
    cond = f"{B}/{t}__CONDITION.txt"
    if os.path.exists(cond):
        open(f"{tdir}/CONDITION.txt", "w").write(open(cond).read())
    for i, (lvl, p) in enumerate(items):
        sid = "ABCD"[i]
        body = LEAK.sub("[СКРЫТО]", open(p).read())
        open(f"{tdir}/solution_{sid}.txt", "w").write(body)
        key[f"{t}/{sid}"] = lvl

json.dump(key, open("docs/archive/grading-experiment/grader/blind_key.json", "w"), ensure_ascii=False, indent=1)
print(f"blinded {len(key)} solutions across {len(set(k.rsplit('/',1)[0] for k in key))} tasks")
print("key written to docs/archive/grading-experiment/grader/blind_key.json (NOT printed)")
