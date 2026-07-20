#!/bin/sh
# Regression: non-x86 DCGM CPU backend requires PAPI hybrid (configure.ac + static bundle).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ac="${ROOT}/configure.ac"
bundle="${ROOT}/scripts/build_static_bundle.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"

grep -q 'MONITOR_CPU_PAPI_FLOPS' "${ac}" \
  || { echo "configure.ac must define MONITOR_CPU_PAPI_FLOPS for DCGM+PAPI hybrid" >&2; exit 1; }
grep -q 'CPU_PAPI_FLOPS' "${ac}" \
  || { echo "configure.ac must AM_CONDITIONAL CPU_PAPI_FLOPS" >&2; exit 1; }
grep -q 'PAPI_library_init' "${ac}" \
  || { echo "configure.ac must AC_SEARCH_LIBS PAPI_library_init" >&2; exit 1; }

grep -q 'build_papi' "${bundle}" \
  || { echo "build_static_bundle.sh must define build_papi" >&2; exit 1; }
grep -q 'STATIC_PIN_PAPI_VERSION' "${bundle}" \
  || { echo "build_static_bundle.sh must pin STATIC_PIN_PAPI_VERSION" >&2; exit 1; }
grep -q 'STATIC_PIN_PAPI_VERSION="7.2.0"' "${bundle}" \
  || { echo "build_static_bundle.sh must pin PAPI 7.2.0" >&2; exit 1; }
grep -q 'libpapi.a' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must guard libpapi.a on non-x86" >&2; exit 1; }

grep -q 'cpu_counter_metrics_papi' "${ROOT}/src/Makefile.am" \
  || { echo "src/Makefile.am must include PAPI sources under CPU_PAPI_FLOPS" >&2; exit 1; }

echo "test_papi_hybrid_configure passed"
