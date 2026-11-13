#!/usr/bin/env bash
# Regression (via check-local): scripts/canary_gemm_load.sh builds the GEMM load, runs it in
# the foreground for a bounded time, starts / refuses a second start / reports / stops a
# background run, and rejects bad options. Skips (77) when no C compiler with OpenMP exists.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/scripts/canary_gemm_load.sh"
TMPBASE="${TMPDIR:-/tmp}"
PREFIX="$(mktemp -d "${TMPBASE}/hps-gemm-load-test.XXXXXX")"
cleanup() {
  "${SCRIPT}" stop --prefix "${PREFIX}" >/dev/null 2>&1 || true
  rm -rf "${PREFIX}"
}
trap cleanup EXIT

fail() {
  echo "test_canary_gemm_load: $*" >&2
  exit 1
}

test -x "${SCRIPT}" || fail "missing or not executable: ${SCRIPT}"

cc_bin="${CC_FOR_GEMM_TEST:-cc}"
command -v "${cc_bin}" >/dev/null 2>&1 || {
  echo "SKIP: no C compiler '${cc_bin}'"
  exit 77
}
printf '#include <omp.h>\nint main(void){return omp_get_max_threads()>0?0:1;}\n' >"${PREFIX}/probe.c"
"${cc_bin}" -fopenmp -o "${PREFIX}/probe" "${PREFIX}/probe.c" >/dev/null 2>&1 || {
  echo "SKIP: '${cc_bin}' has no OpenMP"
  exit 77
}

# Bad input is rejected with exit 2 before anything is built.
for args in "start --precision xx" "run --threads 0" "run --seconds -1" "run --size 8" "bogus"; do
  set +e
  # shellcheck disable=SC2086
  "${SCRIPT}" ${args} --prefix "${PREFIX}" >/dev/null 2>&1
  rc=$?
  set -e
  [ "${rc}" -eq 2 ] || fail "'${args}' exit ${rc}, want 2"
done
[ ! -e "${PREFIX}/bin" ] || fail "bad options must not build anything"

# install builds dp and sp (hp is optional: needs _Float16).
"${SCRIPT}" install --prefix "${PREFIX}" --cc "${cc_bin}" >"${PREFIX}/install.out" 2>&1 ||
  fail "install failed: $(cat "${PREFIX}/install.out")"
for p in dp sp; do
  test -x "${PREFIX}/bin/gemm_load_${p}" || fail "install did not build gemm_load_${p}"
done
grep -q "GEMM_T" "${PREFIX}/src/gemm_load.c" || fail "source not written"

# Foreground run is bounded by --seconds and reports a positive rate and a finite check value.
out="$("${SCRIPT}" run --prefix "${PREFIX}" --precision sp --seconds 1 --size 64 --threads 2)"
echo "${out}" | grep -q "gemm_load sp n=64 threads=2 seconds=1 started" || fail "no start line: ${out}"
echo "${out}" | grep -Eq "GFLOP/s=[0-9]*\.[0-9]+ .*check=1\.000" || fail "no rate/check line: ${out}"

# Background lifecycle: start, a second start is refused, status, stop, status after stop.
"${SCRIPT}" start --prefix "${PREFIX}" --precision dp --size 64 --threads 1 >"${PREFIX}/start.out" ||
  fail "start failed: $(cat "${PREFIX}/start.out")"
grep -q "precision=dp threads=1 size=64 seconds=0" "${PREFIX}/gemm_load.state" || fail "state not recorded"
pid="$(cat "${PREFIX}/gemm_load.pid")"
kill -0 "${pid}" 2>/dev/null || fail "background pid ${pid} not running"
set +e
"${SCRIPT}" start --prefix "${PREFIX}" >/dev/null 2>&1
rc=$?
set -e
[ "${rc}" -eq 2 ] || fail "second start exit ${rc}, want 2 (already running)"
"${SCRIPT}" status --prefix "${PREFIX}" | grep -q "running pid ${pid}" || fail "status does not show pid"
"${SCRIPT}" stop --prefix "${PREFIX}" | grep -q "stopped pid ${pid}" || fail "stop did not report pid"
! kill -0 "${pid}" 2>/dev/null || fail "pid ${pid} still alive after stop"
[ ! -e "${PREFIX}/gemm_load.pid" ] || fail "pidfile left after stop"
set +e
"${SCRIPT}" status --prefix "${PREFIX}" >/dev/null 2>&1
rc=$?
set -e
[ "${rc}" -eq 1 ] || fail "status after stop exit ${rc}, want 1"
"${SCRIPT}" stop --prefix "${PREFIX}" | grep -q "not running" || fail "stop when idle must be a no-op"

echo "PASS test_canary_gemm_load"
