#!/bin/sh
# Regression: core+uncore LIKWID must use readGroupCounters (not readCounters).
# Bare perfmon_readCounters() follows activeGroup; IMC setup steals it and
# leaves host_cpu_hw stuck at zeros (spr-host-cpu-hw-active-group).
#
# Turin/SPR multi-group PERF: DF/IMC/RAPL setupCounters leave core flat unless
# collect re-arms the core group (likwid_pmc_adapter_prepare_collect) with
# setupCounters(g_group) + startCounters before readGroupCounters
# (turin-host-cpu-hw-zeros / host-cpu-hw-residual-zeros).
#
# SKX/ICX IMC zeros: uncore/RAPL collect must re-arm (finish_group / setup+start)
# before readGroupCounters after host_cpu_hw prepare_collect each tick.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pmc="${ROOT}/src/likwid_pmc_adapter.c"
uncore="${ROOT}/src/likwid_uncore_adapter.c"
rapl="${ROOT}/src/likwid_rapl_pwr.c"
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
# prepare_collect must start after setup (uncore pattern); require start near setup(g_group).
awk '
  /int likwid_pmc_adapter_prepare_collect/,/^int likwid_pmc_adapter_read_cpu/ {
    if ($0 ~ /perfmon_setupCounters[[:space:]]*\([[:space:]]*g_group/) saw_setup = 1
    if (saw_setup && $0 ~ /perfmon_startCounters[[:space:]]*\(/) saw_start = 1
  }
  END {
    if (!saw_setup) { print "FAIL: prepare_collect missing setupCounters(g_group)" > "/dev/stderr"; exit 1 }
    if (!saw_start) { print "FAIL: prepare_collect must call startCounters after setupCounters(g_group)" > "/dev/stderr"; exit 1 }
  }
' "${pmc}"
grep -qE 'likwid_pmc_adapter_prepare_collect[[:space:]]*\(' "${collect}" \
  || { echo "FAIL: ${collect} must call likwid_pmc_adapter_prepare_collect once per LIKWID tick" >&2; exit 1; }

# Uncore collect must re-arm its group before read (SKX/ICX IMC zeros after core prepare).
awk '
  /void likwid_uncore_adapter_collect/,/^}/ {
    if ($0 ~ /likwid_uncore_finish_group/) saw_rearm = 1
    if (saw_rearm && $0 ~ /perfmon_readGroupCounters/) saw_read = 1
  }
  END {
    if (!saw_rearm) {
      print "FAIL: uncore collect must call likwid_uncore_finish_group before readGroupCounters" > "/dev/stderr"
      exit 1
    }
    if (!saw_read) {
      print "FAIL: uncore collect must call readGroupCounters after re-arm" > "/dev/stderr"
      exit 1
    }
  }
' "${uncore}"

# RAPL PWR collect must re-arm before readGroupCounters (Intel PWR after core steal).
awk '
  /int likwid_rapl_pwr_collect_socket_mj/,/^}/ {
    if ($0 ~ /perfmon_setupCounters[[:space:]]*\([[:space:]]*g_pwr_group/) saw_setup = 1
    if (saw_setup && $0 ~ /perfmon_startCounters[[:space:]]*\(/) saw_start = 1
    if (saw_start && $0 ~ /perfmon_readGroupCounters[[:space:]]*\([[:space:]]*g_pwr_group/) saw_read = 1
  }
  END {
    if (!saw_setup) {
      print "FAIL: rapl collect missing perfmon_setupCounters(g_pwr_group) before read" > "/dev/stderr"
      exit 1
    }
    if (!saw_start) {
      print "FAIL: rapl collect must call startCounters before readGroupCounters" > "/dev/stderr"
      exit 1
    }
    if (!saw_read) {
      print "FAIL: rapl collect must call readGroupCounters(g_pwr_group) after re-arm" > "/dev/stderr"
      exit 1
    }
  }
' "${rapl}"

echo "test_likwid_read_group_counters passed"
