#!/usr/bin/env bash
# Capture hpcperfstatsd CPU, RSS/USS, I/O, and namespace-wide net counters, plus the
# 1 Hz sampler (exact CPU-s, memory series, process-scoped AMQP bytes) in samples.jsonl.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${HPCPERFSTATS_PYTHON:-python3}"

INTERVAL=10
COUNT=60
OUT=""
TCPDUMP=0
AMQP_PORT="${AMQP_PORT:-5672}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --count) COUNT="$2"; shift 2 ;;
    --tcpdump) TCPDUMP=1; shift ;;
    --amqp-port) AMQP_PORT="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage: capture_daemon_overhead.sh [--out DIR] [--interval 10] [--count 60]
                                  [--tcpdump] [--amqp-port 5672]

Default window is 10 s × 60 = 10 minutes (same shape as the Stampede3 CPU
capture). Requires sysstat pidstat. Optional --tcpdump needs CAP_NET_RAW.

Network totals from /proc/net/dev are namespace-wide (upper bound). Process
I/O uses /proc/<pid>/io. Memory uses status + smaps_rollup (USS).
EOF
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd pidstat
PID="$(find_daemon_pid)"
[[ -d "/proc/${PID}" ]] || die "PID ${PID} is not running"

if [[ -z "${OUT}" ]]; then
  OUT="$(stamp_dir "${ROOT}" overhead)"
else
  mkdir -p "${OUT}"
fi

HOST="$(hostname -f 2>/dev/null || hostname)"
CONF="$(default_conf_path)"
UNAME="$(uname -a)"
export MT_OUT="${OUT}" MT_HOST="${HOST}" MT_PID="${PID}" \
  MT_INTERVAL="${INTERVAL}" MT_COUNT="${COUNT}" MT_CONF="${CONF}" \
  MT_UNAME="${UNAME}" MT_AMQP="${AMQP_PORT}"
"${PYTHON}" - <<'PY'
import json, os, pathlib, time
path = pathlib.Path(os.environ["MT_OUT"]) / "meta.json"
path.write_text(json.dumps({
    "host": os.environ["MT_HOST"],
    "pid": int(os.environ["MT_PID"]),
    "interval_s": int(os.environ["MT_INTERVAL"]),
    "count": int(os.environ["MT_COUNT"]),
    "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "uname": os.environ["MT_UNAME"],
    "conf_path": os.environ["MT_CONF"],
    "amqp_port": int(os.environ["MT_AMQP"]),
}, indent=2) + "\n", encoding="utf-8")
PY

{
  printf 'host=%s\n' "${HOST}"
  printf 'pid=%s\n' "${PID}"
  printf 'nproc=%s\n' "$(nproc 2>/dev/null || echo unknown)"
  uname -a
} > "${OUT}/host.txt"
uname -a > "${OUT}/uname.txt"

if [[ -n "${CONF}" && -r "${CONF}" ]]; then
  sed -E 's/^(password[[:space:]: =]+).*/\1REDACTED/' "${CONF}" \
    > "${OUT}/hpcperfstats.conf.redacted" || cp "${CONF}" "${OUT}/hpcperfstats.conf.redacted"
fi

snapshot() {
  local tag="$1"
  cat "/proc/${PID}/status" > "${OUT}/proc_status_${tag}.txt"
  if [[ -r "/proc/${PID}/smaps_rollup" ]]; then
    cat "/proc/${PID}/smaps_rollup" > "${OUT}/smaps_rollup_${tag}.txt"
  fi
  cat "/proc/${PID}/io" > "${OUT}/proc_io_${tag}.txt"
  cat /proc/net/dev > "${OUT}/net_dev_${tag}.txt"
  if command -v ss >/dev/null 2>&1; then
    ss -tnp "( sport = :${AMQP_PORT} or dport = :${AMQP_PORT} )" \
      > "${OUT}/ss_amqp_${tag}.txt" 2>/dev/null || true
  fi
}

snapshot before
if command -v ss >/dev/null 2>&1; then
  ss -tnp "( sport = :${AMQP_PORT} or dport = :${AMQP_PORT} )" \
    > "${OUT}/ss_amqp.txt" 2>/dev/null || true
fi

TCPDUMP_PID=""
if [[ "${TCPDUMP}" -eq 1 ]]; then
  if command -v tcpdump >/dev/null 2>&1; then
    tcpdump -n -i any "port ${AMQP_PORT}" -w "${OUT}/amqp.pcap" \
      >/dev/null 2>&1 &
    TCPDUMP_PID="$!"
    log "tcpdump pid ${TCPDUMP_PID} writing ${OUT}/amqp.pcap"
  else
    log "WARN: tcpdump not found; skipping packet capture"
  fi
fi

# Exact CPU-s (schedstat ns) and 1 Hz RSS/USS/FD/tcp_info series next to pidstat's rounded bins.
"${PYTHON}" "${ROOT}/bin/sample_proc.py" --pid "${PID}" --amqp-port "${AMQP_PORT}" \
  --duration "$((INTERVAL * COUNT))" --out "${OUT}/samples.jsonl" 2>"${OUT}/sample_proc.err" &
SAMPLER_PID="$!"

log "pidstat -h -urd -p ${PID} ${INTERVAL} ${COUNT} (this blocks)"
# Combined CPU + memory + I/O. Fall back to CPU-only if -urd is unsupported.
set +e
pidstat -h -urd -p "${PID}" "${INTERVAL}" "${COUNT}" > "${OUT}/pidstat.txt" 2>"${OUT}/pidstat.err"
PIDSTAT_RC=$?
set -e
if [[ "${PIDSTAT_RC}" -ne 0 ]] || ! grep -q '%CPU' "${OUT}/pidstat.txt"; then
  log "WARN: pidstat -h -urd failed; falling back to CPU-only pidstat"
  pidstat -p "${PID}" "${INTERVAL}" "${COUNT}" > "${OUT}/pidstat.txt"
fi

if [[ -n "${TCPDUMP_PID}" ]]; then
  kill "${TCPDUMP_PID}" 2>/dev/null || true
  wait "${TCPDUMP_PID}" 2>/dev/null || true
  if command -v capinfos >/dev/null 2>&1 && [[ -f "${OUT}/amqp.pcap" ]]; then
    capinfos -Tm "${OUT}/amqp.pcap" > "${OUT}/amqp.pcap.capinfos.txt" || true
  elif command -v tcpdump >/dev/null 2>&1 && [[ -f "${OUT}/amqp.pcap" ]]; then
    tcpdump -r "${OUT}/amqp.pcap" -n -q 2>/dev/null \
      | awk 'END { print "packet_count", NR }' > "${OUT}/amqp.pcap.summary.txt" || true
  fi
fi

wait "${SAMPLER_PID}" 2>/dev/null || log "WARN: sample_proc.py exited non-zero (see sample_proc.err)"
snapshot after

"${PYTHON}" "${ROOT}/bin/summarize_overhead.py" --capture-dir "${OUT}"

log "Wrote ${OUT}/overhead.json"
printf '%s\n' "${OUT}"
