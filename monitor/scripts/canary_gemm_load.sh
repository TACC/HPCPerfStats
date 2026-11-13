#!/usr/bin/env bash
# Background GEMM load for monitor canaries (cpu-precision Phase 1: O1 power/clock A/B, O3
# monitor-vs-perf rate check, DGEMM / SGEMM / HGEMM precision slices).
#
# Builds a small self-contained OpenMP GEMM (no BLAS, no module needed) in double, single and
# half precision, then runs it in the background with a pidfile so the canary blocks can
# measure a steady, known-precision load. FP instructions go to FP_DP_SPEC / FP_SP_SPEC /
# FP_HP_SPEC on Grace, so each binary exercises one CPU pie slice.
#
# This is a load generator, not a monitor build: it compiles only its own benchmark into
# --prefix, so it does not use monitor_tree_clean.sh (monitor-build-clean-workspace.mdc).
#
# Usage:
#   ./scripts/canary_gemm_load.sh install [--prefix DIR] [--cc CC]
#   ./scripts/canary_gemm_load.sh start   [--precision dp|sp|hp] [--threads N] [--seconds S]
#                                         [--size N] [--prefix DIR]
#   ./scripts/canary_gemm_load.sh status  [--prefix DIR]
#   ./scripts/canary_gemm_load.sh stop    [--prefix DIR]
#   ./scripts/canary_gemm_load.sh run     [same options as start]   (foreground, e.g. in sbatch)
#
# Defaults: --prefix ${HPCPERFSTATS_GEMM_PREFIX:-$HOME/hpcperfstats-gemm-load}, --precision dp,
# --threads = online CPUs, --seconds 0 (until stopped), --size 1024, --cc ${CC:-cc}.
# On TACC, `module load gcc` first if the system compiler lacks OpenMP (tacc-lmod-build-environment).
# The website pie only attributes load that runs inside a Slurm job: use `run` in the job script.
set -euo pipefail

PREFIX="${HPCPERFSTATS_GEMM_PREFIX:-${HOME}/hpcperfstats-gemm-load}"
CC_BIN="${CC:-cc}"
PRECISION="dp"
THREADS=""
SECONDS_RUN=0
SIZE=1024

usage() {
  sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  echo "canary_gemm_load: $*" >&2
  exit 2
}

is_uint() {
  case "$1" in
  '' | *[!0-9]*) return 1 ;;
  *) return 0 ;;
  esac
}

cmd="${1:-}"
[ -n "${cmd}" ] || {
  usage
  exit 2
}
shift
case "${cmd}" in
-h | --help | help)
  usage
  exit 0
  ;;
install | start | stop | status | run) ;;
*) die "unknown command '${cmd}' (install|start|stop|status|run)" ;;
esac

while [ $# -gt 0 ]; do
  case "$1" in
  --prefix | --cc | --precision | --threads | --seconds | --size)
    [ $# -ge 2 ] || die "$1 requires a value"
    case "$1" in
    --prefix) PREFIX="$2" ;;
    --cc) CC_BIN="$2" ;;
    --precision) PRECISION="$2" ;;
    --threads) THREADS="$2" ;;
    --seconds) SECONDS_RUN="$2" ;;
    --size) SIZE="$2" ;;
    esac
    shift 2
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *) die "unknown option '$1'" ;;
  esac
done

case "${PRECISION}" in
dp | sp | hp) ;;
*) die "--precision must be dp, sp or hp (got '${PRECISION}')" ;;
esac
[ -z "${THREADS}" ] || { is_uint "${THREADS}" && [ "${THREADS}" -gt 0 ]; } ||
  die "--threads must be a positive integer"
is_uint "${SECONDS_RUN}" || die "--seconds must be a non-negative integer"
{ is_uint "${SIZE}" && [ "${SIZE}" -ge 16 ] && [ "${SIZE}" -le 8192 ]; } ||
  die "--size must be an integer in 16..8192"
[ -n "${THREADS}" ] || THREADS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"

BIN_DIR="${PREFIX}/bin"
PIDFILE="${PREFIX}/gemm_load.pid"
STATEFILE="${PREFIX}/gemm_load.state"
LOGFILE="${PREFIX}/gemm_load.log"

arch_flags() {
  case "$(uname -m)" in
  aarch64 | arm64) echo "-mcpu=native" ;;
  x86_64 | i?86) echo "-march=native" ;;
  *) echo "" ;;
  esac
}

