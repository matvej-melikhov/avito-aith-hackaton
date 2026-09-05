#!/usr/bin/env python3
"""Стадия 5: прогон кода.

Критерии вида «запускается локально», «все тесты проходят», «обработчик
соответствует заданному API» нельзя проверить чтением — только запуском.
Модуль определяет тип проекта, гоняет тесты и возвращает факты для рубрики.

Запускать на недоверенном коде студентов следует в контейнере без сети.
Здесь — локальный прогон для оффлайн-эксперимента на эталонном корпусе.
"""
import json, os, subprocess, sys, venv

TIMEOUT = 180

def _run(cmd, cwd, timeout=TIMEOUT, env=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return {"cmd": " ".join(cmd), "rc": r.returncode,
                "out": r.stdout[-4000:], "err": r.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"cmd": " ".join(cmd), "rc": -1, "out": "", "err": f"timeout {timeout}s"}
    except FileNotFoundError as e:
        return {"cmd": " ".join(cmd), "rc": -2, "out": "", "err": str(e)}

def detect(root):
    files = set()
    for r, d, fs in os.walk(root):
        d[:] = [x for x in d if x not in {".git", "__pycache__", ".venv"}]
        for f in fs:
            files.add(f)
    if "go.mod" in files:
        return "go"
    if any(f.endswith(".py") for f in files):
        return "python"
    return None

def ensure_venv(sandbox_root, packages):
    vp = os.path.join(sandbox_root, ".venv")
    py = os.path.join(vp, "bin", "python")
    if not os.path.exists(py):
        venv.create(vp, with_pip=True)
        _run([py, "-m", "pip", "-q", "install", *packages], sandbox_root, timeout=600)
    return py

def check_python(sol_dir, py):
    res = {"kind": "python"}
    res["import_app"] = _run(
        [py, "-c", "import sys; sys.path.insert(0,'.'); import main; print('OK')"], sol_dir)
    res["starts"] = res["import_app"]["rc"] == 0
    t = _run([py, "-m", "pytest", "-q", "--no-header"], sol_dir)
    res["pytest"] = t
    res["tests_pass"] = t["rc"] == 0
    tail = [l for l in t["out"].strip().splitlines() if "passed" in l or "failed" in l]
    res["tests_summary"] = tail[-1] if tail else None
    return res

def check_go(sol_dir):
    res = {"kind": "go"}
    res["build"] = _run(["go", "build", "./..."], sol_dir)
    res["starts"] = res["build"]["rc"] == 0
    res["vet"] = _run(["go", "vet", "./..."], sol_dir)
    t = _run(["go", "test", "./...", "-count=1"], sol_dir)
    res["test"] = t
    res["tests_pass"] = t["rc"] == 0
    return res

def run_conformance(sol_dir, py, script_path):
    """script_path — тест соответствия ТЗ, сгенерированный из условия."""
    if not os.path.exists(script_path):
        return None
    return _run([py, os.path.abspath(script_path)], sol_dir)

def check(sandbox_root, conformance=None, packages=("fastapi[standard]", "pytest", "httpx")):
    """sandbox_root содержит подпапки A/B/C (см. sandbox.py)."""
    out = {}
    sids = sorted(d for d in os.listdir(sandbox_root)
                  if os.path.isdir(os.path.join(sandbox_root, d)) and not d.startswith("."))
    py = None
    for sid in sids:
        d = os.path.join(sandbox_root, sid)
        kind = detect(d)
        if kind == "go":
            out[sid] = check_go(d)
        elif kind == "python":
            py = py or ensure_venv(sandbox_root, list(packages))
            out[sid] = check_python(d, py)
            if conformance:
                c = run_conformance(d, py, conformance)
                if c and c["rc"] == 0:
                    try:
                        out[sid]["conformance"] = json.loads(c["out"].strip().splitlines()[-1])
                    except Exception:
                        out[sid]["conformance_raw"] = c["out"][-1000:]
        else:
            out[sid] = {"kind": None, "note": "не код — стадия пропущена"}
    return out

def render(results):
    """Факты для подачи в промпт стадии сравнения."""
    lines = ["ФАКТЫ ПРОГОНА КОДА (не оценка, результат запуска):"]
    for sid, r in sorted(results.items()):
        if not r.get("kind"):
            continue
        lines.append(f"- работа {sid}: собирается/импортируется = {r.get('starts')}, "
                     f"тесты проходят = {r.get('tests_pass')} "
                     f"({r.get('tests_summary') or ''})")
        c = r.get("conformance")
        if c:
            lines.append(f"    соответствие ТЗ: путь={c.get('working_path')} "
                         f"(по ТЗ: {c.get('path_matches_spec')}), "
                         f"ответ — голый bool: {c.get('response_is_bare_bool')}, "
                         f"логика верна: {c.get('logic_correct')}")
    return "\n".join(lines)

if __name__ == "__main__":
    root = sys.argv[1]
    conf = sys.argv[2] if len(sys.argv) > 2 else None
    r = check(root, conf)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:3000])
    print("\n" + render(r))
