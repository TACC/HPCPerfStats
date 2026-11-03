#!/bin/sh
# Regression: with --enable-metric-profiler, src/stats_runtime.c calls the four cycle/collect
# hooks and src/stats.c calls metric_profiler_record_metric for real. A test that links either
# source must link src/metric_profiler.c or stub every hook it needs, or make check fails to
# link (test_stats_runtime_likwid_begin_order lacked the cycle_* stubs; test_stats_set_tier_gate
# lacked record_metric).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}/tests"
MAKEFILE_AM="${MAKEFILE_AM:-Makefile.am}"

runtime_hooks="metric_profiler_cycle_begin metric_profiler_cycle_end metric_profiler_collect_begin metric_profiler_collect_end"
stats_hooks="metric_profiler_record_metric"
fail=0
checked=0
# Rows "<program>:<links stats_runtime.c 0|1>:<links stats.c 0|1>" for programs that link a
# hook-calling source without src/metric_profiler.c.
for row in $(awk '
  /_SOURCES[ \t]*\+?=/ { name = $1; sub(/_SOURCES$/, "", name) }
  name != "" && /src\/stats_runtime\.c/ { rt[name] = 1; any[name] = 1 }
  name != "" && /src\/stats\.c([ \t\\]|$)/ { st[name] = 1; any[name] = 1 }
  name != "" && /src\/metric_profiler\.c/ { mp[name] = 1 }
  !/\\[ \t]*$/ { name = "" }
  END { for (n in any) if (!(n in mp)) printf "%s:%d:%d\n", n, (n in rt), (n in st) }
' "${MAKEFILE_AM}"); do
  prog="${row%%:*}"
  rest="${row#*:}"
  need=""
  test "${rest%%:*}" = "1" && need="${need} ${runtime_hooks}"
  test "${rest#*:}" = "1" && need="${need} ${stats_hooks}"
  checked=$((checked + 1))
  for h in ${need}; do
    if ! grep -q "^void ${h}(" "${prog}.c"; then
      echo "${prog}.c links a profiler-calling source but neither links src/metric_profiler.c nor stubs ${h}" >&2
      fail=1
    fi
  done
done
test "${checked}" -ge 4 || { echo "expected >=4 profiler-calling test drivers, found ${checked}" >&2; exit 1; }
test "${fail}" -eq 0
echo "test_metric_profiler_link_stubs passed (${checked} drivers)"
