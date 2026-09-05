"""Контракт workspace v2.0.0 между платформой и AI-сервисом.

Источник типов: backend/src/review_platform/contracts/workspace.py.
Запросы принимаем мягко (лишние поля игнорируем: платформа может расширить контракт),
события отдаём строго (лишних полей нет, бэкенд валидирует extra="forbid").
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, model_validator

CONTRACT_VERSION = "2.0.0"
Nonnegative = Annotated[int, Field(ge=0, strict=True)]
Points = Annotated[float, Field(ge=0, allow_inf_nan=False)]
ErrorCode = Literal["unavailable", "invalid_artifact", "unsupported_format", "invalid_result"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- критерии -------------------------------------------------------------


class PublicCriterion(Lenient):
    max_points: Points = 0
    id: UUID
    key: str
    title: str


class ReviewCriterionView(PublicCriterion):
    score_step: PositiveFloat = Field(default=0.5, allow_inf_nan=False)
    evaluate_quality: bool = False
    description: str = ""
    max_points: Points = 0
    position: Nonnegative = 0
    # Не входит в контракт v2, но платформа может прислать позже; читаем, если есть.
    check_class: Literal["formal", "content", "judgement"] | None = None


# --- самопроверка студента --------------------------------------------------


class SelfReviewRequest(Lenient):
    contract_version: str = CONTRACT_VERSION
    purpose: Literal["student_self_review"] = "student_self_review"
    run_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    input_fingerprint: str
    artifact_id: UUID
    artifact_url: str
    artifact_digest: str
    media_type: str
    student_text: str
    criteria: list[PublicCriterion]


class SelfReviewFinding(Strict):
    criterion_id: UUID
    status: Literal["met", "needs_attention", "not_checked"]
    feedback: str = Field(min_length=1, max_length=10000)
    evidence: str = Field(default="", max_length=4000)


class SelfReviewResult(Strict):
    findings: list[SelfReviewFinding] = Field(min_length=1, max_length=500)


class SelfReviewEvent(Strict):
    contract_version: Literal["2.0.0"] = CONTRACT_VERSION
    event_id: UUID
    run_id: UUID
    attempt: Annotated[int, Field(ge=1, strict=True)]
    sequence: Nonnegative
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["running", "succeeded", "failed"]
    result: SelfReviewResult | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def coherent(self) -> SelfReviewEvent:
        if (self.status == "succeeded") != (self.result is not None):
            raise ValueError("only successful final events contain a result")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("only failed events contain an error code")
        return self


# --- помощь ревьюеру --------------------------------------------------------


class ReviewAssistRequest(Lenient):
    contract_version: str = CONTRACT_VERSION
    purpose: Literal["reviewer_assist"] = "reviewer_assist"
    run_id: UUID
    attempt: Annotated[int, Field(ge=1)]
    input_fingerprint: str
    review_iteration_id: UUID
    artifact_id: UUID
    artifact_url: str
    artifact_digest: str
    media_type: str
    student_text: str
    criteria: list[ReviewCriterionView]
    reviewer_guidance: str = ""
    reference_url: str | None = None


class AssistEvidence(Strict):
    quote: str = Field(min_length=1, max_length=10000)
    locator: str | None = Field(default=None, max_length=2048)
    path: str | None = Field(default=None, max_length=2048)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    # Контракт запрещает заявлять проверку: поле всегда false. Факт нашей верификации
    # передаём словами в ReviewerSuggestion.evidence.
    verified: Literal[False] = False

    @model_validator(mode="after")
    def ordered_lines(self) -> AssistEvidence:
        if self.line_end is not None and (
            self.line_start is None or self.line_end < self.line_start
        ):
            raise ValueError("line_end requires an ordered line_start")
        return self


class AuthorshipSignal(Strict):
    id: str = Field(min_length=1, max_length=128)
    probability: float | None = Field(default=None, ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=10000)
    evidence: list[AssistEvidence] = Field(default_factory=list, max_length=50)


class ReviewerSuggestion(Strict):
    requirement_met: bool | None = None
    sources: list[AssistEvidence] = Field(default_factory=list, max_length=50)
    criterion_id: UUID
    status: Literal["suggested", "needs_human", "not_checked"]
    proposed_points: Points | None
    reason: str = Field(min_length=1, max_length=10000)
    evidence: list[str] = Field(default_factory=list, max_length=50)
    confidence: Literal["low", "medium", "high"] = "medium"
    reviewer_note: str | None = Field(default=None, max_length=10000)
    student_feedback: str | None = Field(default=None, max_length=10000)


class ReviewAssistResult(Strict):
    authorship_signal: AuthorshipSignal | None = None
    feedback_draft: str | None = Field(default=None, max_length=20000)
    suggestions: list[ReviewerSuggestion] = Field(min_length=1, max_length=500)


class ReviewAssistEvent(Strict):
    contract_version: Literal["2.0.0"] = CONTRACT_VERSION
    event_id: UUID
    run_id: UUID
    attempt: Annotated[int, Field(ge=1, strict=True)]
    sequence: Nonnegative
    input_fingerprint: str
    status: Literal["running", "succeeded", "failed"]
    result: ReviewAssistResult | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def coherent(self) -> ReviewAssistEvent:
        if (self.status == "succeeded") != (self.result is not None):
            raise ValueError("only successful results contain suggestions")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("only failed results contain an error code")
        return self


# --- правила бэкенда, которые проверяем до отправки -------------------------


def validate_assist_result(
    result: ReviewAssistResult, criteria: list[ReviewCriterionView]
) -> list[str]:
    """Возвращает список нарушений правил review_assist.py:282-305. Пустой список = ок."""
    problems: list[str] = []
    by_id = {c.id: c for c in criteria}
    seen: set[UUID] = set()
    for s in result.suggestions:
        if s.criterion_id in seen:
            problems.append(f"дубликат критерия {s.criterion_id}")
        seen.add(s.criterion_id)
        c = by_id.get(s.criterion_id)
        if c is None:
            problems.append(f"посторонний критерий {s.criterion_id}")
            continue
        if s.proposed_points is not None and s.proposed_points > c.max_points:
            problems.append(f"{c.key}: балл {s.proposed_points} больше максимума {c.max_points}")
        if s.status == "not_checked" and s.proposed_points is not None:
            problems.append(f"{c.key}: not_checked с баллом")
        if c.evaluate_quality:
            if s.proposed_points is not None and s.requirement_met is None:
                problems.append(f"{c.key}: evaluate_quality требует requirement_met при балле")
            if s.requirement_met is False and (s.proposed_points or 0) != 0:
                problems.append(f"{c.key}: requirement_met=false требует балл 0")
    missing = set(by_id) - seen
    if missing:
        problems.append(f"нет критериев: {sorted(str(m) for m in missing)}")
    return problems


def validate_self_review_result(
    result: SelfReviewResult, criteria: list[PublicCriterion]
) -> list[str]:
    problems: list[str] = []
    ids = [f.criterion_id for f in result.findings]
    if len(ids) != len(set(ids)):
        problems.append("дубликаты критериев")
    expected = {c.id for c in criteria}
    if set(ids) != expected:
        problems.append("набор критериев не совпадает с запросом")
    if all(f.status == "not_checked" for f in result.findings):
        problems.append("все критерии not_checked: бэкенд посчитает проверку несостоявшейся")
    return problems
