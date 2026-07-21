#!/bin/sh
# Regression: legacy AMD MSR PMC/DF collectors must be gone (LIKWID-only).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for f in amd64_pmc.c amd64_df.c amd64_event_tables.c amd64_pmu_core.c amd64_pmc.h amd64_df.h; do
  if test -f "${ROOT}/src/${f}"; then
    echo "FAIL: legacy ${f} must be deleted" >&2
    exit 1
  fi
done

reg="${ROOT}/src/stats_registry.c"
if grep -nE 'amd64_pmc_stats_type|amd64_df_stats_type' "${reg}"; then
  echo "FAIL: stats_registry must not reference legacy amd64_pmc/df types" >&2
  exit 1
fi
if grep -nE 'st_name = "amd_x86_pmc"|st_name = "amd_x86_uncore_df"' "${ROOT}/src/"*.c 2>/dev/null; then
  echo "FAIL: legacy amd_x86_pmc / amd_x86_uncore_df st_name still defined" >&2
  exit 1
fi

for name in amd_x86_uncore_df_rome amd_x86_uncore_df_milan amd_x86_uncore_df_genoa \
            amd_x86_uncore_df_turin; do
  grep -q "${name}" "${reg}" || {
    echo "FAIL: stats_registry missing ${name}" >&2
    exit 1
  }
done

echo "test_amd_legacy_removed passed"
