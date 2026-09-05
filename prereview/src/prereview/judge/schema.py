"""Схемы ответов модели. Все поля обязательны: так требует strict-режим провайдера."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["pass", "partial", "fail", "not_found"]
Confidence = Literal["low", "medium", "high"]


class EvidenceItem(BaseModel):
    path: str = Field(description="Путь к файлу из среза, как в заголовке ===== FILE:")
    line_start: int = Field(description="Первая строка цитаты по нумерации среза")
    line_end: int = Field(description="Последняя строка цитаты")
    quote: str = Field(description="Дословная цитата из работы, без правок и сокращений")


class Judgement(BaseModel):
    verdict: Verdict = Field(description="pass: требование выполнено; partial: частично; fail: не выполнено; not_found: в срезе не найдено")
    proposed_points: float | None = Field(description="Предлагаемый балл в пределах максимума, кратный шагу; null если балл предложить нельзя")
    confidence: Confidence
    reason: str = Field(description="1–2 предложения: что именно найдено или чего не хватает")
    evidence: list[EvidenceItem] = Field(description="Цитаты, подтверждающие вердикт; пусто только при not_found или fail без следов")
    missing: list[str] = Field(description="Чего нет и где искали")
    reviewer_note: str = Field(description="На что ревьюеру посмотреть самому; пусто, если нечего добавить")
    student_feedback: str = Field(description="Одна короткая императивная фраза для студента; пусто, если требование выполнено")

    @classmethod
    def fake_example(cls) -> dict:
        return {
            "verdict": "partial",
            "proposed_points": None,
            "confidence": "low",
            "reason": "Заглушка модели.",
            "evidence": [],
            "missing": ["заглушка"],
            "reviewer_note": "Проверьте вручную.",
            "student_feedback": "",
        }


class Observation(BaseModel):
    text: str = Field(description="Наблюдение без оценки, одна фраза")
    evidence: list[EvidenceItem]


class Observations(BaseModel):
    observations: list[Observation] = Field(description="2–3 наблюдения с цитатами")

    @classmethod
    def fake_example(cls) -> dict:
        return {"observations": []}


class SelfFinding(BaseModel):
    status: Literal["met", "needs_attention", "not_checked"]
    feedback: str = Field(description="Что сделано или что доработать, 1–2 предложения, без баллов")
    evidence: list[EvidenceItem]

    @classmethod
    def fake_example(cls) -> dict:
        return {"status": "needs_attention", "feedback": "Заглушка: проверьте раздел.", "evidence": []}


class QAQuestions(BaseModel):
    questions: list[str] = Field(description="2–3 вопроса студенту для проверки понимания")

    @classmethod
    def fake_example(cls) -> dict:
        return {"questions": ["Заглушка: расскажите, как устроено решение."]}


class HarnessCriterionEvidence(BaseModel):
    criterion_id: str
    found: bool
    evidence: list[EvidenceItem]
    notes: str


class HarnessReport(BaseModel):
    criteria: list[HarnessCriterionEvidence]
