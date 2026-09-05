#!/usr/bin/env python3
"""Build a manifest of (task -> condition files, {weak,mid,good} -> solution files)."""
import json, os, re, sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "hw_examples"
SKIP_DIRS = {".git", "workflow", "mlruns", "__pycache__"}

LEVEL_PATTERNS = [
    (re.compile(r"слаб", re.I), "weak"),
    (re.compile(r"средн", re.I), "mid"),
    (re.compile(r"хорош", re.I), "good"),
]
COND = re.compile(r"услови|task\d|ДЗ\s*№?\s*\d|ДЗ\s+\d|условия", re.I)

def level_of(name):
    for pat, lvl in LEVEL_PATTERNS:
        if pat.search(name):
            return lvl
    return None

def walk(d):
    out = []
    for r, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f.startswith("."):
                continue
            out.append(os.path.join(r, f))
    return sorted(out)

tasks = {}
# a "task dir" = a dir that directly contains level-labelled files or dirs
for r, dirs, files in os.walk(ROOT):
    dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
    entries = dirs + files
    if not any(level_of(e) for e in entries):
        continue
    task_id = os.path.relpath(r, ROOT).replace("/", "::")
    t = {"task_dir": r, "condition": [], "solutions": {}}
    for e in entries:
        p = os.path.join(r, e)
        lvl = level_of(e)
        if lvl:
            t["solutions"].setdefault(lvl, []).extend(walk(p) if os.path.isdir(p) else [p])
        elif os.path.isfile(p) and COND.search(e):
            t["condition"].append(p)
        elif os.path.isfile(p):
            t["condition"].append(p)   # e.g. GO/task1.md handled below
    if t["solutions"]:
        tasks[task_id] = t

# GO: conditions live one level up as task1..3.md
if "." in tasks:
    tasks["GO"] = tasks.pop(".")
for tid, t in tasks.items():
    if tid.startswith("GO") and not t["condition"]:
        t["condition"] = sorted(f for f in walk(os.path.join(ROOT, "GO")) if re.match(r"task\d\.md", os.path.basename(f)))

json.dump(tasks, open("grader/manifest.json", "w"), ensure_ascii=False, indent=1)
for tid, t in sorted(tasks.items()):
    levels = {k: len(v) for k, v in sorted(t["solutions"].items())}
    print(f"{tid}\n   cond={[os.path.basename(c) for c in t['condition']]}\n   sols={levels}")
print(f"\nTOTAL TASKS: {len(tasks)}")
