#!/usr/bin/env bash
set -euo pipefail

CGROUP_ROOT="${CGROUP_ROOT:-/sys/fs/cgroup}"
PRIORITY_CGROUP="${CGROUP_ROOT}/example-priority"
BACKGROUND_CGROUP="${CGROUP_ROOT}/example-background"

if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: root privileges are required to configure cgroup v2 directories." >&2
    exit 1
fi

if [[ ! -f "${CGROUP_ROOT}/cgroup.controllers" ]]; then
    echo "Error: cgroup v2 unified hierarchy is required (${CGROUP_ROOT}/cgroup.controllers missing)." >&2
    exit 1
fi

echo "WARNING: About to enable '+io +cpu' subtree control in ${CGROUP_ROOT} and create:"
echo "  - ${PRIORITY_CGROUP}"
echo "  - ${BACKGROUND_CGROUP}"

echo "+io +cpu" > "${CGROUP_ROOT}/cgroup.subtree_control"

mkdir -p "${PRIORITY_CGROUP}"
mkdir -p "${BACKGROUND_CGROUP}"

echo "100" > "${PRIORITY_CGROUP}/io.weight"
echo "100" > "${BACKGROUND_CGROUP}/io.weight"

echo "Created example-priority and example-background cgroups under ${CGROUP_ROOT}."
