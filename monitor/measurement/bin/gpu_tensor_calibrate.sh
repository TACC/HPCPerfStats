#!/usr/bin/env bash
# Calibrate the per-SM-per-clock tensor peaks (g_peaks HMMA / DMMA columns of
# src/nvidia_gpu_estimate.c) on one GPU against the DEBUG monitor's own nvidia_gpu row.
#
# Correctness-testing tool of the measurement kit only: never built by the RPM, the spec or the
# verify scripts, and never linked into hpcperfstatsd (the daemon only dlopens vendor libraries,
# which are not on every node).
#
#   build  (as the user): load a CUDA module through Lmod, find nvcc and cuBLAS
#          (lib/gpu_toolchain.sh) and build bin/gpu_tensor_calibrate.cu into PREFIX, statically
#          linked to cudart / cuBLAS when the archives exist so root runs it without modules.
#   run    (as root, while the DEBUG monitor runs): keep every DEBUG shm fast payload, run the
#          calibrator for two sizes each of an FP16 GEMM (HMMA) and a DGEMM (DMMA) with idle gaps
#          between them, then lib/gpu_calibrate_eval.py computes FLOP/s / (activity x SMs x clock)
#          per size, requires the sizes to agree within 5 percent and prints the g_peaks row.
#
# Usage: gpu_tensor_calibrate.sh build [--prefix DIR]
#        gpu_tensor_calibrate.sh run [--prefix DIR] [--device N] [--seconds S] [--shm DIR] [--out DIR]
# Exit: 0 OK, 1 FAIL, 2 usage, 3 NOT_RUN (reason printed).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEAS="$(cd "${HERE}/.." && pwd)"
MON="$(cd "${MEAS}/.." && pwd)"
PY="${PYTHON:-python3}"
DEFAULT_PREFIX="${MEAS}/results/gpu_tensor_calibrate"
PREFIX="${DEFAULT_PREFIX}"
SHM=/dev/shm/hpcperfstatsd-debug
DEV=0
MIN_SECONDS=20
OUT="${MEAS}/results/gpu_tensor_$(hostname -s)_$(date -u +%Y%m%dT%H%M%SZ)"
# kind:n pairs; two sizes per kind, each large enough to keep every SM busy.
LOADS="hmma:8192 hmma:16384 dmma:4096 dmma:8192"
GENCODE=(-gencode "arch=compute_80,code=sm_80" -gencode "arch=compute_90,code=sm_90"
  -gencode "arch=compute_100,code=sm_100" -gencode "arch=compute_120,code=[sm_120,compute_120]")

usage() {
  sed -n 's/^# Usage: /usage: /p; s/^#        gpu_/       gpu_/p' "${BASH_SOURCE[0]}" >&2
  exit 2
}
not_run() {
  echo "NOT_RUN $*"
  exit 3
}

MODE="${1:-}"
[ $# -gt 0 ] && shift
case "${MODE}" in build | run) ;; *) usage ;; esac
while [ $# -gt 0 ]; do
  [ $# -ge 2 ] || usage
  case "${MODE} $1" in
    "build --prefix" | "run --prefix") PREFIX="$2" ;;
    "run --device") DEV="$2" ;;
    "run --seconds") MIN_SECONDS="$2" ;;
    "run --shm") SHM="$2" ;;
    "run --out") OUT="$2" ;;
    *) usage ;;
  esac
  shift 2
done
case "${DEV}" in '' | *[!0-9]*) usage ;; esac
case "${MIN_SECONDS}" in '' | *[!0-9]* | 0) usage ;; esac
BIN="${PREFIX}/gpu_tensor_calibrate"

if [ "${MODE}" = build ]; then
  # shellcheck source=../lib/gpu_toolchain.sh
  . "${MEAS}/lib/gpu_toolchain.sh"
  gpu_toolchain_resolve || not_run "build: ${GPU_TC_NOT_RUN}"
  mkdir -p "${PREFIX}" || exit 1
  if ! "${GPU_TC_NVCC}" -O2 -std=c++17 "${GENCODE[@]}" -I"${GPU_TC_CUBLAS_INC}" \
    -o "${BIN}" "${HERE}/gpu_tensor_calibrate.cu" "${GPU_TC_LINK[@]}" > "${PREFIX}/build.log" 2>&1; then
    echo "BUILD_FAIL nvcc failed (see ${PREFIX}/build.log)"
    tail -n 5 "${PREFIX}/build.log"
    exit 1
  fi
  # Root reads the binary from the user's tree (often root-squashed NFS).
  chmod 755 "${PREFIX}" "${BIN}"
  {
    echo "host $(hostname -s) $(uname -m) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "modules ${GPU_TC_MODULES}"
    echo "nvcc ${GPU_TC_NVCC} cuda ${GPU_TC_CUDA_VER}"
    echo "cublas ${GPU_TC_CUBLAS_LIB} link ${GPU_TC_LINK_MODE}"
    [ -n "${GPU_TC_NOTE}" ] && echo "note ${GPU_TC_NOTE}"
    echo "sha256 $(sha256sum "${BIN}" | cut -d' ' -f1)"
  } > "${PREFIX}/build-info.txt"
  [ -n "${GPU_TC_NOTE}" ] && echo "NOTE ${GPU_TC_NOTE}"
  echo "BUILD_OK ${BIN} link=${GPU_TC_LINK_MODE}"
  ROOT_RUN="${HERE}/gpu_tensor_calibrate.sh run"
  [ "${PREFIX}" = "${DEFAULT_PREFIX}" ] || ROOT_RUN="${ROOT_RUN} --prefix ${PREFIX}"
  echo "as root, with the DEBUG monitor running: ${ROOT_RUN}"
  exit 0
