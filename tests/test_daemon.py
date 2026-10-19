"""Unit tests for the arbitration daemon against a fake tmp_path cgroup hierarchy.

Note: These tests exercise our daemon and cgroup writer against a fake directory tree
in tmp_path, not against the Linux kernel cgroupfs implementation.
"""

from __future__ import annotations

import io
import json
import signal
from pathlib import Path

from arbiter.cgroup import read_io_weight
from arbiter.daemon import ArbiterDaemon, DaemonConfig

NL = chr(10)


def _setup_fake_hierarchy(
    root: Path,
    *,
    priority_some: float = 25.0,
    background_weight: int = 100,
) -> tuple[Path, Path]:
    """Create a fake priority and background cgroup tree in tmp_path."""
    prio = root / "example-priority"
    bg = root / "example-background"
    prio.mkdir(parents=True)
    bg.mkdir(parents=True)

    for cg in (prio, bg):
        (cg / "cgroup.controllers").write_text(f"cpu io memory{NL}", encoding="utf-8")

    (prio / "io.weight").write_text(f"100{NL}", encoding="utf-8")
    (bg / "io.weight").write_text(f"{background_weight}{NL}", encoding="utf-8")

    (prio / "io.pressure").write_text(
        NL.join(
            [
                f"some avg10={priority_some:.2f} avg60=5.00 avg300=1.00 total=1000",
                "full avg10=0.00 avg60=0.00 avg300=0.00 total=0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (bg / "io.pressure").write_text(
        NL.join(
            [
                "some avg10=50.00 avg60=20.00 avg300=10.00 total=5000",
                "full avg10=10.00 avg60=5.00 avg300=2.00 total=2000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return prio, bg


def test_daemon_adjusts_weight_and_restores_on_exit(tmp_path: Path) -> None:
    """Verify multi-tick adjustment under pressure and restoration of original weight on exit."""
    _, bg = _setup_fake_hierarchy(tmp_path, priority_some=25.0, background_weight=100)
    observed_weights_during_sleep: list[int] = []

    def fake_sleep(_seconds: float) -> None:
        observed_weights_during_sleep.append(read_io_weight("example-background", root=tmp_path))

    buf = io.StringIO()
    config = DaemonConfig(
        priority_cgroup="example-priority",
        background_cgroup="example-background",
        interval=0.5,
        dry_run=False,
        cgroup_root=tmp_path,
        max_ticks=3,
    )
    daemon = ArbiterDaemon(config, sleep_fn=fake_sleep, output_stream=buf)
    rc = daemon.run()

    assert rc == 0
    # Ticks 1 and 2 dwell at 100; tick 3 reduces weight to 80; finally restores to 100 on exit.
    assert observed_weights_during_sleep == [100, 100]
    assert read_io_weight("example-background", root=tmp_path) == 100
    assert (bg / "io.weight").read_text(encoding="utf-8") == f"100{NL}"

    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    tick_events = [e for e in events if e["event"] == "tick"]
    restore_events = [e for e in events if e["event"] == "restore"]

    assert len(tick_events) == 3
    assert tick_events[0]["new_weight"] == 100
    assert tick_events[1]["new_weight"] == 100
    assert tick_events[2]["new_weight"] == 80
    assert tick_events[2]["changed"] is True
    assert len(restore_events) == 1
    assert restore_events[0]["restored_weight"] == 100


def test_daemon_dry_run_never_writes_files(tmp_path: Path) -> None:
    """Verify dry-run logs weight transitions without modifying io.weight on disk."""
    _, bg = _setup_fake_hierarchy(tmp_path, priority_some=25.0, background_weight=100)
    (bg / "io.weight").write_text(f"default 100{NL}", encoding="utf-8")

    buf = io.StringIO()
    config = DaemonConfig(
        priority_cgroup="example-priority",
        background_cgroup="example-background",
        interval=0.1,
        dry_run=True,
        cgroup_root=tmp_path,
        max_ticks=3,
    )
    daemon = ArbiterDaemon(config, sleep_fn=lambda _: None, output_stream=buf)
    daemon.run()

    # Exact file content is untouched.
    assert (bg / "io.weight").read_text(encoding="utf-8") == f"default 100{NL}"
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    assert events[2]["new_weight"] == 80
    assert events[2]["dry_run"] is True


def test_daemon_sigterm_graceful_stop(tmp_path: Path) -> None:
    """Verify SIGTERM stops the loop gracefully and restores original weight."""
    _setup_fake_hierarchy(tmp_path, priority_some=25.0, background_weight=90)
    buf = io.StringIO()
    config = DaemonConfig(
        priority_cgroup="example-priority",
        background_cgroup="example-background",
        interval=1.0,
        cgroup_root=tmp_path,
    )

    daemon_ref: list[ArbiterDaemon] = []

    def stop_on_sleep(_seconds: float) -> None:
        daemon_ref[0]._handle_signal(signal.SIGTERM, None)

    daemon = ArbiterDaemon(config, sleep_fn=stop_on_sleep, output_stream=buf)
    daemon_ref.append(daemon)
    rc = daemon.run()

    assert rc == 0
    events = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
    event_types = [e["event"] for e in events]
    assert "signal" in event_types
    assert event_types[-1] == "restore"
    assert read_io_weight("example-background", root=tmp_path) == 90
