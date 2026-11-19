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
#   ./scripts/canary_gemm_load.sh install [--prefix DIR] [--cc CC] [--cflags "FLAGS"]
#   ./scripts/canary_gemm_load.sh start   [--precision dp|sp|hp] [--threads N] [--seconds S]
#                                         [--size N] [--prefix DIR]
#   ./scripts/canary_gemm_load.sh status  [--prefix DIR]
#   ./scripts/canary_gemm_load.sh stop    [--prefix DIR]
#   ./scripts/canary_gemm_load.sh run     [same options as start]   (foreground, e.g. in sbatch)
#   ./scripts/canary_gemm_load.sh run     --kernel read|write [--mib N] [--threads N] [--seconds S]
#
# Defaults: --prefix ${HPCPERFSTATS_GEMM_PREFIX:-$HOME/hpcperfstats-gemm-load}, --precision dp,
# --threads = online CPUs, --seconds 0 (until stopped), --size 1024, --cc ${CC:-cc}.
# --cflags adds compiler flags, e.g. "-mprefer-vector-width=512" for 512-bit math on x86.
# --kernel read (default gemm) sums an --mib MiB array (default 4096) on every thread instead:
# each pass reads the whole array from DRAM, and a bounded run ends with the exact bytes read
# ("done total_bytes_read="), the oracle for DRAM read counters. --precision and --size are
# ignored for it.
# --kernel write overwrites every cache line of the --mib MiB array on every pass (plain stores,
# pass p stores the value p) and a bounded run ends with "write_load done total_bytes_written=N
# passes=P elapsed=Ss", N = P x array bytes, the oracle for DRAM write counters. Write-allocate
# (RFO) fills add DRAM read traffic equal to the bytes written, so a write check compares CAS
# writes only; CAS writes equal the bytes written because the array is far larger than the LLC
# and every dirty line is evicted. For both kernels the first touch of the array happens in
# setup before the timed passes and is not counted (a process-wide counter still sees it).
# On TACC, `module load gcc` first if the system compiler lacks OpenMP (tacc-lmod-build-environment).
# The website pie only attributes load that runs inside a Slurm job: use `run` in the job script.
set -euo pipefail

PREFIX="${HPCPERFSTATS_GEMM_PREFIX:-${HOME}/hpcperfstats-gemm-load}"
CC_BIN="${CC:-cc}"
EXTRA_CFLAGS=""
PRECISION="dp"
THREADS=""
SECONDS_RUN=0
SIZE=1024
KERNEL="gemm"
MIB=4096

usage() {
  sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'
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
  --prefix | --cc | --cflags | --precision | --threads | --seconds | --size | --kernel | --mib)
    [ $# -ge 2 ] || die "$1 requires a value"
    case "$1" in
    --prefix) PREFIX="$2" ;;
    --cc) CC_BIN="$2" ;;
    --cflags) EXTRA_CFLAGS="$2" ;;
    --precision) PRECISION="$2" ;;
    --threads) THREADS="$2" ;;
    --seconds) SECONDS_RUN="$2" ;;
    --size) SIZE="$2" ;;
    --kernel) KERNEL="$2" ;;
    --mib) MIB="$2" ;;
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
case "${KERNEL}" in
gemm) LOAD_ARG="${SIZE}" ;;
read | write) LOAD_ARG="${MIB}" ;;
*) die "--kernel must be gemm, read or write (got '${KERNEL}')" ;;
esac
{ is_uint "${MIB}" && [ "${MIB}" -ge 64 ] && [ "${MIB}" -le 65536 ]; } ||
  die "--mib must be an integer in 64..65536"
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
 * Prints achieved GFLOP/s every ~5 s; runs for argv[3] seconds (0 = until killed), then
 * prints the exact FLOP total of the run. */
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
  double t0 = omp_get_wtime(), last = t0, flops = 0.0, total = 0.0;
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
    total += 2.0 * (double)n * (double)n * (double)n;
    double now = omp_get_wtime();
    if (now - last >= 5.0 || (seconds > 0 && now - t0 >= seconds)) {
      printf("gemm_load %s GFLOP/s=%.2f elapsed=%.0fs check=%.3f\n", GEMM_NAME,
             flops / (now - last) / 1e9, now - t0, (double)c[(n / 2) * n + n / 2]);
      fflush(stdout);
      flops = 0.0;
      last = now;
    }
    if (seconds > 0 && now - t0 >= seconds) {
      /* Exact GEMM FLOPs of the bounded run (perf_raw_flops_check.sh compares counters to it). */
      printf("gemm_load %s done total_flops=%.0f elapsed=%.3fs\n", GEMM_NAME, total, now - t0);
      break;
    }
  }
  free(a);
  free(b);
  free(c);
  return 0;
}
EOF
  cat >"${PREFIX}/src/read_load.c" <<'EOF'
