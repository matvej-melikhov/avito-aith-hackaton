"""Canonical AI portion of the frozen ReviewDetail projection."""

from __future__ import annotations

from typing import Any

from review_platform.infrastructure.db.repositories.ai_reviews import AIReviewHistory


def project_ai_review(history: AIReviewHistory | None) -> dict[str, Any] | None:
    if history is None:
        return None
    run = history.run
    suggestions = [
        {
            "id": str(item.id),
            "criterion_id": str(item.criterion_id),
            "status": item.status,
            "proposed_points": (
                float(item.proposed_points) if item.proposed_points is not None else None
            ),
            "reason": item.reason,
            "evidence": list(item.evidence),
            "confidence": item.confidence,
            "reviewer_note": item.reviewer_note,
            "student_feedback": item.student_feedback,
            "flags": list(item.flags),
        }
        for item in history.suggestions
    ]
    signal_row = history.signals[-1] if history.signals else None
    signal = (
        {
            "level": signal_row.level,
            "evidence": list(signal_row.evidence),
            "limitations": list(signal_row.limitations),
            "questions": list(signal_row.questions),
        }
        if signal_row is not None
        else None
    )
    attempts = [
        {
            "attempt_number": item.attempt_number,
            "state": item.status,
            "started_at": item.started_at.isoformat(),
            "finished_at": item.finished_at.isoformat() if item.finished_at else None,
            "error": item.sanitized_error,
        }
        for item in history.attempts
    ]
    current_attempt = next(
        (
            item
            for item in reversed(history.attempts)
            if item.attempt_number == run.current_attempt_no
        ),
        None,
    )
    return {
        "run_id": str(run.id),
        "input_fingerprint": run.input_fingerprint,
        "contract_version": run.contract_version,
        "state": run.status,
        "attempts": attempts,
        "suggestions": suggestions,
        "signal": signal,
        "error": current_attempt.sanitized_error if current_attempt is not None else None,
    }


__all__ = ["project_ai_review"]
