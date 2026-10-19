"""Arbitration daemon loop reading cgroup PSI and adjusting background io.weight."""

from __future__ import annotations

import json
import signal
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, TextIO

from arbiter.cgroup import (
    DEFAULT_CGROUP_ROOT,
    read_io_weight,
    resolve_cgroup_path,
    write_io_weight,
)
from arbiter.policy import (
    DEFAULT_POLICY_CONFIG,
    PolicyConfig,
    compute_next_weight,
)
from arbiter.psi import PsiMetrics, read_cgroup_io_pressure

_NL = chr(10)


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    """Runtime configuration for the I/O arbitration loop."""

    priority_cgroup: str
    background_cgroup: str
    interval: float = 1.0
    once: bool = False
    dry_run: bool = False
    cgroup_root: Path = DEFAULT_CGROUP_ROOT
    policy: PolicyConfig = DEFAULT_POLICY_CONFIG
    max_ticks: int | None = None

    def __post_init__(self) -> None:
        if self.interval <= 0.0:
            raise ValueError(f"interval must be > 0.0, got {self.interval}")
        if self.max_ticks is not None and self.max_ticks < 1:
            raise ValueError(f"max_ticks must be >= 1, got {self.max_ticks}")


class ArbiterDaemon:
    """Daemon that monitors priority cgroup PSI and adjusts background io.weight."""

    def __init__(
        self,
        config: DaemonConfig,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        output_stream: TextIO | None = None,
    ) -> None:
        self._config = config
        self._sleep_fn = sleep_fn
        self._output_stream = output_stream
        self._stop_requested = False

    def request_stop(self) -> None:
        """Request a graceful stop at the end of the current tick."""
        self._stop_requested = True

    def _handle_signal(self, signum: int, _frame: FrameType | None) -> None:
        self._emit_log({"event": "signal", "signal": signum})
        self._stop_requested = True

    def _emit_log(self, payload: dict[str, Any]) -> None:
        stream = self._output_stream if self._output_stream is not None else sys.stdout
        record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            **payload,
        }
        stream.write(json.dumps(record, sort_keys=True) + _NL)
        stream.flush()

    def run(self) -> int:
        """Run the arbitration loop and restore original background io.weight on exit."""
        resolve_cgroup_path(self._config.priority_cgroup, root=self._config.cgroup_root)
        resolve_cgroup_path(self._config.background_cgroup, root=self._config.cgroup_root)

        original_weight = read_io_weight(
            self._config.background_cgroup,
            root=self._config.cgroup_root,
        )
        current_weight = original_weight

        history_maxlen = max(self._config.policy.min_dwell_ticks * 4, 16)
        history: deque[PsiMetrics] = deque(maxlen=history_maxlen)
        ticks_since_change = self._config.policy.min_dwell_ticks

        prev_sigterm = signal.getsignal(signal.SIGTERM)
        prev_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        tick = 0
        try:
            while not self._stop_requested:
                tick += 1
                priority_psi = read_cgroup_io_pressure(
                    self._config.priority_cgroup,
                    cgroup_root=self._config.cgroup_root,
                )
                background_psi = read_cgroup_io_pressure(
                    self._config.background_cgroup,
                    cgroup_root=self._config.cgroup_root,
                )

                history.append(priority_psi)
                ticks_since_change += 1

                next_weight = compute_next_weight(
                    list(history),
                    current_weight,
                    ticks_since_change=ticks_since_change,
                    config=self._config.policy,
                )
                changed = next_weight != current_weight

                if changed:
                    if not self._config.dry_run:
                        write_io_weight(
                            self._config.background_cgroup,
                            next_weight,
                            root=self._config.cgroup_root,
                        )
                    ticks_since_change = 0

                self._emit_log(
                    {
                        "event": "tick",
                        "tick": tick,
                        "priority_cgroup": self._config.priority_cgroup,
                        "background_cgroup": self._config.background_cgroup,
                        "priority_psi": priority_psi.to_dict(),
                        "background_psi": background_psi.to_dict(),
                        "previous_weight": current_weight,
                        "new_weight": next_weight,
                        "changed": changed,
                        "dry_run": self._config.dry_run,
                    }
                )
                current_weight = next_weight

                if self._config.once:
                    break
                if self._config.max_ticks is not None and tick >= self._config.max_ticks:
                    break
                if self._stop_requested:
                    break

                self._sleep_fn(self._config.interval)
        finally:
            signal.signal(signal.SIGTERM, prev_sigterm)
            signal.signal(signal.SIGINT, prev_sigint)
            if not self._config.dry_run:
                write_io_weight(
                    self._config.background_cgroup,
                    original_weight,
                    root=self._config.cgroup_root,
                )
            self._emit_log(
                {
                    "event": "restore",
                    "background_cgroup": self._config.background_cgroup,
                    "restored_weight": original_weight,
                    "dry_run": self._config.dry_run,
                }
            )

        return 0
