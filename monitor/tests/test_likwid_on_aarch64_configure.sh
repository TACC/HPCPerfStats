#!/bin/sh
# Regression (3.1, cpu-precision Phase 1 A3): aarch64 builds no LIKWID. `auto` on non-x86 is the
# `arm` backend (direct perf core + Grace SCF counters, no MONITOR_CPU_LIKWID_OVERLAY), the
# exclusive LIKWID backend is rejected on non-x86 with a clear configure error, and the static
# bundle / RPM prepare / cross scripts build LIKWID for x86 only. x86 keeps exclusive LIKWID.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ac="${ROOT}/configure.ac"
am="${ROOT}/src/Makefile.am"
bundle="${ROOT}/scripts/build_static_bundle.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"
cross="${ROOT}/scripts/cross_compile_test.sh"

fail() {
  echo "test_likwid_on_aarch64_configure: $*" >&2
  exit 1
}

# The overlay layer is gone (configure, Makefile.am, sources) and cannot come back silently.
if grep -n -E 'MONITOR_CPU_LIKWID_OVERLAY|cpu_likwid_overlay|CPU_LIKWID_OVERLAY' "${ac}" "${am}"; then
  fail "configure.ac / src/Makefile.am must not define the aarch64 LIKWID overlay"
fi
for f in cpu_counter_metrics_likwid_overlay.c cpu_counter_metrics_likwid_overlay.h \
  cpu_counter_metrics_likwid_overlay_map.c cpu_counter_metrics_likwid_overlay_map.h; do
  test ! -e "${ROOT}/src/${f}" || fail "src/${f} must be deleted (overlay removed in 3.1)"
done
if grep -n 'MONITOR_CPU_LIKWID_OVERLAY' "${ROOT}"/src/*.c "${ROOT}"/src/*.h; then
  fail "no source may test MONITOR_CPU_LIKWID_OVERLAY (O15: it must never revive synthetic FLOPs)"
fi

# Backend selection: auto|likwid|arm; auto resolves arm on non-x86; likwid only needs LIKWID.
grep -q 'auto|likwid|arm) ;;' "${ac}" || fail "configure.ac must accept auto|likwid|arm"
grep -q -- '--with-cpu-counter-backend={auto|likwid|arm}' "${ac}" || fail "help must list {auto|likwid|arm}"
grep -q 'cpu_counter_backend="arm"' "${ac}" || fail "auto must resolve to arm on non-x86"
grep -q 'CPU_COUNTER_BACKEND_CPPFLAG="-DMONITOR_CPU_BACKEND_LIKWID"' "${ac}" \
  || fail "x86 likwid backend CPPFLAG must stay"
grep -q 'is x86-only' "${ac}" || fail "configure.ac must reject --with-cpu-counter-backend=likwid on non-x86"
if grep -n -E 'auto\|likwid\|dcgm|xdcgm|MONITOR_CPU_BACKEND_DCGM|CPU_BACKEND_DCGM' "${ac}" "${am}"; then
  fail "dcgm CPU backend leftovers must stay gone"
fi
if grep -q -E 'PAPI_library_init|MONITOR_CPU_PAPI_FLOPS' "${ac}"; then
  fail "configure.ac must not probe PAPI"
fi

# New direct-perf sources are built on the arm backend.
grep -q 'arm_perf_util.c' "${am}" || fail "src/Makefile.am must build arm_perf_util.c"
grep -q 'arm_core_perf.c' "${am}" || fail "src/Makefile.am must build arm_core_perf.c"

# Scripts: LIKWID is built for x86 only.
grep -q 'build_likwid' "${bundle}" || fail "build_static_bundle.sh must keep build_likwid (x86)"
awk '/is_x86_build_host; then/ { gate = NR } /^ *build_likwid$/ { if (gate && NR - gate <= 2) ok = 1 }
  END { exit ok ? 0 : 1 }' "${bundle}" || fail "build_static_bundle.sh must call build_likwid only on x86"
grep -q 'host_builds_likwid' "${prepare}" || fail "prepare_rpmbuild_dirs.sh must require liblikwid.a on x86 only"
grep -q 'liblikwid.a' "${prepare}" || fail "prepare_rpmbuild_dirs.sh must still check liblikwid.a on x86"
awk 'index($0, "if is_x86_triplet \"${target}\"; then") { gate = NR }
  index($0, "build_foreign_likwid \"${target}\"") { if (gate && NR - gate <= 1) ok = 1 }
  END { exit ok ? 0 : 1 }' "${cross}" || fail "cross_compile_test.sh must build LIKWID for x86 targets only"
grep -q 'GCCARMv8' "${cross}" || fail "cross_compile_test.sh keeps the LIKWID COMPILER map"
if grep -q -E 'build_papi|STATIC_PIN_PAPI' "${bundle}" || grep -q 'libpapi.a' "${prepare}"; then
  fail "PAPI must stay gone from the bundle and prepare scripts"
fi

# Functional: a non-x86 host with --with-cpu-counter-backend=likwid stops in configure with the
# x86-only message (host gcc stands in for the cross compiler; it fails before any library probe).
if test -x "${ROOT}/configure" && command -v "${CC:-cc}" >/dev/null 2>&1; then
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/hps_aarch64_cfg.XXXXXX")"
  rc=0
  (cd "${tmp}" && "${ROOT}/configure" --host=aarch64-linux-gnu --build="$("${ROOT}/config.guess")" \
    CC="${CC:-cc}" --with-cpu-counter-backend=likwid >"${tmp}/out.log" 2>&1) || rc=$?
  if test "${rc}" -eq 0 || ! grep -q 'likwid is x86-only' "${tmp}/out.log"; then
    tail -20 "${tmp}/out.log" >&2
    rm -rf "${tmp}"
    fail "configure --host=aarch64 --with-cpu-counter-backend=likwid must fail with the x86-only error"
  fi
  rm -rf "${tmp}"
fi

echo "test_likwid_on_aarch64_configure passed"
