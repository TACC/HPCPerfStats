#!/usr/bin/env bash
# bin/gpu_tensor_calibrate.sh without a GPU: bad options exit 2; build mode resolves the toolchain
# through a stub Lmod (direct cuda/13.1 load, static cuBLAS in math_libs/13.1) and calls a stub
# nvcc with the Blackwell gencodes, the cuBLAS include dir and the static link; build reports
# NOT_RUN (exit 3) when no cuda module is new enough; run mode reports NOT_RUN without a build or
# without the DEBUG shm mirror, and end to end (the stub nvcc's binary plays the calibrator and a
# stub daemon writes nvidia_gpu rows whose tensor activity follows it) prints the g_peaks row.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/../bin/gpu_tensor_calibrate.sh"
WS="$(cd "${HERE}/../../../.." && pwd)"
PY=python3
[ -x "${WS}/.venv/bin/python3" ] && PY="${WS}/.venv/bin/python3"
T="$(mktemp -d "${TMPDIR:-/tmp}/hps-gpu-calibrate-test.XXXXXX")"
WRITER=""
cleanup() {
  [ -n "${WRITER}" ] && kill "${WRITER}" 2>/dev/null
  rm -rf "${T}"
}
trap cleanup EXIT

fail() {
  echo "test_gpu_tensor_calibrate: $*" >&2
  exit 1
}
test -x "${SCRIPT}" || fail "missing or not executable: ${SCRIPT}"

expect_rc() { # want-rc description command...
  local want=$1 what=$2 rc
  shift 2
  set +e
  "$@" > "${T}/out.txt" 2>&1
  rc=$?
  set -e
  [ "${rc}" -eq "${want}" ] || fail "${what}: exit ${rc}, want ${want}: $(cat "${T}/out.txt")"
}

for args in "bogus" "run --device x" "run --seconds 0" "build --prefix" "run --kind"; do
  # shellcheck disable=SC2086
  expect_rc 2 "'${args}'" "${SCRIPT}" ${args}
done

# Stampede3-like NVHPC tree with static cuBLAS, and a stub Lmod that loads it directly.
ROOT="${T}/nvhpc/Linux_x86_64/26.1"
mkdir -p "${ROOT}/cuda/13.1/bin" "${ROOT}/cuda/13.1/lib64" "${ROOT}/math_libs/13.1/lib64" \
  "${ROOT}/math_libs/13.1/include" "${T}/lmod/init"
for f in cuda/13.1/lib64/libcudart_static.a cuda/13.1/lib64/libculibos.a math_libs/13.1/include/cublas_v2.h \
  math_libs/13.1/lib64/libcublas.so math_libs/13.1/lib64/libcublas_static.a \
  math_libs/13.1/lib64/libcublasLt_static.a; do
  : > "${ROOT}/${f}"
done
cat > "${T}/lmod/init/bash" <<EOF
module() {
  case "\$*" in
    "-t spider cuda") printf '%b' "\${FAKE_SPIDER:-cuda/12.6\ncuda/13.1\n}" >&2 ;;
    "load cuda/13.1")
      export TACC_CUDA_DIR="${ROOT}/cuda/13.1" TACC_CUDA_VER=13.1 TACC_CUDA_LIB="${ROOT}/cuda/13.1/lib64" ;;
    *) return 1 ;;
  esac
}
EOF
# Stub nvcc: logs its arguments and writes the -o target as a fake calibrator. The fake prints the
# real result-line format with an exact FLOP total sized so that, at the stub daemon's activity
# (hmma 90 %, dmma 80 %), 148 SMs and 1800 MHz, the rate is 8192 (hmma) or 256 (dmma) FLOP per SM
# per clock; it marks its timed window with a flag file the stub daemon reads.
cat > "${ROOT}/cuda/13.1/bin/nvcc" <<'EOF'
#!/bin/bash
echo "$*" > "${FAKE_NVCC_LOG}"
out=""
while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done
cat > "${out}" <<'BIN'
#!/bin/bash
while [ $# -gt 0 ]; do
  case "$1" in --kind) kind=$2 ;; --n) n=$2 ;; --seconds) secs=$2 ;; --device) dev=$2 ;; esac
  shift 2
done
echo "gpu_tensor_calibrate warmup_calls=6 warmup_ms=1.0 batch=1 cuda_runtime=13010 cublas=130100"
touch "${FAKE_FLAGS}/active_${kind}"
t0=$(date +%s.%N)
sleep "${secs}"
t1=$(date +%s.%N)
rm -f "${FAKE_FLAGS}/active_${kind}"
awk -v k="${kind}" -v n="${n}" -v d="${dev}" -v t0="${t0}" -v t1="${t1}" 'BEGIN {
  per = (k == "hmma") ? 8192 * 0.90 : 256 * 0.80
  call = 2 * n * n * n
  s = t1 - t0
  calls = int(per * 148 * 1800e6 * s / call + 0.5)
  printf "gpu_tensor_calibrate kind=%s n=%d device=%s pci=0000:01:00.0 name=NVIDIA_GB200 cc=10.0 sms=148 calls=%d flops=%.0f elapsed_s=%.6f flops_per_s=%.6e start_epoch=%.6f end_epoch=%.6f\n", k, n, d, calls, calls * call, s, calls * call / s, t0, t1
}'
BIN
chmod +x "${out}"
EOF
chmod +x "${ROOT}/cuda/13.1/bin/nvcc"

