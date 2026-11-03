#!/usr/bin/env bash
# Capture site hpcperfstats.conf cadence keys and optional pidstat busy spacing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${HPCPERFSTATS_PYTHON:-python3}"

OUT=""
PIDSTAT_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --pidstat) PIDSTAT_FILE="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage: capture_monitor_cadence.sh [--out DIR] [--pidstat FILE]

Reads the site hpcperfstats.conf (or HPCPERFSTATS_CONF) and writes cadence.json.
Optionally folds busy-bin spacing from a pidstat capture so authors can compare
configured sample_freq with observed CPU bursts — without equating them.
EOF
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "${OUT}" ]]; then
  OUT="$(stamp_dir "${ROOT}" cadence)"
else
  mkdir -p "${OUT}"
fi

CONF="$(default_conf_path)"
HOST="$(hostname -f 2>/dev/null || hostname)"
META="${OUT}/meta.txt"
{
  printf 'host=%s\n' "${HOST}"
  printf 'date_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'uname=%s\n' "$(uname -a)"
  printf 'conf_path=%s\n' "${CONF:-MISSING}"
} > "${META}"

if [[ -n "${CONF}" && -r "${CONF}" ]]; then
  cp "${CONF}" "${OUT}/hpcperfstats.conf"
  # Strip password for the copy that may be archived with the paper.
  if command -v sed >/dev/null 2>&1; then
    sed -E 's/^(password[[:space:]: =]+).*/\1REDACTED/' \
      "${OUT}/hpcperfstats.conf" > "${OUT}/hpcperfstats.conf.redacted"
  fi
else
  log "WARN: site conf not readable; cadence keys will be missing"
  : > "${OUT}/hpcperfstats.conf"
fi

PIDSTAT_ARG=()
if [[ -n "${PIDSTAT_FILE}" ]]; then
  PIDSTAT_ARG=(--pidstat "${PIDSTAT_FILE}")
elif [[ -f "${OUT}/pidstat.txt" ]]; then
  PIDSTAT_ARG=(--pidstat "${OUT}/pidstat.txt")
fi

if [[ ${#PIDSTAT_ARG[@]} -gt 0 ]]; then
  "${PYTHON}" "${ROOT}/bin/summarize_cadence.py" \
    --conf "${OUT}/hpcperfstats.conf" \
    --host "${HOST}" \
    --out "${OUT}/cadence.json" \
    "${PIDSTAT_ARG[@]}"
else
  "${PYTHON}" "${ROOT}/bin/summarize_cadence.py" \
    --conf "${OUT}/hpcperfstats.conf" \
    --host "${HOST}" \
    --out "${OUT}/cadence.json"
fi

log "Wrote ${OUT}/cadence.json"
printf '%s\n' "${OUT}"
