"""Unit tests for CLI argument parsing and subcommand execution."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from arbiter.cli import build_parser, main

NL = chr(10)


def _create_fake_cgroups(root: Path) -> None:
    for name in ("example-priority", "example-background"):
        cg = root / name
        cg.mkdir(parents=True, exist_ok=True)
        (cg / "cgroup.controllers").write_text(f"cpu io memory{NL}", encoding="utf-8")
        (cg / "io.weight").write_text(f"100{NL}", encoding="utf-8")
        (cg / "io.pressure").write_text(
            NL.join(
                [
                    "some avg10=1.50 avg60=1.00 avg300=0.50 total=100",
                    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0",
                    "",
                ]
            ),
            encoding="utf-8",
        )


def test_python_m_arbiter_help() -> None:
    """Verify `python -m arbiter --help` and `once --dry-run --help` exit cleanly."""
    res_top = subprocess.run(
        [sys.executable, "-m", "arbiter", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_top.returncode == 0
    assert "run" in res_top.stdout
    assert "once" in res_top.stdout
    assert "status" in res_top.stdout
    assert "finops" in res_top.stdout

    res_once = subprocess.run(
        [sys.executable, "-m", "arbiter", "once", "--dry-run", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_once.returncode == 0
    assert "--priority-cgroup" in res_once.stdout
    assert "--background-cgroup" in res_once.stdout


def test_cli_once_dry_run(tmp_path: Path) -> None:
    """Verify `arbiter once --dry-run` executes one tick and emits JSON logs."""
    _create_fake_cgroups(tmp_path)
    buf = io.StringIO()

    rc = main(
        [
            "once",
            "--priority-cgroup",
            "example-priority",
            "--background-cgroup",
            "example-background",
            "--cgroup-root",
            str(tmp_path),
            "--dry-run",
        ],
        output_stream=buf,
    )
    assert rc == 0
    lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["event"] == "tick"
    assert lines[0]["dry_run"] is True
    assert lines[1]["event"] == "restore"


def test_cli_status_subcommand(tmp_path: Path) -> None:
    """Verify `arbiter status` prints cgroup and system PSI JSON."""
    _create_fake_cgroups(tmp_path)
    proc_dir = tmp_path / "proc" / "pressure"
    proc_dir.mkdir(parents=True)
    (proc_dir / "io").write_text(
        NL.join(
            [
                "some avg10=2.00 avg60=1.00 avg300=0.50 total=200",
                "full avg10=0.50 avg60=0.20 avg300=0.10 total=50",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (proc_dir / "cpu").write_text(
        f"some avg10=3.00 avg60=1.50 avg300=0.75 total=300{NL}",
        encoding="utf-8",
    )

    buf = io.StringIO()
    rc = main(
        [
            "status",
            "--priority-cgroup",
            "example-priority",
            "--background-cgroup",
            "example-background",
            "--cgroup-root",
            str(tmp_path),
            "--proc-root",
            str(tmp_path / "proc"),
        ],
        output_stream=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["priority_cgroup"]["io_weight"] == 100
    assert payload["background_cgroup"]["io_weight"] == 100
    assert payload["system"]["cpu_pressure"]["some"]["avg10"] == 3.00


def test_cli_finops_requires_all_flags() -> None:
    """Verify `arbiter finops` fails if any required flag is omitted and succeeds when given."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["finops", "--idle-watts", "50"])

    buf = io.StringIO()
    rc = main(
        [
            "finops",
            "--idle-watts",
            "50",
            "--load-watts",
            "150",
            "--kwh-price",
            "0.20",
            "--hours",
            "10",
            "--duty-load",
            "0.5",
        ],
        output_stream=buf,
    )
    assert rc == 0
    data = json.loads(buf.getvalue())
    assert data["average_watts"] == pytest.approx(100.0)
    assert data["kwh"] == pytest.approx(1.0)
    assert data["cost"] == pytest.approx(0.20)
