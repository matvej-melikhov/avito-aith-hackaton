"""Контракт функции-сигнала. Каждый сигнал независим, у каждого есть ограничение."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from prereview.artifact.model import Work

Group = Literal["artifacts", "style", "process", "spec_mismatch", "declaration"]
Strength = Literal["weak", "strong"]


@dataclass
class Ground:
    signal: str
    group: Group
    strength: Strength
    text: str
    limitation: str
    evidence: list[tuple[str, int, str]] = field(default_factory=list)  # path, line, snippet

    def to_dict(self) -> dict:
        return {"signal": self.signal, "group": self.group, "strength": self.strength, "text": self.text,
                "limitation": self.limitation,
                "evidence": [{"path": p, "line": ln, "snippet": s} for p, ln, s in self.evidence[:5]]}


Signal = Callable[[Work, dict], Ground | None]
