"""Unit tests for the pure deterministic arbitration policy."""

from __future__ import annotations

import pytest

from arbiter.policy import (
    HIGH_FULL_AVG10_THRESHOLD,
    HIGH_SOME_AVG10_THRESHOLD,
    LOW_FULL_AVG10_THRESHOLD,
    LOW_SOME_AVG10_THRESHOLD,
    MAX_IO_WEIGHT,
    MIN_DWELL_TICKS,
    MIN_IO_WEIGHT,
    STEP_DOWN,
    STEP_UP,
    PolicyConfig,
    compute_next_weight,
)
from arbiter.psi import PsiLine, PsiMetrics


def _sample(some_avg10: float, full_avg10: float = 0.0) -> PsiMetrics:
    return PsiMetrics(
        some=PsiLine(avg10=some_avg10, avg60=0.0, avg300=0.0, total=0),
        full=PsiLine(avg10=full_avg10, avg60=0.0, avg300=0.0, total=0),
    )


def test_minimum_dwell_ticks_prevents_premature_change() -> None:
    """Weight does not change until at least MIN_DWELL_TICKS samples are observed."""
    high = _sample(HIGH_SOME_AVG10_THRESHOLD + 5.0)

    assert compute_next_weight([], 100) == 100
    assert compute_next_weight([high], 100) == 100
    assert compute_next_weight([high] * (MIN_DWELL_TICKS - 1), 100) == 100
    assert compute_next_weight([high] * MIN_DWELL_TICKS, 100) == 100 - STEP_DOWN

    assert (
        compute_next_weight(
            [high] * MIN_DWELL_TICKS,
            100,
            ticks_since_change=MIN_DWELL_TICKS - 1,
        )
        == 100
    )


def test_hysteresis_deadband_holds_weight_steady() -> None:
    """Pressure inside the deadband between low and high thresholds keeps weight unchanged."""
    mid_some = (LOW_SOME_AVG10_THRESHOLD + HIGH_SOME_AVG10_THRESHOLD) / 2.0
    mid_full = (LOW_FULL_AVG10_THRESHOLD + HIGH_FULL_AVG10_THRESHOLD) / 2.0
    deadband_sample = _sample(mid_some, mid_full)

    history = [deadband_sample] * MIN_DWELL_TICKS
    assert compute_next_weight(history, 60) == 60


def test_full_pressure_triggers_step_down() -> None:
    """Sustained elevated 'full' stall triggers step down even if 'some' is low."""
    full_stall = _sample(some_avg10=0.0, full_avg10=HIGH_FULL_AVG10_THRESHOLD + 1.0)
    history = [full_stall] * MIN_DWELL_TICKS

    assert compute_next_weight(history, 80) == 80 - STEP_DOWN


def test_step_down_and_step_up_sequence_with_bounds() -> None:
    """Verify bounded step-down to MIN_IO_WEIGHT and step-up back to MAX_IO_WEIGHT."""
    high = _sample(HIGH_SOME_AVG10_THRESHOLD + 1.0)
    low = _sample(LOW_SOME_AVG10_THRESHOLD, LOW_FULL_AVG10_THRESHOLD)

    weight = MAX_IO_WEIGHT
    down_weights: list[int] = []
    for _ in range(10):
        weight = compute_next_weight([high] * MIN_DWELL_TICKS, weight)
        down_weights.append(weight)

    assert down_weights == [80, 60, 40, 20, 1, 1, 1, 1, 1, 1]
    assert weight == MIN_IO_WEIGHT

    up_weights: list[int] = []
    for _ in range(12):
        weight = compute_next_weight([low] * MIN_DWELL_TICKS, weight)
        up_weights.append(weight)

    assert up_weights[0] == MIN_IO_WEIGHT + STEP_UP
    assert up_weights == [11, 21, 31, 41, 51, 61, 71, 81, 91, 100, 100, 100]
    assert weight == MAX_IO_WEIGHT


def test_mixed_window_does_not_adjust_weight() -> None:
    """Mixed high and low samples within the dwell window hold current weight."""
    high = _sample(HIGH_SOME_AVG10_THRESHOLD + 2.0)
    low = _sample(0.0, 0.0)

    assert compute_next_weight([high, high, low], 50) == 50
    assert compute_next_weight([low, low, high], 50) == 50


def test_policy_config_validation() -> None:
    """Reject invalid PolicyConfig bounds and thresholds."""
    with pytest.raises(ValueError):
        PolicyConfig(min_weight=0)

    with pytest.raises(ValueError):
        PolicyConfig(min_weight=80, max_weight=50)

    with pytest.raises(ValueError):
        PolicyConfig(low_some_avg10=15.0, high_some_avg10=10.0)
