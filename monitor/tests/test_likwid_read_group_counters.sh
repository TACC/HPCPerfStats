#!/bin/sh
# Regression: core+uncore LIKWID must use readGroupCounters (not readCounters).
# Bare perfmon_readCounters() follows activeGroup; IMC setup steals it and
# leaves host_cpu_hw stuck at zeros (spr-host-cpu-hw-active-group).
#
# Turin/amd-rtx: DF/RAPL setupCounters also leave core PERF flat unless collect
# re-programs the core group (likwid_pmc_adapter_prepare_collect) before
# readGroupCounters (turin-host-cpu-hw-zeros).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pmc="${ROOT}/src/likwid_pmc_adapter.c"
uncore="${ROOT}/src/likwid_uncore_adapter.c"
collect="${ROOT}/src/cpu_counter_metrics.c"

for f in "${pmc}" "${uncore}"; do
  if grep -nE 'perfmon_readCounters[[:space:]]*\(' "${f}"; then
    echo "FAIL: ${f} must not call perfmon_readCounters(); use perfmon_readGroupCounters(groupId)" >&2
    exit 1
  fi
  grep -qE 'perfmon_readGroupCounters[[:space:]]*\(' "${f}" \
    || { echo "FAIL: ${f} must call perfmon_readGroupCounters" >&2; exit 1; }
done

grep -qE 'likwid_pmc_adapter_prepare_collect[[:space:]]*\(' "${pmc}" \
  || { echo "FAIL: ${pmc} must define likwid_pmc_adapter_prepare_collect" >&2; exit 1; }
grep -qE 'perfmon_setupCounters[[:space:]]*\([[:space:]]*g_group' "${pmc}" \
  || { echo "FAIL: ${pmc} prepare_collect must call perfmon_setupCounters(g_group)" >&2; exit 1; }
grep -qE 'likwid_pmc_adapter_prepare_collect[[:space:]]*\(' "${collect}" \
  || { echo "FAIL: ${collect} must call likwid_pmc_adapter_prepare_collect once per LIKWID tick" >&2; exit 1; }

echo "test_likwid_read_group_counters passed"
