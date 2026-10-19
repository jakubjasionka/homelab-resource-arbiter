# homelab-resource-arbiter

Linux cgroups v2 I/O resource arbiter that keeps a latency-sensitive priority workload ahead of background workloads using Linux Pressure Stall Information (PSI) and `io.weight`.

## Problem

When a latency-sensitive service (`example-priority`) shares block storage with batch or background workloads (`example-background`), bursty background I/O can induce queuing delays and stall the priority service. Static I/O limits either throttle background throughput unnecessarily when the priority service is idle or fail to react when contention spikes.

`homelab-resource-arbiter` polls per-cgroup `io.pressure` metrics and dynamically adjusts the background cgroup's `io.weight` within bounded limits using deterministic hysteresis.

## Architecture

```text
+-------------------------------------------------------------------+
|                           Linux Kernel                            |
|                                                                   |
|  /proc/pressure/{io,cpu}                                          |
|  /sys/fs/cgroup/                                                  |
|    +-- example-priority/                                          |
|    |     +-- io.pressure  (some / full stall averages)            |
|    |     +-- io.weight                                            |
|    +-- example-background/                                        |
|          +-- io.pressure                                          |
|          +-- io.weight    <-- adjusted by arbiter (1..10000)      |
+-------------------------------------------------------------------+
                 |                                  ^
                 | 1. Read PSI & weights            | 3. Write io.weight
                 v                                  |    (restore on exit)
+-------------------------------------------------------------------+
|                     homelab-resource-arbiter                      |
|                                                                   |
|  [arbiter.psi] ---> [arbiter.policy] ---> [arbiter.cgroup]        |
|                           ^                                       |
|                     2. Pure hysteresis                            |
|                        (thresholds, step bounds, dwell ticks)     |
+-------------------------------------------------------------------+
```

## Requirements

- **Operating System:** Linux with kernel PSI enabled (`CONFIG_PSI=y`, `/proc/pressure/*` and per-cgroup `io.pressure`).
- **Control Groups:** Unified cgroup v2 hierarchy mounted at `/sys/fs/cgroup` with the `io` controller enabled in `cgroup.subtree_control`.
- **Permissions:** Root privileges (or delegated write permissions on the target cgroup's `io.weight`) are required when running without `--dry-run`.
- **Runtime:** Python `>= 3.11` with zero third-party runtime dependencies.

## Quick Start

Install in editable mode with development dependencies, run the test suite, and inspect CLI usage:

```bash
pip install -e .[dev]
pytest
python -m arbiter once --dry-run --help
```

### CLI Subcommands

- **Single evaluation tick (dry-run):**
  ```bash
  python -m arbiter once --priority-cgroup example-priority --background-cgroup example-background --dry-run
  ```
- **Continuous arbitration daemon:**
  ```bash
  python -m arbiter run --priority-cgroup example-priority --background-cgroup example-background --interval 1.0
  ```
- **Inspect current cgroup and system PSI status:**
  ```bash
  python -m arbiter status --priority-cgroup example-priority --background-cgroup example-background
  ```
- **Calculate energy cost from explicit user-supplied parameters (no built-in defaults):**
  ```bash
  python -m arbiter finops --idle-watts <IDLE_WATTS> --load-watts <LOAD_WATTS> --kwh-price <PRICE_PER_KWH> --hours <HOURS> --duty-load <FRACTION_0_TO_1>
  ```

## Safety

- **Restore on exit:** The daemon records the initial `io.weight` of `--background-cgroup` before entering the control loop and restores that original weight in a `finally` block on normal completion, exception, `SIGINT`, or `SIGTERM`.
- **Strict bounds validation:** All cgroup weights are validated as integers within the kernel range `[1, 10000]`. Policy adjustments are clamped to configured minimum and maximum bounds and change by bounded step sizes only after `MIN_DWELL_TICKS` consecutive observations.
- **Precondition checks:** The cgroup controller refuses to run if `cgroup.controllers` is missing in the target cgroup hierarchy.
- **Dry-run mode:** Passing `--dry-run` executes the full PSI read and policy evaluation pipeline and emits structured JSON logs without modifying any files in `/sys/fs/cgroup`.

## Benchmarks

Not measured yet.

### Benchmark Procedure

To measure the effect of `homelab-resource-arbiter` on your own hardware:

1. Provision two cgroup v2 slices (`example-priority` and `example-background`) using `deploy/setup_example_cgroups.sh` on a Linux host with a physical block device that supports BFQ/kyber proportional I/O scheduling.
2. Place a reproducible latency-sensitive workload inside `example-priority` and a competing sequential/random I/O generator inside `example-background`.
3. Record baseline tail latency and `io.pressure` (`some` and `full` `avg10`/`total`) with both groups fixed at default `io.weight` (`100`).
4. Run `python -m arbiter run --priority-cgroup example-priority --background-cgroup example-background --interval 1.0`, capture the emitted JSON log stream, and compare the priority workload's latency distribution and stall time against the baseline run.

## Limitations

- **Policy constants are not tuned:** Thresholds, step sizes, and dwell counts in `arbiter/policy.py` are reference constants documented as `"not tuned"` and must be calibrated for specific storage devices and workload SLAs.
- **Kernel I/O scheduler dependency:** `io.weight` proportional control in cgroup v2 requires an underlying block layer configuration and scheduler (such as BFQ or IOCost) that honors proportional weights on the target device.
- **Buffered writeback accounting:** Kernel page-cache writeback attribution depends on proper memory and I/O controller integration; unthrottled dirty-page flushes may still cause latency spikes before `io.weight` takes effect.
