# Shared helpers for monitor/measurement capture scripts.
# shellcheck shell=bash

measurement_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
  cd "${here}/.." && pwd
}

log() {
  printf '%s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

find_daemon_pid() {
  local pid
  if [[ -n "${HPCPERFSTATSD_PID:-}" ]]; then
    printf '%s\n' "${HPCPERFSTATSD_PID}"
    return 0
  fi
  pid="$(pgrep -x hpcperfstatsd | head -n 1 || true)"
  if [[ -z "${pid}" ]]; then
    pid="$(pgrep -f '/hpcperfstatsd( |$)' | head -n 1 || true)"
  fi
  [[ -n "${pid}" ]] || die "hpcperfstatsd PID not found (set HPCPERFSTATSD_PID)"
  printf '%s\n' "${pid}"
}

default_conf_path() {
  if [[ -n "${HPCPERFSTATS_CONF:-}" ]]; then
    printf '%s\n' "${HPCPERFSTATS_CONF}"
    return 0
  fi
  for candidate in \
    /etc/hpcperfstats/hpcperfstats.conf \
    /etc/hpcperfstats.conf \
    /usr/local/etc/hpcperfstats.conf; do
    if [[ -r "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  printf '%s\n' ""
}

stamp_dir() {
  local root="$1"
  local label="${2:-capture}"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local out="${root}/results/${label}_${stamp}"
  mkdir -p "${out}"
  printf '%s\n' "${out}"
}