/* read_load: sums an argv[1] MiB double array on argv[2] threads (OpenMP); the array is far
 * larger than the caches, so every pass reads all of it from DRAM. Prints GB/s every ~5 s; runs
 * for argv[3] seconds (0 = until killed), then prints the exact bytes the passes read. The
 * array is written once first (first touch), which is not counted. */
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
  long mib = argc > 1 ? atol(argv[1]) : 4096;
  int threads = argc > 2 ? atoi(argv[2]) : omp_get_max_threads();
  double seconds = argc > 3 ? atof(argv[3]) : 0.0;
  if (mib < 64 || mib > 65536 || threads < 1 || seconds < 0) {
    fprintf(stderr, "usage: %s MIB THREADS SECONDS\n", argv[0]);
    return 2;
  }
  omp_set_num_threads(threads);
  long n = mib * 1024L * 1024L / (long)sizeof(double);
  double *a = malloc(sizeof(double) * (size_t)n);
  if (!a) {
    fprintf(stderr, "read_load: out of memory for %ld MiB\n", mib);
    return 1;
  }
#pragma omp parallel for schedule(static)
  for (long i = 0; i < n; i++)
    a[i] = 1.0;
  printf("read_load mib=%ld threads=%d seconds=%g started\n", mib, threads, seconds);
  fflush(stdout);
  double t0 = omp_get_wtime(), last = t0;
  long passes = 0, interval = 0;
  for (;;) {
    double s = 0.0;
#pragma omp parallel for schedule(static) reduction(+ : s)
    for (long i = 0; i < n; i++)
      s += a[i];
    passes++;
    interval++;
    double now = omp_get_wtime();
    if (now - last >= 5.0 || (seconds > 0 && now - t0 >= seconds)) {
      printf("read_load GB/s=%.2f elapsed=%.0fs check=%.0f\n",
             (double)interval * (double)n * 8.0 / (now - last) / 1e9, now - t0, s);
      fflush(stdout);
      interval = 0;
      last = now;
    }
    if (seconds > 0 && now - t0 >= seconds) {
      printf("read_load done total_bytes_read=%.0f passes=%ld elapsed=%.3fs\n",
             (double)passes * (double)n * 8.0, passes, now - t0);
      break;
    }
  }
  free(a);
  return 0;
}
EOF
  cat >"${PREFIX}/src/write_load.c" <<'EOF'
/* write_load: overwrites every element of an argv[1] MiB double array on argv[2] threads (OpenMP)
 * with plain stores; pass p stores the value p, so no pass is a dead store. The array is far
 * larger than the caches, so every dirty line is evicted to DRAM. Prints GB/s every ~5 s with
 * check = the sum of one double per 4 KiB (pass count x array MiB x 256 after a pass); runs for
 * argv[3] seconds (0 = until killed), then prints the exact bytes the passes wrote. The first
 * touch in setup is not counted. Write-allocate fills add read traffic, never write traffic. */
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>

#define CHECK_STRIDE 512 /* doubles per 4 KiB */

static double sampled_sum(const double *a, long n)
{
  double s = 0.0;
  for (long i = 0; i < n; i += CHECK_STRIDE)
    s += a[i];
  return s;
}

