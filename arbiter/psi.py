"""Parser for Linux Pressure Stall Information (PSI) files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROC_ROOT = Path("/proc")
DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")

_REQUIRED_KEYS = frozenset({"avg10", "avg60", "avg300", "total"})


@dataclass(frozen=True, slots=True)
class PsiLine:
    """Single PSI line representing either 'some' or 'full' stall metrics."""

    avg10: float
    avg60: float
    avg300: float
    total: int

    def to_dict(self) -> dict[str, float | int]:
        """Serialize line metrics into a plain dictionary."""
        return {
            "avg10": self.avg10,
            "avg60": self.avg60,
            "avg300": self.avg300,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class PsiMetrics:
    """Parsed PSI snapshot containing 'some' and 'full' stall records."""

    some: PsiLine
    full: PsiLine

    def to_dict(self) -> dict[str, dict[str, float | int]]:
        """Serialize PSI snapshot into a nested dictionary."""
        return {
            "some": self.some.to_dict(),
            "full": self.full.to_dict(),
        }


def _parse_psi_line(raw_line: str) -> tuple[str, PsiLine]:
    parts = raw_line.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Malformed PSI line (expected 5 fields): {raw_line!r}")

    kind = parts[0]
    if kind not in {"some", "full"}:
        raise ValueError(f"Unknown PSI record type {kind!r} in line: {raw_line!r}")

    kv: dict[str, str] = {}
    for token in parts[1:]:
        if "=" not in token:
            raise ValueError(f"Malformed PSI token {token!r} in line: {raw_line!r}")
        key, value = token.split("=", 1)
        if key in kv:
            raise ValueError(f"Duplicate PSI key {key!r} in line: {raw_line!r}")
        kv[key] = value

    if set(kv.keys()) != _REQUIRED_KEYS:
        missing = sorted(_REQUIRED_KEYS - set(kv.keys()))
        extra = sorted(set(kv.keys()) - _REQUIRED_KEYS)
        raise ValueError(f"Invalid PSI keys in line {raw_line!r}: missing={missing}, extra={extra}")

    try:
        avg10 = float(kv["avg10"])
        avg60 = float(kv["avg60"])
        avg300 = float(kv["avg300"])
        total = int(kv["total"])
    except ValueError as exc:
        raise ValueError(f"Non-numeric PSI value in line: {raw_line!r}") from exc

    for field_name, avg_val in (("avg10", avg10), ("avg60", avg60), ("avg300", avg300)):
        if avg_val < 0.0 or avg_val > 100.0:
            raise ValueError(f"PSI {field_name} out of range [0.0, 100.0]: {avg_val}")

    if total < 0:
        raise ValueError(f"PSI total must be non-negative: {total}")

    return kind, PsiLine(avg10=avg10, avg60=avg60, avg300=avg300, total=total)


def parse_psi(content: str, *, require_full: bool = True) -> PsiMetrics:
    """Parse raw text from a Linux PSI file into a PsiMetrics dataclass."""
    records: dict[str, PsiLine] = {}
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        kind, parsed_line = _parse_psi_line(stripped)
        if kind in records:
            raise ValueError(f"Duplicate PSI {kind!r} line in content")
        records[kind] = parsed_line

    if "some" not in records:
        raise ValueError("Missing required 'some' line in PSI content")

    if "full" not in records:
        if require_full:
            raise ValueError("Missing required 'full' line in PSI content")
        records["full"] = PsiLine(avg10=0.0, avg60=0.0, avg300=0.0, total=0)

    return PsiMetrics(some=records["some"], full=records["full"])


def read_psi_file(path: Path, *, require_full: bool = True) -> PsiMetrics:
    """Read and parse a PSI file from the given path."""
    content = path.read_text(encoding="utf-8")
    return parse_psi(content, require_full=require_full)


def read_system_io_pressure(proc_root: Path = DEFAULT_PROC_ROOT) -> PsiMetrics:
    """Read system-wide I/O pressure from <proc_root>/pressure/io."""
    return read_psi_file(proc_root / "pressure" / "io", require_full=True)


def read_system_cpu_pressure(proc_root: Path = DEFAULT_PROC_ROOT) -> PsiMetrics:
    """Read system-wide CPU pressure from <proc_root>/pressure/cpu."""
    return read_psi_file(proc_root / "pressure" / "cpu", require_full=False)


def read_cgroup_io_pressure(
    cgroup: str | Path,
    cgroup_root: Path = DEFAULT_CGROUP_ROOT,
) -> PsiMetrics:
    """Read per-cgroup io.pressure from <cgroup_root>/<cgroup>/io.pressure."""
    rel = str(cgroup).strip().lstrip("/")
    target = cgroup_root / rel if rel else cgroup_root
    return read_psi_file(target / "io.pressure", require_full=True)
