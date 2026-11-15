#!/usr/bin/env bash
# Run the test suite under a hard memory ceiling.
#
# Why: a leaking test can grow to ~28GB and trip the *global* OOM killer, which
# then kills unrelated processes on this host (docker containers, x11vnc, agent
# sessions). Confining the run to its own cgroup means a runaway test dies by
# itself and everything else keeps running.
#
# MemorySwapMax=0 also keeps a leak from thrashing swap before it dies.
#
# Usage: ./run-tests.sh [pytest args...]
#   MEM_MAX=4G ./run-tests.sh tests/test_system.py
set -euo pipefail

cd "$(dirname "$0")"

MEM_MAX="${MEM_MAX:-8G}"

exec systemd-run --user --scope --quiet --collect \
  --unit="intercept-pytest-$$" \
  -p MemoryMax="$MEM_MAX" \
  -p MemorySwapMax=0 \
  venv/bin/python -m pytest "$@"