int main(int argc, char **argv)
{
  long mib = argc > 1 ? atol(argv[1]) : 4096;
  int threads = argc > 2 ? atoi(argv[2]) : omp_get_max_threads();
  double seconds = argc > 3 ? atof(argv[3]) : 0.0;
  if (mib < 64 || mib > 65536 || threads < 1 || seconds < 0) {
    fprintf(stderr, "usage: %s MIB THREADS SECONDS\n", argv[0]);
    return 2;
  }
  omp_set_num_threads(threads);
  long n = mib * 1024L * 1024L / (long)sizeof(double);
  double *a = malloc(sizeof(double) * (size_t)n);
  if (!a) {
    fprintf(stderr, "write_load: out of memory for %ld MiB\n", mib);
    return 1;
  }
  /* First touch with the same static schedule as the passes: pages land near their writer. */
#pragma omp parallel for schedule(static)
  for (long i = 0; i < n; i++)
    a[i] = 0.0;
  printf("write_load mib=%ld threads=%d seconds=%g started\n", mib, threads, seconds);
  fflush(stdout);
  double t0 = omp_get_wtime(), last = t0;
  long passes = 0, interval = 0;
  for (;;) {
    const double v = (double)(passes + 1);
#pragma omp parallel for schedule(static)
    for (long i = 0; i < n; i++)
      a[i] = v;
    passes++;
    interval++;
    double now = omp_get_wtime();
    if (now - last >= 5.0 || (seconds > 0 && now - t0 >= seconds)) {
      printf("write_load GB/s=%.2f elapsed=%.0fs check=%.0f\n",
             (double)interval * (double)n * 8.0 / (now - last) / 1e9, now - t0, sampled_sum(a, n));
      fflush(stdout);
      interval = 0;
      last = now;
    }
    if (seconds > 0 && now - t0 >= seconds) {
      printf("write_load done total_bytes_written=%.0f passes=%ld elapsed=%.3fs\n",
             (double)passes * (double)n * 8.0, passes, now - t0);
      break;
    }
  }
  free(a);
  return 0;
}
EOF
}

build_one() {
  local prec="$1" type="$2"
  local out="${BIN_DIR}/gemm_load_${prec}"
  # shellcheck disable=SC2046,SC2086
  if "${CC_BIN}" -O3 -ffast-math $(arch_flags) ${EXTRA_CFLAGS} -fopenmp -DGEMM_T="${type}" \
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
  local k
  for k in read write; do
    # shellcheck disable=SC2046,SC2086
    "${CC_BIN}" -O3 -ffast-math $(arch_flags) ${EXTRA_CFLAGS} -fopenmp \
      -o "${BIN_DIR}/${k}_load" "${PREFIX}/src/${k}_load.c" 2>"${PREFIX}/build_${k}.log" ||
      die "${k}_load build failed (see ${PREFIX}/build_${k}.log)"
    echo "built ${BIN_DIR}/${k}_load"
  done
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
  [ "${KERNEL}" = gemm ] || bin="${BIN_DIR}/${KERNEL}_load"
  if [ ! -x "${bin}" ]; then
    do_install >&2
  fi
  [ -x "${bin}" ] || die "no ${bin##*/} binary (hp needs a compiler with _Float16)"
  echo "${bin}"
}

do_run() {
  local bin
  bin="$(binary_for)"
  OMP_NUM_THREADS="${THREADS}" OMP_PROC_BIND="${OMP_PROC_BIND:-spread}" \
    OMP_PLACES="${OMP_PLACES:-cores}" exec "${bin}" "${LOAD_ARG}" "${THREADS}" "${SECONDS_RUN}"
}

do_start() {
  local bin pid
  if pid="$(running_pid)"; then
    die "already running (pid ${pid}, $(cat "${STATEFILE}" 2>/dev/null)); stop it first"
  fi
  bin="$(binary_for)"
  mkdir -p "${PREFIX}"
  OMP_NUM_THREADS="${THREADS}" OMP_PROC_BIND="${OMP_PROC_BIND:-spread}" \
    OMP_PLACES="${OMP_PLACES:-cores}" nohup "${bin}" "${LOAD_ARG}" "${THREADS}" "${SECONDS_RUN}" \
    >"${LOGFILE}" 2>&1 </dev/null &
  pid=$!
  echo "${pid}" >"${PIDFILE}"
  echo "kernel=${KERNEL} precision=${PRECISION} threads=${THREADS} size=${SIZE} mib=${MIB} seconds=${SECONDS_RUN}" \
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
  local pid
  if ! pid="$(running_pid)"; then
    rm -f "${PIDFILE}"
    echo "not running"
    return 0
  fi
  kill "${pid}"
  for _ in $(seq 50); do
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
