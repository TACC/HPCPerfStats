#!/usr/bin/env bash
set -euo pipefail

# Run post-deploy CPU peg triage bundle for hpcperfstatsd.
# Usage: ./scripts/track_cpu_peg.sh [duration_minutes]

DURATION_MINUTES="${1:-20}"
if ! [[ "$DURATION_MINUTES" =~ ^[0-9]+$ ]] || [ "$DURATION_MINUTES" -le 0 ]; then
  echo "duration_minutes must be a positive integer" >&2
  exit 2
fi

PID="$(pidof hpcperfstatsd || true)"
if [ -z "${PID}" ]; then
  echo "hpcperfstatsd is not running" >&2
  exit 1
fi

OUT_DIR="${TMPDIR:-/tmp}/hpcperfstatsd-track-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${OUT_DIR}"

SAMPLES=$((DURATION_MINUTES * 60 / 5))
[ "${SAMPLES}" -lt 1 ] && SAMPLES=1

echo "Tracking PID=${PID} for ${DURATION_MINUTES} minutes"
echo "Output: ${OUT_DIR}"

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
echo "  ${OUT_DIR}/strace.txt"
echo "  ${OUT_DIR}/journal-hotpaths.txt"

