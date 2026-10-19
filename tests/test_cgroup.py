"""Unit tests for cgroup v2 validation and read/write on a tmp_path fake hierarchy.

Note: These tests exercise our cgroup reader and writer against a fake directory tree
in tmp_path, not against the Linux kernel cgroupfs implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.cgroup import (
    CgroupError,
    IoMaxLimit,
    read_controllers,
    read_cpu_weight,
    read_io_max,
    read_io_weight,
    write_cpu_weight,
    write_io_max,
    write_io_weight,
)

NL = chr(10)


def _create_fake_cgroup(root: Path, name: str, *, with_controllers: bool = True) -> Path:
    """Create a fake cgroup directory tree inside tmp_path for testing."""
    cg_dir = root / name
    cg_dir.mkdir(parents=True, exist_ok=True)
    if with_controllers:
        (cg_dir / "cgroup.controllers").write_text(f"cpu io memory{NL}", encoding="utf-8")
    return cg_dir


def test_refuses_when_cgroup_controllers_missing(tmp_path: Path) -> None:
    """Refuse to run with a clear CgroupError if cgroup.controllers is missing."""
    cg_dir = _create_fake_cgroup(tmp_path, "example-background", with_controllers=False)
    (cg_dir / "io.weight").write_text(f"default 100{NL}", encoding="utf-8")

    with pytest.raises(CgroupError, match=r"cgroup\.controllers is missing"):
        read_io_weight("example-background", root=tmp_path)

    with pytest.raises(CgroupError, match=r"cgroup\.controllers is missing"):
        write_io_weight("example-background", 50, root=tmp_path)


def test_read_and_write_io_weight_on_fake_tree(tmp_path: Path) -> None:
    """Test io.weight reader and writer against a fake directory tree, not the kernel."""
    cg_dir = _create_fake_cgroup(tmp_path, "example-background")
    (cg_dir / "io.weight").write_text(f"default 100{NL}", encoding="utf-8")

    assert read_controllers("example-background", root=tmp_path) == ["cpu", "io", "memory"]
    assert read_io_weight("example-background", root=tmp_path) == 100

    write_io_weight("example-background", 40, root=tmp_path)
    assert (cg_dir / "io.weight").read_text(encoding="utf-8") == f"40{NL}"
    assert read_io_weight("example-background", root=tmp_path) == 40


def test_read_and_write_cpu_weight_on_fake_tree(tmp_path: Path) -> None:
    """Test cpu.weight reader and writer against a fake directory tree, not the kernel."""
    cg_dir = _create_fake_cgroup(tmp_path, "example-background")
    (cg_dir / "cpu.weight").write_text(f"100{NL}", encoding="utf-8")

    assert read_cpu_weight("example-background", root=tmp_path) == 100

    write_cpu_weight("example-background", 250, root=tmp_path)
    assert (cg_dir / "cpu.weight").read_text(encoding="utf-8") == f"250{NL}"
    assert read_cpu_weight("example-background", root=tmp_path) == 250


@pytest.mark.parametrize("bad_weight", [0, -1, 10001, True, False])
def test_weight_validation_bounds(tmp_path: Path, bad_weight: int) -> None:
    """Reject weights outside [1, 10000] or boolean values."""
    _create_fake_cgroup(tmp_path, "example-background")

    with pytest.raises(ValueError):
        write_io_weight("example-background", bad_weight, root=tmp_path)

    with pytest.raises(ValueError):
        write_cpu_weight("example-background", bad_weight, root=tmp_path)


def test_read_and_write_io_max_on_fake_tree(tmp_path: Path) -> None:
    """Test io.max reader and writer against a fake directory tree, not the kernel."""
    cg_dir = _create_fake_cgroup(tmp_path, "example-background")
    (cg_dir / "io.max").write_text(
        f"8:0 rbps=1048576 wbps=max riops=max wiops=max{NL}",
        encoding="utf-8",
    )

    limits = read_io_max("example-background", root=tmp_path)
    assert limits == {"8:0": IoMaxLimit(device="8:0", rbps=1048576, wbps=None)}

    new_limit = IoMaxLimit(device="259:0", rbps=2097152, wbps=1048576)
    write_io_max("example-background", new_limit, root=tmp_path)
    assert (cg_dir / "io.max").read_text(encoding="utf-8") == (
        f"259:0 rbps=2097152 wbps=1048576{NL}"
    )
    assert read_io_max("example-background", root=tmp_path) == {"259:0": new_limit}


def test_io_max_validation() -> None:
    """Reject invalid device identifiers or non-positive byte limits."""
    with pytest.raises(ValueError):
        IoMaxLimit(device="sda", rbps=1024)

    with pytest.raises(ValueError):
        IoMaxLimit(device="8:0", rbps=0)

    with pytest.raises(ValueError):
        IoMaxLimit(device="8:0", wbps=-10)
