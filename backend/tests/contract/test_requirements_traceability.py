from __future__ import annotations

import re

from tests.support.contracts import FEATURE_ROOT


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", ":", " "}:
            continue
        rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows[1:]


def _covered_ids(cells: list[str], prefix: str) -> set[int]:
    found: set[int] = set()
    for start_text, end_text in re.findall(
        rf"{prefix}-(\d{{3}})(?:\.\.{prefix}-(\d{{3}}))?", " ".join(cells)
    ):
        start = int(start_text)
        end = int(end_text or start_text)
        found.update(range(start, end + 1))
    return found


def test_every_functional_requirement_has_an_executable_owner_and_status() -> None:
    text = (FEATURE_ROOT / "requirements-traceability.md").read_text(encoding="utf-8")
    functional, success = text.split("## Success criteria", maxsplit=1)
    rows = _table_rows(functional)

    assert _covered_ids([row[0] for row in rows], "FR") == set(range(1, 86))
    for requirements, executable, owner, command, status in rows:
        assert requirements.startswith("FR-")
        assert "tests/" in executable and executable.count("`") >= 2
        assert owner
        assert command.startswith("`uv run --directory backend pytest ")
        assert status in {"PLANNED", "BLOCKED"} or status.startswith("BLOCKED ")

    success_rows = _table_rows(success.split("## Contract and live-gate policy", maxsplit=1)[0])
    assert _covered_ids([row[0] for row in success_rows], "SC") == set(range(1, 24))
    for criterion, fixture, owner, command, status in success_rows:
        assert criterion.startswith("SC-")
        assert fixture
        assert owner
        assert command.startswith("`uv run --directory backend pytest ")
        assert status == "PLANNED" or status.startswith("BLOCKED ")
