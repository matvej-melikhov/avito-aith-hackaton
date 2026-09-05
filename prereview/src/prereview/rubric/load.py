"""Поиск и загрузка файла задания."""

from __future__ import annotations

import json
from pathlib import Path

from prereview.rubric.model import AssignmentFile


def load_assignment(path: Path) -> AssignmentFile:
    return AssignmentFile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def list_assignments(directory: Path) -> list[AssignmentFile]:
    if not directory.exists():
        return []
    out = []
    for p in sorted(directory.glob("*.json")):
        try:
            out.append(load_assignment(p))
        except Exception:  # noqa: BLE001 — битый файл не должен ронять сервис
            continue
    return out


def find_assignment(directory: Path, slug: str | None, keys: list[str]) -> AssignmentFile | None:
    """По slug, иначе по совпадению ключей критериев (не меньше 60 % ключей запроса)."""
    if slug:
        path = directory / f"{slug}.json"
        return load_assignment(path) if path.exists() else None
    if not keys:
        return None
    best: tuple[float, AssignmentFile | None] = (0.0, None)
    wanted = set(keys)
    for a in list_assignments(directory):
        known = set(a.criteria) | set(a.match_keys)
        share = len(wanted & known) / len(wanted)
        if share > best[0]:
            best = (share, a)
    return best[1] if best[0] >= 0.6 else None
