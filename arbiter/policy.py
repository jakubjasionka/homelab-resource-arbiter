"""Pure deterministic I/O weight arbitration policy based on PSI history.

All threshold, step, and dwell constants in this module are reference values and are
explicitly documented as "not tuned". No filesystem access is performed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from arbiter.psi import PsiMetrics

# Reference policy constants (not tuned).
HIGH_SOME_AVG10_THRESHOLD: float = 10.0  # not tuned: high 'some' stall threshold (%)
HIGH_FULL_AVG10_THRESHOLD: float = 2.0  # not tuned: high 'full' stall threshold (%)
LOW_SOME_AVG10_THRESHOLD: float = 2.0  # not tuned: low 'some' stall recovery threshold (%)
LOW_FULL_AVG10_THRESHOLD: float = 0.5  # not tuned: low 'full' stall recovery threshold (%)
STEP_DOWN: int = 20  # not tuned: bounded io.weight reduction step
STEP_UP: int = 10  # not tuned: bounded io.weight recovery step
MIN_DWELL_TICKS: int = 3  # not tuned: minimum ticks required before changing weight
MIN_IO_WEIGHT: int = 1  # cgroup v2 minimum weight bound
MAX_IO_WEIGHT: int = 100  # not tuned: default upper ceiling for background io.weight
KERNEL_MAX_IO_WEIGHT: int = 10000  # cgroup v2 maximum weight bound


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    """Configuration parameters for the deterministic hysteresis policy (not tuned)."""

    high_some_avg10: float = HIGH_SOME_AVG10_THRESHOLD
    high_full_avg10: float = HIGH_FULL_AVG10_THRESHOLD
    low_some_avg10: float = LOW_SOME_AVG10_THRESHOLD
    low_full_avg10: float = LOW_FULL_AVG10_THRESHOLD
    step_down: int = STEP_DOWN
    step_up: int = STEP_UP
    min_dwell_ticks: int = MIN_DWELL_TICKS
    min_weight: int = MIN_IO_WEIGHT
    max_weight: int = MAX_IO_WEIGHT

    def __post_init__(self) -> None:
        if self.min_weight < MIN_IO_WEIGHT or self.max_weight > KERNEL_MAX_IO_WEIGHT:
            raise ValueError(
                f"Weight bounds must be within [{MIN_IO_WEIGHT}, {KERNEL_MAX_IO_WEIGHT}]"
            )
        if self.min_weight > self.max_weight:
            raise ValueError("min_weight must not exceed max_weight")
        if self.step_down < 1 or self.step_up < 1:
            raise ValueError("step_down and step_up must be >= 1")
        if self.min_dwell_ticks < 1:
            raise ValueError("min_dwell_ticks must be >= 1")
        if self.low_some_avg10 > self.high_some_avg10:
            raise ValueError("low_some_avg10 must not exceed high_some_avg10")
        if self.low_full_avg10 > self.high_full_avg10:
            raise ValueError("low_full_avg10 must not exceed high_full_avg10")


def _is_high_pressure(sample: PsiMetrics, config: PolicyConfig) -> bool:
    return (
        sample.some.avg10 >= config.high_some_avg10
        or sample.full.avg10 >= config.high_full_avg10
    )


def _is_low_pressure(sample: PsiMetrics, config: PolicyConfig) -> bool:
    return (
        sample.some.avg10 <= config.low_some_avg10
        and sample.full.avg10 <= config.low_full_avg10
    )


DEFAULT_POLICY_CONFIG = PolicyConfig()


def compute_next_weight(
    priority_psi_history: Sequence[PsiMetrics],
    current_weight: int,
    *,
    ticks_since_change: int | None = None,
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
) -> int:
    """Compute the next background io.weight from priority PSI history and current weight.

    Applies hysteresis with high/low pressure thresholds, bounded step sizes, and a
    minimum dwell requirement of ``config.min_dwell_ticks`` samples/ticks.
    """
    if isinstance(current_weight, bool) or not isinstance(current_weight, int):
        raise ValueError(f"current_weight must be an integer, got {current_weight!r}")
    if current_weight < MIN_IO_WEIGHT or current_weight > KERNEL_MAX_IO_WEIGHT:
        raise ValueError(
            f"current_weight must be in [{MIN_IO_WEIGHT}, {KERNEL_MAX_IO_WEIGHT}], "
            f"got {current_weight}"
        )

    clamped_current = max(config.min_weight, min(config.max_weight, current_weight))

    if len(priority_psi_history) < config.min_dwell_ticks:
        return clamped_current

    if ticks_since_change is not None and ticks_since_change < config.min_dwell_ticks:
        return clamped_current

    dwell_window = priority_psi_history[-config.min_dwell_ticks :]

    if all(_is_high_pressure(sample, config) for sample in dwell_window):
        return max(config.min_weight, clamped_current - config.step_down)

    if all(_is_low_pressure(sample, config) for sample in dwell_window):
        return min(config.max_weight, clamped_current + config.step_up)

    return clamped_current
