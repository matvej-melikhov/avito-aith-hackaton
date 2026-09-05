import json
import uuid

import jsonschema
import pytest

from conftest import EXAMPLES, SCHEMAS
from prereview.contracts import (
    ReviewAssistEvent, ReviewAssistRequest, ReviewAssistResult, ReviewCriterionView, ReviewerSuggestion,
    SelfReviewEvent, SelfReviewRequest, validate_assist_result,
)


@pytest.mark.parametrize("name,model", [
    ("review-assist-request.json", ReviewAssistRequest), ("review-assist-running.json", ReviewAssistEvent),
    ("review-assist-succeeded.json", ReviewAssistEvent), ("review-assist-unavailable.json", ReviewAssistEvent),
    ("self-review-request.json", SelfReviewRequest), ("self-review-succeeded.json", SelfReviewEvent),
    ("self-review-running.json", SelfReviewEvent), ("self-review-unsupported-format.json", SelfReviewEvent),
    ("self-review-all-not-checked.json", SelfReviewEvent),
])
def test_examples_validate(name, model):
    data = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    obj = model.model_validate(data)
    if "Event" in model.__name__:
        schema_name = "review-assist-event.schema.json" if "assist" in name else "self-review-event.schema.json"
        schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
        jsonschema.validate(obj.model_dump(mode="json"), schema)


def test_validate_assist_rules():
    cid, qid = uuid.uuid4(), uuid.uuid4()
    criteria = [ReviewCriterionView(id=cid, key="a", title="A", max_points=2, position=1),
                ReviewCriterionView(id=qid, key="q", title="Q", max_points=1, evaluate_quality=True, position=2)]
    ok = ReviewAssistResult(suggestions=[
        ReviewerSuggestion(criterion_id=cid, status="suggested", proposed_points=2, reason="ok"),
        ReviewerSuggestion(criterion_id=qid, status="suggested", proposed_points=0.5, requirement_met=True, reason="ok"),
    ])
    assert validate_assist_result(ok, criteria) == []
    bad = ReviewAssistResult(suggestions=[
        ReviewerSuggestion(criterion_id=cid, status="suggested", proposed_points=3, reason="too much"),
        ReviewerSuggestion(criterion_id=qid, status="suggested", proposed_points=0.5, reason="no met"),
        ReviewerSuggestion(criterion_id=cid, status="not_checked", proposed_points=1, reason="dup"),
    ])
    problems = validate_assist_result(bad, criteria)
    assert any("больше максимума" in p for p in problems)
    assert any("requirement_met" in p for p in problems)
    assert any("дубликат" in p for p in problems)
