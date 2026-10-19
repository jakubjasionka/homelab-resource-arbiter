"""Unit tests for PSI parsing from tmp_path fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.psi import (
    PsiLine,
    PsiMetrics,
    parse_psi,
    read_cgroup_io_pressure,
    read_system_cpu_pressure,
    read_system_io_pressure,
)

NL = chr(10)


def test_read_system_io_pressure_from_tmp_path(tmp_path: Path) -> None:
    """Parse /proc/pressure/io from an injected proc_root directory."""
    pressure_dir = tmp_path / "pressure"
    pressure_dir.mkdir(parents=True)
    content = NL.join(
        [
            "some avg10=12.34 avg60=5.60 avg300=1.20 total=987654",
            "full avg10=3.21 avg60=1.10 avg300=0.45 total=123456",
            "",
        ]
    )
    (pressure_dir / "io").write_text(content, encoding="utf-8")

    metrics = read_system_io_pressure(proc_root=tmp_path)

    assert metrics == PsiMetrics(
        some=PsiLine(avg10=12.34, avg60=5.60, avg300=1.20, total=987654),
        full=PsiLine(avg10=3.21, avg60=1.10, avg300=0.45, total=123456),
    )
    assert metrics.to_dict() == {
        "some": {"avg10": 12.34, "avg60": 5.60, "avg300": 1.20, "total": 987654},
        "full": {"avg10": 3.21, "avg60": 1.10, "avg300": 0.45, "total": 123456},
    }


def test_read_system_cpu_pressure_single_some_line(tmp_path: Path) -> None:
    """Parse /proc/pressure/cpu when only the 'some' line is present."""
    pressure_dir = tmp_path / "pressure"
    pressure_dir.mkdir(parents=True)
    (pressure_dir / "cpu").write_text(
        f"some avg10=4.50 avg60=2.25 avg300=0.75 total=42000{NL}",
        encoding="utf-8",
    )

    metrics = read_system_cpu_pressure(proc_root=tmp_path)

    assert metrics.some == PsiLine(avg10=4.50, avg60=2.25, avg300=0.75, total=42000)
    assert metrics.full == PsiLine(avg10=0.0, avg60=0.0, avg300=0.0, total=0)


def test_read_cgroup_io_pressure_from_tmp_path(tmp_path: Path) -> None:
    """Parse per-cgroup io.pressure from an injected cgroup_root directory."""
    cg_dir = tmp_path / "example-priority"
    cg_dir.mkdir(parents=True)
    content = NL.join(
        [
            "some avg10=18.00 avg60=9.50 avg300=4.00 total=555000",
            "full avg10=6.25 avg60=2.00 avg300=0.50 total=111000",
            "",
        ]
    )
    (cg_dir / "io.pressure").write_text(content, encoding="utf-8")

    metrics = read_cgroup_io_pressure("example-priority", cgroup_root=tmp_path)

    assert metrics.some.avg10 == 18.00
    assert metrics.full.avg10 == 6.25
    assert metrics.some.total == 555000
    assert metrics.full.total == 111000


@pytest.mark.parametrize(
    "invalid_lines",
    [
        [],
        ["full avg10=0.00 avg60=0.00 avg300=0.00 total=0"],
        ["some avg10=0.00 avg60=0.00 avg300=0.00 total=0"],
        ["unknown avg10=0.00 avg60=0.00 avg300=0.00 total=0"],
        ["some avg10=0.00 avg60=0.00 total=0"],
        [
            "some avg10=105.00 avg60=0.00 avg300=0.00 total=0",
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0",
        ],
        [
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=-1",
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0",
        ],
        [
            "some avg10=abc avg60=0.00 avg300=0.00 total=0",
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0",
        ],
    ],
)
def test_parse_psi_rejects_malformed_input(invalid_lines: list[str]) -> None:
    """Reject malformed, incomplete, or out-of-range PSI input."""
    with pytest.raises(ValueError):
        parse_psi(NL.join(invalid_lines), require_full=True)
