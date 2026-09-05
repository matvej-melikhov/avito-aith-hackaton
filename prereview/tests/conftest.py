import os
import uuid
from pathlib import Path

import pytest

from prereview.config import REPO_ROOT, get_settings

EXAMPLES = REPO_ROOT / "specs" / "005-workspace-completion" / "contracts" / "examples"
SCHEMAS = REPO_ROOT / "specs" / "005-workspace-completion" / "contracts"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PREREVIEW_LLM_PROVIDER", "fake")
    monkeypatch.setenv("PREREVIEW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PREREVIEW_HARNESS_ENABLED", "false")
    monkeypatch.delenv("PREREVIEW_TOKEN", raising=False)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def md_artifact(tmp_path):
    p = tmp_path / "work.md"
    p.write_text("# Лаба\n\n## Контекст\n\nСистема продаёт билеты.\n\n```mermaid\nC4Context\nPerson(u, \"User\")\n```\n", encoding="utf-8")
    return p


def criteria_payload(n: int = 2, private: bool = True) -> list[dict]:
    out = []
    for i in range(n):
        c = {"id": str(uuid.uuid4()), "key": f"k{i}", "title": f"Критерий {i}", "max_points": 2.0}
        if private:
            c |= {"description": "Описание", "score_step": 0.5, "evaluate_quality": i == 1, "position": i + 1}
        out.append(c)
    return out
