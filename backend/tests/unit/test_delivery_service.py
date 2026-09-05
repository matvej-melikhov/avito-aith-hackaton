from __future__ import annotations

import pytest

from review_platform.application.services.deliveries import (
    DeliveryTransitionError,
    validate_delivery_transition,
)


def test_unknown_outcome_must_reconcile_before_retry() -> None:
    assert (
        validate_delivery_transition(
            current_state="unknown_outcome",
            target_state="reconciling",
            attempt_count=1,
            max_attempts=3,
            reconciliation_observed=False,
        )
        == "reconciling"
    )
    with pytest.raises(DeliveryTransitionError, match="illegal|reconciliation"):
        validate_delivery_transition(
            current_state="unknown_outcome",
            target_state="processing",
            attempt_count=1,
            max_attempts=3,
            reconciliation_observed=False,
        )


def test_retry_cap_and_terminal_non_regression() -> None:
    with pytest.raises(DeliveryTransitionError, match="cap"):
        validate_delivery_transition(
            current_state="retryable_failed",
            target_state="processing",
            attempt_count=3,
            max_attempts=3,
            reconciliation_observed=True,
        )
    with pytest.raises(DeliveryTransitionError, match="terminal"):
        validate_delivery_transition(
            current_state="succeeded",
            target_state="processing",
            attempt_count=1,
            max_attempts=3,
            reconciliation_observed=True,
        )
