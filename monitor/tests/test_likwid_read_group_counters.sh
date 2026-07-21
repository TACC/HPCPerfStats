#!/bin/sh
# Regression: core+uncore LIKWID must use readGroupCounters (not readCounters).
# Bare perfmon_readCounters() follows activeGroup; IMC setup steals it and
# leaves host_cpu_hw stuck at zeros (spr-host-cpu-hw-active-group).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pmc="${ROOT}/src/likwid_pmc_adapter.c"
uncore="${ROOT}/src/likwid_uncore_adapter.c"

for f in "${pmc}" "${uncore}"; do
  if grep -nE 'perfmon_readCounters[[:space:]]*\(' "${f}"; then
    echo "FAIL: ${f} must not call perfmon_readCounters(); use perfmon_readGroupCounters(groupId)" >&2
    exit 1
  fi
  grep -qE 'perfmon_readGroupCounters[[:space:]]*\(' "${f}" \
    || { echo "FAIL: ${f} must call perfmon_readGroupCounters" >&2; exit 1; }
done

echo "test_likwid_read_group_counters passed"
