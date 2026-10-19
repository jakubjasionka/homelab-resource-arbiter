"""Unit tests verifying the FinOps energy cost formula with caller-supplied inputs."""

from __future__ import annotations

import inspect

import pytest

from arbiter.finops import calculate_energy_cost


def test_calculate_energy_cost_has_no_defaults() -> None:
    """Ensure all parameters to calculate_energy_cost are required with no defaults."""
    sig = inspect.signature(calculate_energy_cost)
    for param in sig.parameters.values():
        assert param.default is inspect.Parameter.empty


def test_calculate_energy_cost_formula() -> None:
    """Verify exact arithmetic for average watts, watt-hours, kWh, and cost."""
    result = calculate_energy_cost(
        idle_watts=50.0,
        load_watts=150.0,
        kwh_price=0.25,
        hours=10.0,
        duty_load=0.4,
    )

    # average_watts = 50 * 0.6 + 150 * 0.4 = 30 + 60 = 90 W
    assert result.average_watts == pytest.approx(90.0)
    # watt_hours = 90 W * 10 h = 900 Wh
    assert result.watt_hours == pytest.approx(900.0)
    # kwh = 900 / 1000 = 0.9 kWh
    assert result.kwh == pytest.approx(0.9)
    # cost = 0.9 * 0.25 = 0.225
    assert result.cost == pytest.approx(0.225)


def test_calculate_energy_cost_boundary_duty_loads() -> None:
    """Verify formula at 0.0 and 1.0 duty cycle boundaries."""
    idle_only = calculate_energy_cost(
        idle_watts=40.0,
        load_watts=120.0,
        kwh_price=0.5,
        hours=20.0,
        duty_load=0.0,
    )
    assert idle_only.average_watts == pytest.approx(40.0)
    assert idle_only.kwh == pytest.approx(0.8)
    assert idle_only.cost == pytest.approx(0.4)

    full_load = calculate_energy_cost(
        idle_watts=40.0,
        load_watts=120.0,
        kwh_price=0.5,
        hours=20.0,
        duty_load=1.0,
    )
    assert full_load.average_watts == pytest.approx(120.0)
    assert full_load.kwh == pytest.approx(2.4)
    assert full_load.cost == pytest.approx(1.2)


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "idle_watts": -1.0,
            "load_watts": 100.0,
            "kwh_price": 0.1,
            "hours": 1.0,
            "duty_load": 0.5,
        },
        {
            "idle_watts": 100.0,
            "load_watts": 50.0,
            "kwh_price": 0.1,
            "hours": 1.0,
            "duty_load": 0.5,
        },
        {
            "idle_watts": 50.0,
            "load_watts": 100.0,
            "kwh_price": -0.1,
            "hours": 1.0,
            "duty_load": 0.5,
        },
        {
            "idle_watts": 50.0,
            "load_watts": 100.0,
            "kwh_price": 0.1,
            "hours": -1.0,
            "duty_load": 0.5,
        },
        {
            "idle_watts": 50.0,
            "load_watts": 100.0,
            "kwh_price": 0.1,
            "hours": 1.0,
            "duty_load": -0.1,
        },
        {
            "idle_watts": 50.0,
            "load_watts": 100.0,
            "kwh_price": 0.1,
            "hours": 1.0,
            "duty_load": 1.1,
        },
    ],
)
def test_calculate_energy_cost_rejects_invalid_inputs(kwargs: dict[str, float]) -> None:
    """Reject negative values, load_watts < idle_watts, or duty_load outside [0, 1]."""
    with pytest.raises(ValueError):
        calculate_energy_cost(**kwargs)
