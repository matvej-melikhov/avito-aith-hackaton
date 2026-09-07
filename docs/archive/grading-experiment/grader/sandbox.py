#!/usr/bin/env python3
"""Копирует решения в анонимные папки A/B/C (тот же shuffle, что в blind.py) и гоняет тесты.
Метки уровней НЕ печатаются — на выходе только blind id и результат прогона."""
import glob, json, os, random, shutil, subprocess, sys

M = json.load(open("docs/archive/grading-experiment/grader/manifest.json"))
random.seed(20260904)

task = sys.argv[1]
dest_root = sys.argv[2]

# воспроизводим порядок из blind.py
tasks = {}
for p in sorted(glob.glob("docs/archive/grading-experiment/grader/bundles/*__*.txt")):
    b = os.path.basename(p)[:-4]
    if b.endswith("__CONDITION"): continue
    t, lvl = b.rsplit("__", 1)
    tasks.setdefault(t, {})[lvl] = p

mapping = {}
for t, d in sorted(tasks.items()):
    items = [(lvl, p) for lvl, p in d.items() if os.path.getsize(p) > 200]
    if len(items) < 2: continue
    random.shuffle(items)
    for i, (lvl, p) in enumerate(items):
        mapping[f"{t}/{'ABCD'[i]}"] = lvl

# найти исходные каталоги решений
src = None
for tid, t in M.items():
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in tid)[:80]
    if slug == task:
        src = t
        break
assert src, f"task {task} not found"

shutil.rmtree(dest_root, ignore_errors=True)
for sid_key, lvl in mapping.items():
    t, sid = sid_key.rsplit("/", 1)
    if t != task: continue
    files = src["solutions"][lvl]
    common = os.path.commonpath([os.path.dirname(f) for f in files])
    dst = os.path.join(dest_root, sid)
    os.makedirs(dst, exist_ok=True)
    for f in files:
        rel = os.path.relpath(f, common)
        os.makedirs(os.path.join(dst, os.path.dirname(rel)), exist_ok=True)
        shutil.copy2(f, os.path.join(dst, rel))
    print(f"prepared {sid}: {len(files)} files")
