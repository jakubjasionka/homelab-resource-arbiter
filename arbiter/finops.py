"""Explicit flag-driven energy cost calculator (no built-in hardware or tariff defaults)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FinOpsResult:
    """Computed energy consumption and monetary cost from explicit user parameters."""

    idle_watts: float
    load_watts: float
    kwh_price: float
    hours: float
    duty_load: float
    average_watts: float
    watt_hours: float
    kwh: float
    cost: float

    def to_dict(self) -> dict[str, float]:
        """Serialize the calculation inputs and outputs to a plain dictionary."""
        return {
            "idle_watts": self.idle_watts,
            "load_watts": self.load_watts,
            "kwh_price": self.kwh_price,
            "hours": self.hours,
            "duty_load": self.duty_load,
            "average_watts": self.average_watts,
            "watt_hours": self.watt_hours,
            "kwh": self.kwh,
            "cost": self.cost,
        }


def _validate_finite_non_negative(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    as_float = float(value)
    if not math.isfinite(as_float) or as_float < 0.0:
        raise ValueError(f"{name} must be a finite number >= 0.0, got {value!r}")
    return as_float


def calculate_energy_cost(
    *,
    idle_watts: float,
    load_watts: float,
    kwh_price: float,
    hours: float,
    duty_load: float,
) -> FinOpsResult:
    """Compute energy usage (kWh) and cost from explicit user-supplied inputs only."""
    idle_w = _validate_finite_non_negative(idle_watts, "idle_watts")
    load_w = _validate_finite_non_negative(load_watts, "load_watts")
    price = _validate_finite_non_negative(kwh_price, "kwh_price")
    duration_h = _validate_finite_non_negative(hours, "hours")
    duty = _validate_finite_non_negative(duty_load, "duty_load")

    if load_w < idle_w:
        raise ValueError(f"load_watts ({load_w}) must be >= idle_watts ({idle_w})")
    if duty > 1.0:
        raise ValueError(f"duty_load must be within [0.0, 1.0], got {duty}")

    average_watts = idle_w * (1.0 - duty) + load_w * duty
    watt_hours = average_watts * duration_h
    kwh = watt_hours / 1000.0
    cost = kwh * price

    return FinOpsResult(
        idle_watts=idle_w,
        load_watts=load_w,
        kwh_price=price,
        hours=duration_h,
        duty_load=duty,
        average_watts=average_watts,
        watt_hours=watt_hours,
        kwh=kwh,
        cost=cost,
    )
