#!/usr/bin/env bash
# Regression (via check-local): scripts/canary_gemm_load.sh builds the GEMM load, runs it in
# the foreground for a bounded time (ending with its exact FLOP total), starts / refuses a
# second start / reports / stops a background run, rejects bad options, passes --cflags to the
# compiler, and runs the read and write kernels to their exact byte totals. Skips (77) when no C compiler with
# OpenMP exists.
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
for args in "start --precision xx" "run --threads 0" "run --seconds -1" "run --size 8" \
  "run --kernel xx" "run --kernel read --mib 8" "run --kernel write --mib 8" \
  "run --kernel write --mib 65537" "run --kernel write --threads 0" "run --kernel write --seconds x" \
  "run --kernel write --mib" "start --kernel Write" "bogus"; do
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
test -x "${PREFIX}/bin/read_load" || fail "install did not build read_load"
test -x "${PREFIX}/bin/write_load" || fail "install did not build write_load"
grep -q "GEMM_T" "${PREFIX}/src/gemm_load.c" || fail "source not written"

# Foreground run is bounded by --seconds and reports a positive rate and a finite check value.
out="$("${SCRIPT}" run --prefix "${PREFIX}" --precision sp --seconds 1 --size 64 --threads 2)"
grep -q "gemm_load sp n=64 threads=2 seconds=1 started" <<<"${out}" || fail "no start line: ${out}"
echo "${out}" | grep -Eq "GFLOP/s=[0-9]*\.[0-9]+ .*check=1\.000" || fail "no rate/check line: ${out}"
# A bounded run ends with its exact FLOP total (2 n^3 per GEMM; perf_raw_flops_check.sh needs it).
total="$(echo "${out}" | sed -n 's/^gemm_load sp done total_flops=\([0-9]*\) elapsed=.*/\1/p')"
[ -n "${total}" ] || fail "no done total_flops line: ${out}"
if [ "${total}" -le 0 ] || [ $((total % (2 * 64 * 64 * 64))) -ne 0 ]; then
  fail "total_flops ${total} is not a positive multiple of 2*64^3"
fi

# Background lifecycle: start, a second start is refused, status, stop, status after stop.
"${SCRIPT}" start --prefix "${PREFIX}" --precision dp --size 64 --threads 1 >"${PREFIX}/start.out" ||
  fail "start failed: $(cat "${PREFIX}/start.out")"
grep -q "kernel=gemm precision=dp threads=1 size=64 mib=4096 seconds=0" "${PREFIX}/gemm_load.state" ||
  fail "state not recorded: $(cat "${PREFIX}/gemm_load.state")"
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

# The read kernel reads the whole --mib array every pass and reports exactly passes x array bytes;
# check= is the per-pass sum (64 MiB of doubles that are all 1.0).
out="$("${SCRIPT}" run --prefix "${PREFIX}" --kernel read --mib 64 --seconds 1 --threads 2)"
grep -q "read_load mib=64 threads=2 seconds=1 started" <<<"${out}" || fail "no read start: ${out}"
echo "${out}" | grep -Eq "GB/s=[0-9]*\.[0-9]+ .*check=8388608$" || fail "no read rate/check: ${out}"
bytes="$(echo "${out}" | sed -n 's/^read_load done total_bytes_read=\([0-9]*\) passes=\([0-9]*\) .*/\1 \2/p')"
[ -n "${bytes}" ] || fail "no read done line: ${out}"
read -r total passes <<<"${bytes}"
if [ "${passes}" -lt 1 ] || [ "${total}" -ne $((passes * 64 * 1024 * 1024)) ]; then
  fail "total_bytes_read ${total} is not passes (${passes}) x 64 MiB"
fi
"${SCRIPT}" start --prefix "${PREFIX}" --kernel read --mib 64 --threads 1 >"${PREFIX}/rstart.out" ||
  fail "read start failed: $(cat "${PREFIX}/rstart.out")"
grep -q "kernel=read .* mib=64 seconds=0" "${PREFIX}/gemm_load.state" || fail "read state not recorded"
"${SCRIPT}" stop --prefix "${PREFIX}" | grep -q "stopped pid" || fail "read stop did not report pid"

# The write kernel overwrites every line of the --mib array each pass and reports exactly
# passes x array bytes. Pass p stores the value p, and check= sums one double per 4 KiB of the
# array (16384 for 64 MiB), so the final check equals passes x 16384 only if every pass stored.
out="$("${SCRIPT}" run --prefix "${PREFIX}" --kernel write --mib 64 --seconds 1 --threads 2)"
grep -q "write_load mib=64 threads=2 seconds=1 started" <<<"${out}" || fail "no write start: ${out}"
echo "${out}" | grep -Eq "^write_load GB/s=[0-9]*\.[0-9]+ elapsed=[0-9]+s check=[0-9]+$" ||
  fail "no write rate/check: ${out}"
bytes="$(echo "${out}" | sed -n 's/^write_load done total_bytes_written=\([0-9]*\) passes=\([0-9]*\) elapsed=[0-9.]*s$/\1 \2/p')"
[ -n "${bytes}" ] || fail "no write done line: ${out}"
read -r total passes <<<"${bytes}"
if [ "${passes}" -lt 1 ] || [ "${total}" -ne $((passes * 64 * 1024 * 1024)) ]; then
  fail "total_bytes_written ${total} is not passes (${passes}) x 64 MiB"
fi
check="$(echo "${out}" | sed -n 's/^write_load GB\/s=.* check=\([0-9]*\)$/\1/p' | tail -n 1)"
[ "${check}" -eq $((passes * 16384)) ] || fail "final check ${check} != passes (${passes}) x 16384"
echo "${out}" | tail -n 1 | grep -q "^write_load done " || fail "done line is not last: ${out}"
"${SCRIPT}" start --prefix "${PREFIX}" --kernel write --mib 64 --threads 1 >"${PREFIX}/wstart.out" ||
  fail "write start failed: $(cat "${PREFIX}/wstart.out")"
grep -q "kernel=write .* mib=64 seconds=0" "${PREFIX}/gemm_load.state" || fail "write state not recorded"
"${SCRIPT}" stop --prefix "${PREFIX}" | grep -q "stopped pid" || fail "write stop did not report pid"
# The usage header documents the write kernel and its done line.
"${SCRIPT}" --help | grep -q -- "--kernel write" || fail "usage lacks --kernel write"
"${SCRIPT}" --help | grep -q "total_bytes_written=" || fail "usage lacks the write done line"

# An unknown --cflags flag fails the build and shows in its log, so the flags reach the compiler.
CF="${PREFIX}/cflags"
set +e
"${SCRIPT}" install --prefix "${CF}" --cc "${cc_bin}" --cflags "-fhps-no-such-flag" \
  >"${PREFIX}/cflags.out" 2>&1
rc=$?
set -e
[ "${rc}" -ne 0 ] || fail "install with a bad --cflags flag must fail"
grep -q -- "-fhps-no-such-flag" "${CF}/build_dp.log" || fail "flag not passed: $(cat "${CF}/build_dp.log")"
"${SCRIPT}" install --prefix "${CF}" --cc "${cc_bin}" --cflags "-DHPS_CFLAGS_OK=1" \
  >"${PREFIX}/cflags2.out" 2>&1 || fail "install with a valid --cflags failed: $(cat "${PREFIX}/cflags2.out")"
test -x "${CF}/bin/gemm_load_dp" || fail "--cflags install did not build gemm_load_dp"

echo "PASS test_canary_gemm_load"
