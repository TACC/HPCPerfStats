#!/bin/sh
# Regression: legacy Intel MSR PMC readers must be gone (LIKWID-only host_cpu_hw).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

for f in intel_4pmc3.c intel_8pmc3.c intel_pmc3.h intel_pmc3_core.c intel_pmc3_core.h \
         msr_io.c msr_io.h intel_uncore_msr_box.c intel_uncore_msr_box.h \
         intel_uncore_pci.c intel_uncore_pci.h intel_uncore_mmio.c intel_uncore_mmio.h \
         intel_pmc_uncore.h intel_topology_walk.h pci.c pci.h \
         intel_mmconfig.c intel_mmconfig.h; do
  if test -f "${ROOT}/src/${f}"; then
    echo "FAIL: legacy ${f} must be deleted" >&2
    exit 1
  fi
done

for f in test_msr_io.c test_intel_mmconfig.c; do
  if test -f "${ROOT}/tests/${f}"; then
    echo "FAIL: legacy ${f} must be deleted" >&2
    exit 1
  fi
done

reg="${ROOT}/src/stats_registry.c"
if grep -nE 'intel_4pmc3_stats_type|intel_8pmc3_stats_type' "${reg}"; then
  echo "FAIL: stats_registry must not reference intel_4/8pmc3 types" >&2
  exit 1
fi
if grep -nE 'st_name = "intel_x86_pmc_gpr4"|st_name = "intel_x86_pmc_gpr8"' "${ROOT}/src/"*.c 2>/dev/null; then
  echo "FAIL: intel_x86_pmc_gpr* st_name still defined" >&2
  exit 1
fi
if grep -nE 'fallback_fill|read_msr_u64|g_msr_fd_cache' "${ROOT}/src/cpu_counter_metrics.c"; then
  echo "FAIL: cpu_counter_metrics must not contain MSR fallback_fill" >&2
  exit 1
fi
if grep -n 'msr_io.h' "${ROOT}/src/likwid_pmc_adapter.c"; then
  echo "FAIL: likwid_pmc_adapter must not include msr_io.h" >&2
  exit 1
fi

echo "test_intel_legacy_pmc_removed passed"