run_env() {
  env -i PATH=/usr/bin:/bin HOME="${T}" MODULESHOME="${T}/lmod" FAKE_NVCC_LOG="${T}/nvcc.log" \
    FAKE_FLAGS="${T}" PYTHON="${PY}" "$@"
}

expect_rc 3 "build without a new enough cuda" run_env FAKE_SPIDER='cuda/12.4\n' \
  "${SCRIPT}" build --prefix "${T}/prefix"
grep -q "NOT_RUN build: no cuda module >= 12.8" "${T}/out.txt" || fail "build NOT_RUN reason: $(cat "${T}/out.txt")"

expect_rc 0 "build" run_env "${SCRIPT}" build --prefix "${T}/prefix"
grep -q "BUILD_OK ${T}/prefix/gpu_tensor_calibrate link=static" "${T}/out.txt" || fail "no BUILD_OK: $(cat "${T}/out.txt")"
for want in "-gencode arch=compute_100,code=sm_100" "-gencode arch=compute_120,code=" \
  "-I${ROOT}/math_libs/13.1/include" "-lcublas_static -lcublasLt_static -lculibos" \
  "bin/gpu_tensor_calibrate.cu" "-o ${T}/prefix/gpu_tensor_calibrate"; do
  grep -qF -- "${want}" "${T}/nvcc.log" || fail "nvcc args lack '${want}': $(cat "${T}/nvcc.log")"
done
grep -qF "as root, with the DEBUG monitor running: $(cd "${HERE}/../bin" && pwd)/gpu_tensor_calibrate.sh run --prefix ${T}/prefix" \
  "${T}/out.txt" || fail "no root run command: $(cat "${T}/out.txt")"
grep -q "^modules cuda/13.1$" "${T}/prefix/build-info.txt" || fail "build-info: $(cat "${T}/prefix/build-info.txt")"
test -x "${T}/prefix/gpu_tensor_calibrate" || fail "no binary"

SHM="${T}/shm"
expect_rc 3 "run without the shm mirror" run_env "${SCRIPT}" run --prefix "${T}/prefix" --shm "${SHM}"
grep -q "NOT_RUN run: no ${SHM}/fast or schema" "${T}/out.txt" || fail "shm NOT_RUN reason: $(cat "${T}/out.txt")"
expect_rc 3 "run without a build" run_env "${SCRIPT}" run --prefix "${T}/nobuild" --shm "${SHM}"
grep -q "NOT_RUN run: not built" "${T}/out.txt" || fail "build NOT_RUN reason: $(cat "${T}/out.txt")"

# Stub DEBUG daemon: a fast payload every 0.5 s (written atomically, like the daemon), tensor
# activity set while the fake calibrator's flag exists.
mkdir -p "${SHM}"
# shellcheck disable=SC2016  # literal $ schema lines
printf '$hpcperfstats 3.1\n$hostname testhost\n!nvidia_gpu gpu_util gpu_sm_clock tensor_hmma_active tensor_dmma_active\n' \
  > "${SHM}/schema"
(
  while :; do
    h=0 d=0
    [ -e "${T}/active_hmma" ] && h=90
    [ -e "${T}/active_dmma" ] && d=80
    printf '%s - testhost\nnvidia_gpu 0 99 1800 %s %s\n' "$(date +%s.%N)" "${h}" "${d}" > "${SHM}/fast.tmp"
    mv "${SHM}/fast.tmp" "${SHM}/fast"
    sleep 0.5
  done
) &
WRITER=$!
sleep 1

expect_rc 3 "run on a device with no row" run_env "${SCRIPT}" run --prefix "${T}/prefix" --shm "${SHM}" --device 1
grep -q "NOT_RUN run: no nvidia_gpu 1 row" "${T}/out.txt" || fail "row NOT_RUN reason: $(cat "${T}/out.txt")"

expect_rc 0 "end-to-end run" run_env "${SCRIPT}" run --prefix "${T}/prefix" --shm "${SHM}" \
  --seconds 3 --out "${T}/run"
grep -qF "ROW {10, 0, 128, {128, 256, 512, 8192, 256}}," "${T}/out.txt" || fail "no table row: $(cat "${T}/out.txt")"
tail -n 1 "${T}/out.txt" | grep -q "GPU_TENSOR_CALIBRATE_OK" || fail "no OK verdict: $(cat "${T}/out.txt")"
test -s "${T}/run/summary.txt" || fail "no summary.txt"
[ "$(find "${T}/run" -name 'load_*.txt' | wc -l)" -eq 4 ] || fail "want 4 load outputs"

echo "test_gpu_tensor_calibrate: PASS"
