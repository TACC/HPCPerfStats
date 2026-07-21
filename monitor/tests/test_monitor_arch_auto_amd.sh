#!/bin/sh
# Regression: --with-monitor-arch=auto must detect AuthenticAMD (not force intel on all x86).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ac="${ROOT}/configure.ac"

grep -q 'AuthenticAMD' "${ac}" \
  || { echo "configure.ac auto monitor-arch must probe AuthenticAMD" >&2; exit 1; }
grep -q 'vendor_id' "${ac}" \
  || { echo "configure.ac auto monitor-arch must read vendor_id from /proc/cpuinfo" >&2; exit 1; }
grep -q 'likwid_rapl_collect_path' "${ac}" \
  || { echo "configure.ac must note RAPL uses runtime likwid_rapl_collect_path" >&2; exit 1; }

# Source contract: RAPL must not gate on MONITOR_ARCH_* compile flags.
rapl="${ROOT}/src/likwid_rapl.c"
if grep -nE '#if[[:space:]]+defined[[:space:]]*\([[:space:]]*MONITOR_ARCH_' "${rapl}"; then
  echo "FAIL: likwid_rapl.c must not use MONITOR_ARCH_* for collect path" >&2
  exit 1
fi
grep -q 'likwid_rapl_collect_path' "${rapl}" \
  || { echo "likwid_rapl.c must call likwid_rapl_collect_path" >&2; exit 1; }

echo "test_monitor_arch_auto_amd passed"
