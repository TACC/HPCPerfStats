#!/usr/bin/env bash
# Baseline CPU profiling examples for hpcperfstatsd (Tier A vs B validation).
# Requires: built binary (see scripts/build_static_bundle.sh), Linux perf, optional privileges.
#
# Usage (from repo):
#   PREFIX=/path/to/prefix ./scripts/build_static_bundle.sh   # or SKIP_DEPS=1 if deps exist
#   ./tests/run_tests.sh
#   ./scripts/profile_hpcperfstatsd_example.sh /path/to/hpcperfstatsd [seconds]
#
# This script does not start the daemon; it prints recommended perf commands.
set -euo pipefail

BIN="${1:-}"
DUR="${2:-30}"

if [[ -z "${BIN}" || ! -x "${BIN}" ]]; then
  echo "Usage: $0 /path/to/hpcperfstatsd [duration_seconds]" >&2
  echo "Example: $0 \"\${PWD}/.build-static/src/hpcperfstatsd\" 20" >&2
  exit 1
fi

echo "=== hpcperfstatsd profiling cookbook (duration=${DUR}s) ==="
echo
echo "# 1) Top symbols (needs debug symbols in the binary):"
echo "perf record -g -F 997 --call-graph dwarf -o \"\${PWD}/perf.data\" -- \"${BIN}\" -c /path/to/hpcperfstats.conf"
echo "# Let it run in another terminal, then Ctrl+C or kill after ${DUR}s, then:"
echo "perf report -i \"\${PWD}/perf.data\" --stdio | head -80"
echo
echo "# 2) Quick counters (no root if hardware events allowed):"
echo "perf stat -e cycles,instructions,cache-misses -- \"${BIN}\" -c /path/to/hpcperfstats.conf"
echo
echo "# 3) Flame graph (requires FlameGraph scripts on PATH):"
echo "perf record -g -F 997 -o \"\${PWD}/perf.data\" -- \"${BIN}\" ..."
echo "perf script -i \"\${PWD}/perf.data\" | stackcollapse-perf.pl | flamegraph.pl > fg.svg"
echo
echo "Compare before/after: RabbitMQ connect-per-message vs persistent connection should"
echo "dominate syscalls; payload append + collectors follow in samples."