write_source() {
  mkdir -p "${PREFIX}/src"
  cat >"${PREFIX}/src/gemm_load.c" <<'EOF'
/* gemm_load: C = A x B in a loop on all threads (OpenMP), element type T from -DGEMM_T.
 * Prints achieved GFLOP/s every ~5 s; runs for argv[3] seconds (0 = until killed). */
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef GEMM_T
#define GEMM_T double
#endif
#ifndef GEMM_NAME
#define GEMM_NAME "dp"
#endif
typedef GEMM_T T;

int main(int argc, char **argv)
{
  long n = argc > 1 ? atol(argv[1]) : 1024;
  int threads = argc > 2 ? atoi(argv[2]) : omp_get_max_threads();
  double seconds = argc > 3 ? atof(argv[3]) : 0.0;
  if (n < 16 || n > 8192 || threads < 1 || seconds < 0) {
    fprintf(stderr, "usage: %s N THREADS SECONDS\n", argv[0]);
    return 2;
  }
  omp_set_num_threads(threads);
  T *a = malloc(sizeof(T) * (size_t)(n * n));
  T *b = malloc(sizeof(T) * (size_t)(n * n));
  T *c = malloc(sizeof(T) * (size_t)(n * n));
  if (!a || !b || !c) {
    fprintf(stderr, "gemm_load: out of memory for n=%ld\n", n);
    return 1;
  }
  /* Entries 1/n keep every C element near 1, finite even in half precision. */
  for (long i = 0; i < n * n; i++) {
    a[i] = (T)(1.0 / (double)n);
    b[i] = (T)1.0;
  }
  printf("gemm_load %s n=%ld threads=%d seconds=%g started\n", GEMM_NAME, n, threads, seconds);
  fflush(stdout);
  double t0 = omp_get_wtime(), last = t0, flops = 0.0;
  for (;;) {
#pragma omp parallel for schedule(static)
    for (long i = 0; i < n; i++) {
      T *ci = c + i * n;
      memset(ci, 0, sizeof(T) * (size_t)n);
      for (long k = 0; k < n; k++) {
        const T aik = a[i * n + k];
        const T *bk = b + k * n;
        for (long j = 0; j < n; j++)
          ci[j] += aik * bk[j];
      }
    }
    flops += 2.0 * (double)n * (double)n * (double)n;
    double now = omp_get_wtime();
    if (now - last >= 5.0 || (seconds > 0 && now - t0 >= seconds)) {
      printf("gemm_load %s GFLOP/s=%.2f elapsed=%.0fs check=%.3f\n", GEMM_NAME,
             flops / (now - last) / 1e9, now - t0, (double)c[(n / 2) * n + n / 2]);
      fflush(stdout);
      flops = 0.0;
      last = now;
    }
    if (seconds > 0 && now - t0 >= seconds)
      break;
  }
  free(a);
  free(b);
  free(c);
  return 0;
}
EOF
}

build_one() {
  local prec="$1" type="$2"
  local out="${BIN_DIR}/gemm_load_${prec}"
  # shellcheck disable=SC2046
  if "${CC_BIN}" -O3 -ffast-math $(arch_flags) -fopenmp -DGEMM_T="${type}" \
    -DGEMM_NAME="\"${prec}\"" -o "${out}" "${PREFIX}/src/gemm_load.c" \
    2>"${PREFIX}/build_${prec}.log"; then
    echo "built ${out}"
    return 0
  fi
  rm -f "${out}"
  echo "could not build ${prec} (see ${PREFIX}/build_${prec}.log)" >&2
  return 1
}

do_install() {
  command -v "${CC_BIN}" >/dev/null 2>&1 || die "compiler '${CC_BIN}' not found (module load gcc?)"
  mkdir -p "${BIN_DIR}"
  write_source
  build_one dp double || die "dp build failed; need a C compiler with -fopenmp"
  build_one sp float || die "sp build failed"
  build_one hp _Float16 || echo "hp skipped: this compiler/arch has no _Float16" >&2
}

running_pid() {
  local pid
  [ -f "${PIDFILE}" ] || return 1
  pid="$(cat "${PIDFILE}" 2>/dev/null || true)"
  is_uint "${pid}" || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  echo "${pid}"
}

binary_for() {
  local bin="${BIN_DIR}/gemm_load_${PRECISION}"
  if [ ! -x "${bin}" ]; then
    do_install >&2
  fi
  [ -x "${bin}" ] || die "no ${PRECISION} binary (hp needs a compiler with _Float16)"
  echo "${bin}"
}

do_run() {
  local bin
  bin="$(binary_for)"
  OMP_NUM_THREADS="${THREADS}" OMP_PROC_BIND="${OMP_PROC_BIND:-spread}" \
    OMP_PLACES="${OMP_PLACES:-cores}" exec "${bin}" "${SIZE}" "${THREADS}" "${SECONDS_RUN}"
}

do_start() {
  local bin pid
  if pid="$(running_pid)"; then
    die "already running (pid ${pid}, $(cat "${STATEFILE}" 2>/dev/null)); stop it first"
  fi
  bin="$(binary_for)"
  mkdir -p "${PREFIX}"
  OMP_NUM_THREADS="${THREADS}" OMP_PROC_BIND="${OMP_PROC_BIND:-spread}" \
    OMP_PLACES="${OMP_PLACES:-cores}" nohup "${bin}" "${SIZE}" "${THREADS}" "${SECONDS_RUN}" \
    >"${LOGFILE}" 2>&1 </dev/null &
  pid=$!
  echo "${pid}" >"${PIDFILE}"
  echo "precision=${PRECISION} threads=${THREADS} size=${SIZE} seconds=${SECONDS_RUN}" \
    >"${STATEFILE}"
  sleep 1
  kill -0 "${pid}" 2>/dev/null || {
    rm -f "${PIDFILE}"
    cat "${LOGFILE}" >&2
    die "gemm_load exited at start"
  }
  echo "started pid ${pid}: $(cat "${STATEFILE}") log ${LOGFILE}"
}

do_status() {
  local pid
  if pid="$(running_pid)"; then
    echo "running pid ${pid}: $(cat "${STATEFILE}" 2>/dev/null)"
    tail -1 "${LOGFILE}" 2>/dev/null || true
    return 0
  fi
  echo "not running"
  [ ! -f "${LOGFILE}" ] || tail -1 "${LOGFILE}"
  return 1
}

do_stop() {
  local pid i
  if ! pid="$(running_pid)"; then
    rm -f "${PIDFILE}"
    echo "not running"
    return 0
  fi
  kill "${pid}"
  for i in $(seq 50); do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 0.1
  done
  kill -0 "${pid}" 2>/dev/null && kill -9 "${pid}"
  rm -f "${PIDFILE}"
  echo "stopped pid ${pid}"
  tail -1 "${LOGFILE}" 2>/dev/null || true
}

case "${cmd}" in
install) do_install ;;
start) do_start ;;
stop) do_stop ;;
status) do_status ;;
run) do_run ;;
esac
