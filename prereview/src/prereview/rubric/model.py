"""Рубрика: критерии платформы плюс настройки задания из файла.

Правило проекта: критерии задания это файл настроек, а не промпт. Класс проверки
(formal / content / judgement) и формальные проверки задаются в assignments/<slug>.json
по ключу критерия. Ядро не знает про конкретный курс.
"""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prereview.contracts import PublicCriterion, ReviewCriterionView

CheckClass = Literal["formal", "content", "judgement"]

JUDGEMENT_WORDS = re.compile(
    r"(?i)\b(хорош\w*|читаем\w*|понятн\w*|осознанн\w*|качеств\w*|обоснованн\w*|аккуратн\w*|"
    r"чист\w*|грамотн\w*|глубин\w*|продуманн\w*|тонк\w*|удобн\w*|логичн\w*)\b"
)


class CheckSpec(BaseModel):
    """Одна формальная проверка. kind = имя примитива из checks/primitives.py."""

    model_config = ConfigDict(extra="allow")

    kind: str
    note: str = ""


class RuntimeProbe(BaseModel):
    """HTTP-проба в сценарии запуска: метод, путь и ожидания (expect_status, expect_json и т. п.)."""

    model_config = ConfigDict(extra="allow")

    id: str
    method: str = "GET"
    path: str = "/"


class RuntimeScenario(BaseModel):
    """Сценарий песочницы: service (запуск, пробы, остановка) или migrations (чистый и повторный прогон)."""

    model_config = ConfigDict(extra="allow")

    id: str
    kind: Literal["service", "migrations"] = "service"
    title: str = ""
    probes: list[RuntimeProbe] = Field(default_factory=list)

    def refs(self) -> set[str]:
        if self.kind == "migrations":
            return {self.id, f"{self.id}.first", f"{self.id}.second"}
        out = {self.id, f"{self.id}.port", f"{self.id}.stop", f"{self.id}.log", f"{self.id}.log_exact", f"{self.id}.stdout"}
        out |= {f"{self.id}.{p.id}" for p in self.probes}
        return out


class RuntimeSpec(BaseModel):
    """Сценарии запуска в песочнице (prereview/runner). Критерий ссылается на исходы по строкам
    «сценарий», «сценарий.проба», «сценарий.stop», «сценарий.log», «миграции.first», «миграции.second»."""

    model_config = ConfigDict(extra="allow")

    kind: str = "http_service"
    scenarios: list[RuntimeScenario] = Field(default_factory=list)

    def refs(self) -> set[str]:
        out: set[str] = set()
        for s in self.scenarios:
            out |= s.refs()
        return out


class CriterionSettings(BaseModel):
    """Настройки одного критерия в файле задания."""

    model_config = ConfigDict(extra="forbid")

    check_class: CheckClass | None = None
    checks: list[CheckSpec] = Field(default_factory=list)
    runtime: list[str] = Field(default_factory=list)  # ссылки на исходы сценариев запуска
    scope: list[str] = Field(default_factory=list)  # glob-подсказки для репозиториев
    hints: str = ""  # что именно искать, описание уровней, заметки калибровки
    critical: bool = False
    points_if_pass: float | None = None  # для формальных: сколько давать при pass (по умолчанию max)
    # Поля ниже нужны только для локальных прогонов и evals без платформы:
    # платформа присылает свои title/description/max_points, а здесь они позволяют
    # собрать критерии из одного файла задания.
    title: str = ""
    description: str = ""
    max_points: float | None = None
    group: str = ""


class AssignmentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str = ""
    version: str = "1"
    match_keys: list[str] = Field(default_factory=list)
    student_text_hint: str = ""  # краткое ТЗ для модели, если student_text платформы неполный
    runtime: RuntimeSpec | None = None  # сценарии запуска в песочнице
    criteria: dict[str, CriterionSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _runtime_refs_exist(self) -> "AssignmentFile":
        known = self.runtime.refs() if self.runtime else set()
        for key, s in self.criteria.items():
            unknown = [r for r in s.runtime if r not in known]
            if unknown:
                raise ValueError(f"критерий {key}: неизвестные ссылки на сценарии запуска {unknown}")
        return self


class Criterion(BaseModel):
    """Критерий, готовый к проверке: поля платформы + класс + формальные проверки."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    key: str
    title: str
    description: str = ""
    max_points: float = 0
    score_step: float = 0.5
    evaluate_quality: bool = False
    position: int = 0
    check_class: CheckClass = "content"
    class_source: Literal["platform", "assignment", "heuristic"] = "heuristic"
    checks: list[CheckSpec] = Field(default_factory=list)
    runtime: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    hints: str = ""
    critical: bool = False
    points_if_pass: float | None = None

    @property
    def is_formal(self) -> bool:
        return self.check_class == "formal" and bool(self.checks)

    def snap(self, points: float | None) -> float | None:
        """Округление к шагу балла и обрезка в [0, max]."""
        if points is None:
            return None
        import math

        step = self.score_step or 0.5
        snapped = round(math.floor(points / step + 0.5) * step, 4)
        return max(0.0, min(float(self.max_points), snapped))


def infer_class(c: ReviewCriterionView | PublicCriterion) -> CheckClass:
    if getattr(c, "evaluate_quality", False):
        return "judgement"
    text = f"{c.title} {getattr(c, 'description', '')}"
    if JUDGEMENT_WORDS.search(text):
        return "judgement"
    return "content"


def criteria_from_assignment(a: AssignmentFile) -> list[ReviewCriterionView]:
    """Критерии из файла задания для CLI и evals (id детерминированный, uuid5 от slug и key)."""
    import uuid

    out: list[ReviewCriterionView] = []
    for i, (key, s) in enumerate(a.criteria.items(), 1):
        out.append(ReviewCriterionView(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"prereview:{a.slug}:{key}"),
            key=key,
            title=s.title or key,
            description=s.description,
            max_points=float(s.max_points or 0),
            score_step=0.5,
            evaluate_quality=(s.check_class == "judgement"),
            position=i,
        ))
    return out


def build_rubric(
    criteria: list[ReviewCriterionView] | list[PublicCriterion],
    assignment: AssignmentFile | None,
) -> list[Criterion]:
    out: list[Criterion] = []
    for c in criteria:
        settings = assignment.criteria.get(c.key) if assignment else None
        platform_class = getattr(c, "check_class", None)
        if settings and settings.check_class:
            check_class, source = settings.check_class, "assignment"
        elif platform_class:
            check_class, source = platform_class, "platform"
        else:
            check_class, source = infer_class(c), "heuristic"
        out.append(
            Criterion(
                id=c.id,
                key=c.key,
                title=c.title,
                description=getattr(c, "description", "") or "",
                max_points=float(c.max_points),
                score_step=float(getattr(c, "score_step", 0.5) or 0.5),
                evaluate_quality=bool(getattr(c, "evaluate_quality", False)),
                position=int(getattr(c, "position", 0) or 0),
                check_class=check_class,
                class_source=source,
                checks=list(settings.checks) if settings else [],
                runtime=list(settings.runtime) if settings else [],
                scope=list(settings.scope) if settings else [],
                hints=settings.hints if settings else "",
                critical=settings.critical if settings else False,
                points_if_pass=settings.points_if_pass if settings else None,
            )
        )
    out.sort(key=lambda x: (x.position, x.key))
    return out
