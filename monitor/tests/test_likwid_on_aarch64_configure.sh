#!/bin/sh
# Regression: non-x86 DCGM CPU backend uses LIKWID overlay (configure.ac + static bundle).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ac="${ROOT}/configure.ac"
bundle="${ROOT}/scripts/build_static_bundle.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"
cross="${ROOT}/scripts/cross_compile_test.sh"

grep -q 'MONITOR_CPU_LIKWID_OVERLAY' "${ac}" \
  || { echo "configure.ac must define MONITOR_CPU_LIKWID_OVERLAY for DCGM+LIKWID hybrid" >&2; exit 1; }
grep -q 'CPU_LIKWID_OVERLAY' "${ac}" \
  || { echo "configure.ac must AM_CONDITIONAL CPU_LIKWID_OVERLAY" >&2; exit 1; }
# Exclusive LIKWID is allowed on ARM (no longer a configure error).
if grep -q 'LIKWID CPU backend is supported on x86 only' "${ac}"; then
  echo "configure.ac must not reject LIKWID on non-x86" >&2
  exit 1
fi
# PAPI must be gone from Autotools.
if grep -q 'PAPI_library_init' "${ac}"; then
  echo "configure.ac must not probe PAPI_library_init" >&2
  exit 1
fi
if grep -q 'MONITOR_CPU_PAPI_FLOPS' "${ac}"; then
  echo "configure.ac must not define MONITOR_CPU_PAPI_FLOPS" >&2
  exit 1
fi

# Regression (c122-018 native bundle, 2026-11-03): DCGM builds compile likwid_uncore_adapter.c
# under `if LIKWID` (the Grace overlay links LIKWID), and it calls cpu_counter_metrics_likwid_ready().
# The DCGM stub lives in cpu_counter_metrics_likwid_begin.c, so the CPU_BACKEND_DCGM block must
# compile it; only --gc-sections release links hid the undefined reference.
am="${ROOT}/src/Makefile.am"
awk '
  /^if CPU_BACKEND_DCGM$/ { inblk = 1; depth = 0; next }
  inblk && /^if / { depth++ }
  inblk && /^endif/ { if (depth == 0) { inblk = 0 } else { depth-- } }
  inblk && /cpu_counter_metrics_likwid_begin\.c/ { found = 1 }
  END { exit found ? 0 : 1 }
' "${am}" \
  || { echo "src/Makefile.am CPU_BACKEND_DCGM must compile cpu_counter_metrics_likwid_begin.c (DCGM likwid_ready stub)" >&2; exit 1; }

grep -q 'build_likwid' "${bundle}" \
  || { echo "build_static_bundle.sh must define build_likwid" >&2; exit 1; }
if grep -q 'build_papi' "${bundle}"; then
  echo "build_static_bundle.sh must not define build_papi" >&2
  exit 1
fi
if grep -q 'STATIC_PIN_PAPI' "${bundle}"; then
  echo "build_static_bundle.sh must not pin PAPI" >&2
  exit 1
fi
if grep -q 'libpapi.a' "${prepare}"; then
  echo "prepare_rpmbuild_dirs.sh must not require libpapi.a" >&2
  exit 1
fi
grep -q 'liblikwid.a' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must require liblikwid.a on all arches" >&2; exit 1; }

if grep -q 'build_foreign_papi' "${cross}"; then
  echo "cross_compile_test.sh must not build PAPI" >&2
  exit 1
fi
grep -q 'build_foreign_likwid' "${cross}" \
  || { echo "cross_compile_test.sh must build LIKWID on foreign targets" >&2; exit 1; }
# LIKWID config.mk defaults to COMPILER=GCC (x86). Foreign/native non-x86 must
# pass GCCARMv8 / GCCPOWER or the archive compiles rdpmc / .intel_syntax.
grep -q 'GCCARMv8' "${cross}" \
  || { echo "cross_compile_test.sh must set LIKWID COMPILER=GCCARMv8 for aarch64" >&2; exit 1; }
grep -q 'likwid_compiler_for_target' "${cross}" \
  || { echo "cross_compile_test.sh must map target CPU to LIKWID COMPILER" >&2; exit 1; }
grep -q 'GCCARMv8' "${bundle}" \
  || { echo "build_static_bundle.sh must set LIKWID COMPILER=GCCARMv8 on aarch64" >&2; exit 1; }
grep -q 'likwid_compiler_for_host' "${bundle}" \
  || { echo "build_static_bundle.sh must map host CPU to LIKWID COMPILER" >&2; exit 1; }

grep -q 'cpu_counter_metrics_likwid_overlay' "${ROOT}/src/Makefile.am" \
  || { echo "src/Makefile.am must include LIKWID overlay sources under CPU_LIKWID_OVERLAY" >&2; exit 1; }
if grep -q 'cpu_counter_metrics_papi' "${ROOT}/src/Makefile.am"; then
  echo "src/Makefile.am must not include PAPI sources" >&2
  exit 1
fi

echo "test_likwid_on_aarch64_configure passed"
