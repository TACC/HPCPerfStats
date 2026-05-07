#!/usr/bin/env bash
set -euo pipefail

# Run post-deploy CPU peg triage bundle for hpcperfstatsd.
# Usage:
#   ./scripts/track_cpu_peg.sh [duration_minutes]
#   ./scripts/track_cpu_peg.sh --duration 20 --out-dir ./cpu_peg_artifacts/latest
#   ./scripts/track_cpu_peg.sh --debug-bin /path/to/unstripped/hpcperfstatsd

DURATION_MINUTES=20
OUT_DIR=""
DEBUG_BIN="${HPCPERFSTATS_DEBUG_BIN:-}"

usage() {
  cat <<'EOF'
Usage: ./scripts/track_cpu_peg.sh [duration_minutes] [options]

Options:
  --duration <minutes>   Sampling duration (positive integer, default: 20)
  --out-dir <path>       Output directory for artifacts (default: /tmp timestamp dir)
  --debug-bin <path>     Unstripped hpcperfstatsd binary with symbols
  -h, --help             Show this help

Env:
  HPCPERFSTATS_DEBUG_BIN Alternative way to provide debug binary path
EOF
}

while (($# > 0)); do
  case "$1" in
    --duration)
      shift
      [ $# -gt 0 ] || { echo "--duration requires a value" >&2; exit 2; }
      DURATION_MINUTES="$1"
      ;;
    --out-dir)
      shift
      [ $# -gt 0 ] || { echo "--out-dir requires a value" >&2; exit 2; }
      OUT_DIR="$1"
      ;;
    --debug-bin)
      shift
      [ $# -gt 0 ] || { echo "--debug-bin requires a value" >&2; exit 2; }
      DEBUG_BIN="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        DURATION_MINUTES="$1"
      else
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
  shift
done

if ! [[ "$DURATION_MINUTES" =~ ^[0-9]+$ ]] || [ "$DURATION_MINUTES" -le 0 ]; then
  echo "duration_minutes must be a positive integer" >&2
  exit 2
fi

PID="$(pidof hpcperfstatsd || true)"
if [ -z "${PID}" ]; then
  echo "hpcperfstatsd is not running" >&2
  exit 1
fi

if [ -z "${OUT_DIR}" ]; then
  OUT_DIR="${TMPDIR:-/tmp}/hpcperfstatsd-track-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "${OUT_DIR}"

SAMPLES=$((DURATION_MINUTES * 60 / 5))
[ "${SAMPLES}" -lt 1 ] && SAMPLES=1

RUN_BIN="$(readlink -f /proc/${PID}/exe 2>/dev/null || true)"
if [ -z "${RUN_BIN}" ]; then
  RUN_BIN="/usr/sbin/hpcperfstatsd"
fi

debug_note="none"
if [ -n "${DEBUG_BIN}" ]; then
  if [ ! -f "${DEBUG_BIN}" ]; then
    echo "debug binary does not exist: ${DEBUG_BIN}" >&2
    exit 2
  fi
  if command -v readelf >/dev/null 2>&1; then
    run_buildid="$(readelf -n "${RUN_BIN}" 2>/dev/null | awk '/Build ID/ {print $3; exit}')"
    dbg_buildid="$(readelf -n "${DEBUG_BIN}" 2>/dev/null | awk '/Build ID/ {print $3; exit}')"
    if [ -n "${run_buildid}" ] && [ -n "${dbg_buildid}" ] && [ "${run_buildid}" != "${dbg_buildid}" ]; then
      echo "WARNING: debug binary Build ID does not match running daemon" | tee -a "${OUT_DIR}/debug-build-id.txt"
      echo "running (${RUN_BIN}): ${run_buildid}" | tee -a "${OUT_DIR}/debug-build-id.txt"
      echo "debug   (${DEBUG_BIN}): ${dbg_buildid}" | tee -a "${OUT_DIR}/debug-build-id.txt"
      debug_note="mismatch"
    else
      {
        echo "Build IDs match"
        echo "running (${RUN_BIN}): ${run_buildid:-unknown}"
        echo "debug   (${DEBUG_BIN}): ${dbg_buildid:-unknown}"
      } > "${OUT_DIR}/debug-build-id.txt"
      debug_note="matched"
      perf buildid-cache -v -a "${DEBUG_BIN}" > "${OUT_DIR}/perf-buildid-cache.txt" 2>&1 || true
    fi
  fi
fi

echo "Tracking PID=${PID} for ${DURATION_MINUTES} minutes"
echo "Output: ${OUT_DIR}"
echo "Running binary: ${RUN_BIN}"
echo "Debug binary: ${DEBUG_BIN:-"(not set)"} (${debug_note})"

pidstat -u -p "${PID}" 5 "${SAMPLES}" > "${OUT_DIR}/pidstat.txt" 2>&1 &
PIDSTAT_PID=$!

perf stat -p "${PID}" -e cycles,instructions,cache-misses,context-switches -I 5000 \
  > "${OUT_DIR}/perf-stat.txt" 2>&1 &
PERFSTAT_PID=$!

perf record -F 199 -g --call-graph dwarf -o "${OUT_DIR}/perf.data" -p "${PID}" -- \
  sleep "$((DURATION_MINUTES * 60))" > "${OUT_DIR}/perf-record.txt" 2>&1 &
PERFREC_PID=$!

strace -tt -f -p "${PID}" \
  -e trace=clock_nanosleep,epoll_wait,poll,read,openat,close \
  -o "${OUT_DIR}/strace.txt" &
STRACE_PID=$!

sleep "$((DURATION_MINUTES * 60))"

kill "${STRACE_PID}" "${PERFSTAT_PID}" "${PIDSTAT_PID}" 2>/dev/null || true
wait "${STRACE_PID}" "${PERFSTAT_PID}" "${PIDSTAT_PID}" "${PERFREC_PID}" 2>/dev/null || true

if [ -s "${OUT_DIR}/perf.data" ]; then
  perf report --stdio --no-children --sort dso,symbol -i "${OUT_DIR}/perf.data" \
    > "${OUT_DIR}/perf-report.txt" 2>&1 || true
  perf report --stdio -g graph,0.5,caller --sort comm,dso,symbol -i "${OUT_DIR}/perf.data" \
    > "${OUT_DIR}/perf-report-callgraph.txt" 2>&1 || true
  perf report --stdio --sort dso,symbol,srcline --show-total-period -i "${OUT_DIR}/perf.data" \
    > "${OUT_DIR}/perf-report-srcline.txt" 2>&1 || true
  perf script -i "${OUT_DIR}/perf.data" > "${OUT_DIR}/perf-script.txt" 2>&1 || true
else
  echo "perf.data missing or empty: perf record likely failed" > "${OUT_DIR}/perf-report.txt"
fi

journalctl -u hpcperfstatsd -S "-${DURATION_MINUTES}m" \
  | grep -E 'slow|drift|reconnect|warmup|replay|jobid|probe' \
  > "${OUT_DIR}/journal-hotpaths.txt" 2>/dev/null || true

chmod -R a+rX "${OUT_DIR}" 2>/dev/null || true

echo "Done. Artifacts:"
echo "  ${OUT_DIR}/pidstat.txt"
echo "  ${OUT_DIR}/perf-stat.txt"
echo "  ${OUT_DIR}/perf-report.txt"
echo "  ${OUT_DIR}/perf-report-callgraph.txt"
echo "  ${OUT_DIR}/perf-report-srcline.txt"
echo "  ${OUT_DIR}/perf-script.txt"
echo "  ${OUT_DIR}/strace.txt"
echo "  ${OUT_DIR}/journal-hotpaths.txt"