fi

[ -x "${BIN}" ] || not_run "run: not built (no ${BIN}); run '$0 build' as the user first"
if [ ! -r "${SHM}/fast" ] || [ ! -r "${SHM}/schema" ]; then
  not_run "run: no ${SHM}/fast or schema (is the DEBUG monitor running? run as root)"
fi
grep -q "^nvidia_gpu ${DEV} " "${SHM}/fast" \
  || not_run "run: no nvidia_gpu ${DEV} row in ${SHM}/fast"
mkdir -p "${OUT}/samples" || exit 1
cp "${SHM}/schema" "${OUT}/schema.txt"
cp "${PREFIX}/build-info.txt" "${OUT}/" 2>/dev/null
# CUDA ordinals in PCI order match the NVML index the nvidia_gpu row is keyed by; cuBLAS math
# emulation must not replace the tensor pipes being measured.
export CUDA_DEVICE_ORDER=PCI_BUS_ID
unset CUDA_VISIBLE_DEVICES CUBLAS_EMULATION_STRATEGY

say() { printf '%s\n' "$*" | tee -a "${OUT}/summary.txt"; }
head_ts() { awk 'NF { print $1; exit }' "$1" 2>/dev/null; }
# Copy each new fast payload (by its header timestamp) until OUT/.stop exists.
sampler() {
  local last="" seq=0 ts
  while [ ! -e "${OUT}/.stop" ]; do
    cp "${SHM}/fast" "${OUT}/samples/.tmp" 2>/dev/null
    ts="$(head_ts "${OUT}/samples/.tmp")"
    if [ -n "${ts}" ] && [ "${ts}" != "${last}" ]; then
      mv "${OUT}/samples/.tmp" "${OUT}/samples/s$(printf '%06d' "${seq}").txt"
      seq=$((seq + 1))
      last="${ts}"
    fi
    sleep 0.2
  done
  rm -f "${OUT}/samples/.tmp"
}
SAMPLER=""
# shellcheck disable=SC2317  # invoked via trap
cleanup() {
  touch "${OUT}/.stop"
  [ -n "${SAMPLER}" ] && wait "${SAMPLER}" 2>/dev/null
  rm -f "${OUT}/.stop"
}
trap cleanup EXIT INT TERM HUP
sampler &
SAMPLER=$!

say "host $(hostname -s) device ${DEV} shm ${SHM} out ${OUT}"
# Sample interval from the first two new payloads (bounded wait: ten DEBUG intervals of 60 s).
deadline=$(( $(date +%s) + 600 ))
while [ "$(find "${OUT}/samples" -name 's*.txt' | wc -l)" -lt 3 ]; do
  [ "$(date +%s)" -lt "${deadline}" ] || { say "NOT_RUN run: ${SHM}/fast stopped advancing"; exit 3; }
  sleep 0.5
done
set -- "${OUT}"/samples/s*.txt
INTERVAL="$(awk -v a="$(head_ts "$2")" -v b="$(head_ts "$3")" 'BEGIN { print b - a }')"
# Four intervals per load leave at least three samples wholly inside it; three idle intervals
# between loads give each a clean before / after sample.
LOAD_S="$(awk -v i="${INTERVAL}" -v m="${MIN_SECONDS}" 'BEGIN { s = 4 * i; print (s > m ? s : m) }')"
GAP="$(awk -v i="${INTERVAL}" 'BEGIN { print 3 * i }')"
say "sample interval ${INTERVAL} s, load ${LOAD_S} s, idle gap ${GAP} s"

i=0
for load in ${LOADS}; do
  kind="${load%%:*}" n="${load##*:}"
  sleep "${GAP}"
  say "load ${kind} n=${n}"
  "${BIN}" --kind "${kind}" --n "${n}" --seconds "${LOAD_S}" --device "${DEV}" \
    > "${OUT}/load_${i}_${kind}_${n}.txt" 2>&1 \
    || say "WARN calibrator ${kind} n=${n} exited non-zero (see load_${i}_${kind}_${n}.txt)"
  i=$((i + 1))
done
sleep "${GAP}"
cleanup
trap - EXIT INT TERM HUP

PYTHONPATH="${MEAS}" "${PY}" -m lib.gpu_calibrate_eval "${OUT}" "${MON}/src" "${DEV}" \
  | tee -a "${OUT}/summary.txt"
exit "${PIPESTATUS[0]}"
