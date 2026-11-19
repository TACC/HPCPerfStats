#!/bin/sh
# Regression: both CPU backends read hardware counters by direct perf with no PMU library.
# `auto` is the x86 backend on x86 and the arm backend elsewhere, `x86` is rejected on non-x86
# with a clear configure error, the x86 backend builds the direct-perf readers, and no build
# script, prepare script or configure probe builds or links a third-party PMU library.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ac="${ROOT}/configure.ac"
am="${ROOT}/src/Makefile.am"
bundle="${ROOT}/scripts/build_static_bundle.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"
cross="${ROOT}/scripts/cross_compile_test.sh"

fail() {
  echo "test_cpu_backend_configure: $*" >&2
  exit 1
}

# Backend selection: auto|x86|arm; auto resolves per host_cpu.
grep -q 'auto|x86|arm) ;;' "${ac}" || fail "configure.ac must accept auto|x86|arm"
grep -q -- '--with-cpu-counter-backend={auto|x86|arm}' "${ac}" || fail "help must list {auto|x86|arm}"
grep -q 'x86_64|i?86) cpu_counter_backend="x86" ;;' "${ac}" || fail "auto must resolve to x86 on x86"
grep -q '\*) cpu_counter_backend="arm" ;;' "${ac}" || fail "auto must resolve to arm on non-x86"
grep -q 'CPU_COUNTER_BACKEND_CPPFLAG="-DMONITOR_CPU_BACKEND_X86"' "${ac}" \
  || fail "x86 backend CPPFLAG must be -DMONITOR_CPU_BACKEND_X86"
grep -q 'CPU_COUNTER_BACKEND_CPPFLAG="-DMONITOR_CPU_BACKEND_ARM"' "${ac}" \
  || fail "arm backend CPPFLAG must be -DMONITOR_CPU_BACKEND_ARM"
grep -q 'cpu-counter-backend=x86 is x86-only' "${ac}" \
  || fail "configure.ac must reject --with-cpu-counter-backend=x86 on non-x86"
if grep -n -E 'MONITOR_CPU_BACKEND_DCGM|CPU_BACKEND_DCGM|PAPI_library_init|perfmon_init' "${ac}" "${am}"; then
  fail "configure.ac / src/Makefile.am must not probe or define a PMU library backend"
fi
if grep -n -E 'with-monitor-arch|MONITOR_ARCH_(INTEL|AMD)' "${ac}" "${am}"; then
  fail "the compile-time vendor selection is gone (the vendor is the runtime processor_t)"
fi

# The x86 backend builds the direct-perf readers.
for f in perf_util.c x86_core_perf.c x86_uncore_perf.c x86_rapl.c rapl_powercap.c \
  cpu_counter_metrics_x86_publish.c; do
  awk -v f="${f}" '/^if CPU_BACKEND_X86$/ { on = 1 } on && $1 == f { ok = 1 } on && /^endif$/ { exit }
    END { exit ok ? 0 : 1 }' "${am}" || fail "src/Makefile.am must build ${f} under CPU_BACKEND_X86"
done

# Scripts: libev, rabbitmq-c and (eBPF profiler only) libbpf are the only static dependencies.
if grep -n -E 'build_papi|STATIC_PIN_PAPI|libpapi' "${bundle}" "${prepare}" "${cross}"; then
  fail "PAPI must stay gone from the bundle, prepare and cross scripts"
fi
pins="$(grep -o -E '^STATIC_PIN_[A-Z0-9_]+_VERSION=' "${bundle}" | sort | tr '\n' ' ')"
test "${pins}" = "STATIC_PIN_LIBBPF_VERSION= STATIC_PIN_LIBEV_VERSION= STATIC_PIN_RABBITMQ_C_VERSION= " \
  || fail "build_static_bundle.sh must pin only libbpf, libev and rabbitmq-c (got: ${pins})"

# Functional: a non-x86 host with --with-cpu-counter-backend=x86 stops in configure with the
# x86-only message (host gcc stands in for the cross compiler; it fails before any library probe).
if test -x "${ROOT}/configure" && command -v "${CC:-cc}" >/dev/null 2>&1; then
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/hps_cpu_backend_cfg.XXXXXX")"
  rc=0
  (cd "${tmp}" && "${ROOT}/configure" --host=aarch64-linux-gnu --build="$("${ROOT}/config.guess")" \
    CC="${CC:-cc}" --with-cpu-counter-backend=x86 >"${tmp}/out.log" 2>&1) || rc=$?
  if test "${rc}" -eq 0 || ! grep -q 'x86 is x86-only' "${tmp}/out.log"; then
    tail -20 "${tmp}/out.log" >&2
    rm -rf "${tmp}"
    fail "configure --host=aarch64 --with-cpu-counter-backend=x86 must fail with the x86-only error"
  fi
  rm -rf "${tmp}"
fi

echo "test_cpu_backend_configure passed"
