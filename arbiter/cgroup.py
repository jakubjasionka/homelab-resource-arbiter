"""Linux cgroups v2 reader and writer for io.weight, io.max, and cpu.weight."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")

MIN_WEIGHT = 1
MAX_WEIGHT = 10000

_DEVICE_PATTERN = re.compile(r"^[0-9]+:[0-9]+$")
_NL = chr(10)


class CgroupError(RuntimeError):
    """Raised when a cgroup v2 hierarchy or controller precondition fails."""


@dataclass(frozen=True, slots=True)
class IoMaxLimit:
    """Per-device cgroup v2 io.max bandwidth limit (None represents 'max')."""

    device: str
    rbps: int | None = None
    wbps: int | None = None

    def __post_init__(self) -> None:
        validate_device_id(self.device)
        if self.rbps is not None:
            _validate_positive_int(self.rbps, "rbps")
        if self.wbps is not None:
            _validate_positive_int(self.wbps, "wbps")

    def format_line(self) -> str:
        """Format as a cgroup v2 io.max control line."""
        rbps_str = "max" if self.rbps is None else str(self.rbps)
        wbps_str = "max" if self.wbps is None else str(self.wbps)
        return f"{self.device} rbps={rbps_str} wbps={wbps_str}"


def _validate_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer >= 1, got {value!r}")


def validate_weight(weight: int, field_name: str = "weight") -> None:
    """Validate that a cgroup v2 weight is an integer within [1, 10000]."""
    if isinstance(weight, bool) or not isinstance(weight, int):
        raise ValueError(f"{field_name} must be an integer, got {weight!r}")
    if weight < MIN_WEIGHT or weight > MAX_WEIGHT:
        raise ValueError(
            f"{field_name} must be between {MIN_WEIGHT} and {MAX_WEIGHT}, got {weight}"
        )


def validate_device_id(device: str) -> None:
    """Validate a block device major:minor identifier (e.g. '8:0')."""
    if not _DEVICE_PATTERN.match(device):
        raise ValueError(f"Invalid block device identifier (expected 'MAJ:MIN'): {device!r}")


def resolve_cgroup_path(cgroup: str | Path, root: Path = DEFAULT_CGROUP_ROOT) -> Path:
    """Resolve a cgroup path relative to root and verify cgroup.controllers exists."""
    rel = str(cgroup).strip().lstrip("/")
    cgroup_dir = (root / rel).resolve() if rel else root.resolve()
    root_resolved = root.resolve()

    if cgroup_dir != root_resolved and root_resolved not in cgroup_dir.parents:
        raise ValueError(f"Cgroup path {cgroup!r} escapes root {root}")

    if not cgroup_dir.is_dir():
        raise CgroupError(f"Cgroup directory does not exist: {cgroup_dir}")

    controllers_file = cgroup_dir / "cgroup.controllers"
    if not controllers_file.is_file():
        raise CgroupError(
            f"Refusing to run: cgroup v2 required, but {controllers_file} is missing"
        )

    return cgroup_dir


def read_controllers(cgroup: str | Path = "", root: Path = DEFAULT_CGROUP_ROOT) -> list[str]:
    """Read the list of available controllers from cgroup.controllers."""
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    raw = (cgroup_dir / "cgroup.controllers").read_text(encoding="utf-8").strip()
    return raw.split() if raw else []


def _parse_weight_content(content: str, field_name: str) -> int:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Empty {field_name} file")

    tokens = lines[0].split()
    raw_value = tokens[1] if len(tokens) == 2 and tokens[0] == "default" else tokens[0]
    if len(tokens) > 2 or (len(tokens) == 2 and tokens[0] != "default"):
        raise ValueError(f"Malformed {field_name} content: {lines[0]!r}")

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Non-integer {field_name} value: {raw_value!r}") from exc

    validate_weight(value, field_name=field_name)
    return value


def read_io_weight(cgroup: str | Path, root: Path = DEFAULT_CGROUP_ROOT) -> int:
    """Read io.weight (1-10000) from the target cgroup."""
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    content = (cgroup_dir / "io.weight").read_text(encoding="utf-8")
    return _parse_weight_content(content, field_name="io.weight")


def write_io_weight(cgroup: str | Path, weight: int, root: Path = DEFAULT_CGROUP_ROOT) -> None:
    """Write io.weight (1-10000) to the target cgroup."""
    validate_weight(weight, field_name="io.weight")
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    (cgroup_dir / "io.weight").write_text(f"{weight}{_NL}", encoding="utf-8")


def read_cpu_weight(cgroup: str | Path, root: Path = DEFAULT_CGROUP_ROOT) -> int:
    """Read cpu.weight (1-10000) from the target cgroup."""
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    content = (cgroup_dir / "cpu.weight").read_text(encoding="utf-8")
    return _parse_weight_content(content, field_name="cpu.weight")


def write_cpu_weight(cgroup: str | Path, weight: int, root: Path = DEFAULT_CGROUP_ROOT) -> None:
    """Write cpu.weight (1-10000) to the target cgroup."""
    validate_weight(weight, field_name="cpu.weight")
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    (cgroup_dir / "cpu.weight").write_text(f"{weight}{_NL}", encoding="utf-8")


def _parse_limit_token(raw: str, key: str) -> int | None:
    if raw == "max":
        return None
    try:
        val = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {key} value in io.max: {raw!r}") from exc
    _validate_positive_int(val, key)
    return val


def read_io_max(cgroup: str | Path, root: Path = DEFAULT_CGROUP_ROOT) -> dict[str, IoMaxLimit]:
    """Read per-device rbps/wbps limits from io.max."""
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    content = (cgroup_dir / "io.max").read_text(encoding="utf-8")
    limits: dict[str, IoMaxLimit] = {}

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        device = parts[0]
        validate_device_id(device)

        rbps: int | None = None
        wbps: int | None = None
        for token in parts[1:]:
            if "=" not in token:
                raise ValueError(f"Malformed io.max token {token!r} in line {raw_line!r}")
            k, v = token.split("=", 1)
            if k == "rbps":
                rbps = _parse_limit_token(v, "rbps")
            elif k == "wbps":
                wbps = _parse_limit_token(v, "wbps")

        limits[device] = IoMaxLimit(device=device, rbps=rbps, wbps=wbps)

    return limits


def write_io_max(
    cgroup: str | Path,
    limit: IoMaxLimit,
    root: Path = DEFAULT_CGROUP_ROOT,
) -> None:
    """Write a per-device rbps/wbps limit line to io.max."""
    cgroup_dir = resolve_cgroup_path(cgroup, root=root)
    (cgroup_dir / "io.max").write_text(f"{limit.format_line()}{_NL}", encoding="utf-8")
