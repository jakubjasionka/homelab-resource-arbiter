"""Command-line interface for homelab-resource-arbiter."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

from arbiter.cgroup import DEFAULT_CGROUP_ROOT, read_io_weight, resolve_cgroup_path
from arbiter.daemon import ArbiterDaemon, DaemonConfig
from arbiter.finops import calculate_energy_cost
from arbiter.psi import (
    DEFAULT_PROC_ROOT,
    read_cgroup_io_pressure,
    read_system_cpu_pressure,
    read_system_io_pressure,
)

_NL = chr(10)


def _add_common_cgroup_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--priority-cgroup",
        required=True,
        help="Priority service cgroup path relative to cgroup root (e.g. example-priority)",
    )
    parser.add_argument(
        "--background-cgroup",
        required=True,
        help="Background workload cgroup path relative to cgroup root (e.g. example-background)",
    )
    parser.add_argument(
        "--cgroup-root",
        type=Path,
        default=DEFAULT_CGROUP_ROOT,
        help="Path to the cgroup v2 mount root (default: /sys/fs/cgroup)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands: run, once, status, finops."""
    parser = argparse.ArgumentParser(
        prog="arbiter",
        description=(
            "Linux cgroups v2 I/O resource arbiter keeping latency-sensitive services "
            "ahead of background workloads."
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the continuous I/O arbitration loop",
    )
    _add_common_cgroup_args(run_parser)
    run_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single evaluation tick and exit",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log arbitration decisions without writing to cgroup files",
    )

    once_parser = subparsers.add_parser(
        "once",
        help="Execute a single evaluation tick and exit",
    )
    _add_common_cgroup_args(once_parser)
    once_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    once_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log arbitration decision without writing to cgroup files",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Read and print current PSI and io.weight status as JSON",
    )
    _add_common_cgroup_args(status_parser)
    status_parser.add_argument(
        "--proc-root",
        type=Path,
        default=DEFAULT_PROC_ROOT,
        help="Path to the procfs root (default: /proc)",
    )

    finops_parser = subparsers.add_parser(
        "finops",
        help="Calculate energy usage and cost from explicit user-supplied parameters",
    )
    finops_parser.add_argument(
        "--idle-watts",
        type=float,
        required=True,
        help="System power draw in watts when idle (required)",
    )
    finops_parser.add_argument(
        "--load-watts",
        type=float,
        required=True,
        help="System power draw in watts under load (required)",
    )
    finops_parser.add_argument(
        "--kwh-price",
        type=float,
        required=True,
        help="Electricity price per kWh (required)",
    )
    finops_parser.add_argument(
        "--hours",
        type=float,
        required=True,
        help="Duration in hours (required)",
    )
    finops_parser.add_argument(
        "--duty-load",
        type=float,
        required=True,
        help="Fraction of time spent under load in [0.0, 1.0] (required)",
    )

    return parser


def _run_status(args: argparse.Namespace, stream: TextIO) -> int:
    resolve_cgroup_path(args.priority_cgroup, root=args.cgroup_root)
    resolve_cgroup_path(args.background_cgroup, root=args.cgroup_root)

    priority_psi = read_cgroup_io_pressure(args.priority_cgroup, cgroup_root=args.cgroup_root)
    background_psi = read_cgroup_io_pressure(args.background_cgroup, cgroup_root=args.cgroup_root)
    priority_weight = read_io_weight(args.priority_cgroup, root=args.cgroup_root)
    background_weight = read_io_weight(args.background_cgroup, root=args.cgroup_root)

    payload: dict[str, Any] = {
        "priority_cgroup": {
            "name": args.priority_cgroup,
            "io_weight": priority_weight,
            "io_pressure": priority_psi.to_dict(),
        },
        "background_cgroup": {
            "name": args.background_cgroup,
            "io_weight": background_weight,
            "io_pressure": background_psi.to_dict(),
        },
    }

    proc_io = args.proc_root / "pressure" / "io"
    proc_cpu = args.proc_root / "pressure" / "cpu"
    if proc_io.is_file() and proc_cpu.is_file():
        payload["system"] = {
            "io_pressure": read_system_io_pressure(proc_root=args.proc_root).to_dict(),
            "cpu_pressure": read_system_cpu_pressure(proc_root=args.proc_root).to_dict(),
        }

    stream.write(json.dumps(payload, sort_keys=True) + _NL)
    stream.flush()
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    output_stream: TextIO | None = None,
) -> int:
    """Parse CLI arguments and dispatch the selected subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)
    stream = output_stream if output_stream is not None else sys.stdout

    if args.subcommand in {"run", "once"}:
        once_flag = True if args.subcommand == "once" else bool(args.once)
        config = DaemonConfig(
            priority_cgroup=args.priority_cgroup,
            background_cgroup=args.background_cgroup,
            interval=args.interval,
            once=once_flag,
            dry_run=bool(args.dry_run),
            cgroup_root=args.cgroup_root,
        )
        daemon = ArbiterDaemon(config, output_stream=stream)
        return daemon.run()

    if args.subcommand == "status":
        return _run_status(args, stream)

    if args.subcommand == "finops":
        result = calculate_energy_cost(
            idle_watts=args.idle_watts,
            load_watts=args.load_watts,
            kwh_price=args.kwh_price,
            hours=args.hours,
            duty_load=args.duty_load,
        )
        stream.write(json.dumps(result.to_dict(), sort_keys=True) + _NL)
        stream.flush()
        return 0

    parser.error(f"Unsupported subcommand: {args.subcommand}")
    return 2
