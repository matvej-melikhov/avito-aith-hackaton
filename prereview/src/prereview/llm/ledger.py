"""Учёт токенов и стоимости каждого вызова модели. «₽ за работу» считается отсюда."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0

    @property
    def cache_miss_tokens(self) -> int:
        return max(self.prompt_tokens - self.cache_hit_tokens, 0)

    @classmethod
    def from_api(cls, usage: object) -> Usage:
        if usage is None:
            return cls()
        get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
        prompt = int(get("prompt_tokens") or 0)
        completion = int(get("completion_tokens") or 0)
        hit = get("prompt_cache_hit_tokens")
        if hit is None:
            details = get("prompt_tokens_details")
            if isinstance(details, dict):
                hit = details.get("cached_tokens")
            elif details is not None:
                hit = getattr(details, "cached_tokens", None)
        return cls(prompt, completion, int(hit or 0))


@dataclass
class Call:
    tag: str
    model: str
    usage: Usage
    elapsed_seconds: float
    cost_usd: float
    attempts: int = 1
    note: str = ""
    at: float = field(default_factory=time.time)


class Pricing:
    def __init__(self, table: dict, usd_rub: float):
        self.table = table.get("models", {})
        self.usd_rub = usd_rub
        self.meta = {k: v for k, v in table.items() if k != "models"}

    @classmethod
    def load(cls, path: Path, usd_rub: float) -> Pricing:
        return cls(json.loads(path.read_text(encoding="utf-8")), usd_rub)

    def cost_usd(self, model: str, usage: Usage) -> float:
        rates = self.table.get(model)
        if not rates:
            return 0.0
        return (
            usage.cache_miss_tokens * rates["input_cache_miss_per_m"]
            + usage.cache_hit_tokens * rates["input_cache_hit_per_m"]
            + usage.completion_tokens * rates["output_per_m"]
        ) / 1_000_000


class Ledger:
    """Журнал вызовов одного прогона."""

    def __init__(self, pricing: Pricing):
        self.pricing = pricing
        self.calls: list[Call] = []

    def record(
        self, tag: str, model: str, usage: Usage, elapsed: float, attempts: int = 1, note: str = ""
    ) -> Call:
        call = Call(tag, model, usage, elapsed, self.pricing.cost_usd(model, usage), attempts, note)
        self.calls.append(call)
        return call

    @property
    def prompt_tokens(self) -> int:
        return sum(c.usage.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.usage.completion_tokens for c in self.calls)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def cost_rub(self) -> float:
        return self.cost_usd * self.pricing.usd_rub

    def summary(self) -> dict:
        return {
            "calls": len(self.calls),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_hit_tokens": sum(c.usage.cache_hit_tokens for c in self.calls),
            "cost_usd": round(self.cost_usd, 5),
            "cost_rub": round(self.cost_rub, 2),
            "usd_rub": self.pricing.usd_rub,
            "usd_rub_note": "допущение",
            "elapsed_seconds": round(sum(c.elapsed_seconds for c in self.calls), 1),
            "by_tag": self._by_tag(),
        }

    def _by_tag(self) -> dict:
        out: dict[str, dict] = {}
        for c in self.calls:
            row = out.setdefault(c.tag, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
            row["calls"] += 1
            row["prompt_tokens"] += c.usage.prompt_tokens
            row["completion_tokens"] += c.usage.completion_tokens
            row["cost_usd"] = round(row["cost_usd"] + c.cost_usd, 5)
        return out
