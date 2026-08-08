#!/usr/bin/env bash
# Stampede3 one-binary /dev/shm validate for a single LSPCI queue profile.
#
# Run on a compute node of the given queue class after the same DEBUG fleet
# binary/RPM is installed and producing DEBUG shm payloads.
#
# Usage:
#   ./scripts/validate_stampede3_profile.sh --profile h100
#   CAPS_JSON=/path/to/monitor-build-capabilities.json \
#     ./scripts/validate_stampede3_profile.sh --profile skx
#
# Env (optional): CAPS_JSON, BUILD_DIR, HPCPERFSTATS_DEBUG_SHM_DIR,
#   ENABLE_SLOW_TIER, WORKSPACE_ROOT, WAIT_SHM_SECONDS, HPCPERFSTATS_CONF,
#   RELAX_PROFILE_CONTRACT,
#   CROSS_SAMPLE_CHECK / STRICT_* (default 1; set 0 to disable),
#   GOLDEN_DIR=/path|auto or GOLDEN_CHECK=1 (opt-in golden diff)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MONITOR_DIR="${MONITOR_DIR:-${SCRIPT_DIR}/..}"

env_flag_on() {
  case "${1:-1}" in
  0 | false | FALSE | no | NO | off | OFF) return 1 ;;
  *) return 0 ;;
  esac
}

resolve_workspace_root() {
  local monitor_dir="$1"
  local d="${monitor_dir}"
  local i=0
  while test "${i}" -lt 5; do
    if test -x "${d}/.venv/bin/python3"; then
      printf '%s' "${d}"
      return 0
    fi
    d="$(cd "${d}/.." && pwd)"
    i=$((i + 1))
  done
  printf '%s' "$(cd "${monitor_dir}/../.." && pwd)"
}

resolve_python() {
  local ws="$1"
  if test -x "${ws}/.venv/bin/python3"; then
    printf '%s' "${ws}/.venv/bin/python3"
  else
    command -v python3
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") --profile <skx|clx|icx|spr|h100|pvc|amd-rtx> [options]

Validate DEBUG shm for one Stampede3 queue profile using the shared fleet
capability slug. Vista profiles (gg/gh) are rejected here (future path).

Options:
  --profile NAME     Required Stampede3 profile
  --caps PATH        monitor-build-capabilities.json (or set CAPS_JSON)
  --shm-dir PATH     DEBUG shm dir (default: HPCPERFSTATS_DEBUG_SHM_DIR or /dev/shm/...)
  --relax-profile-contract
  -h, --help
EOF
}

main() {
  local profile="" caps="" shm_dir="" relax=0
  while test "$#" -gt 0; do
    case "$1" in
    --profile)
      profile="${2:-}"
      shift 2
      ;;
    --caps)
      caps="${2:-}"
      shift 2
      ;;
    --shm-dir)
      shm_dir="${2:-}"
      shift 2
      ;;
    --relax-profile-contract)
      relax=1
      shift
      ;;
    -h | --help)
      usage
      return 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      return 2
      ;;
    esac
  done

  if test -z "${profile}"; then
    echo "ERROR: --profile is required" >&2
    usage >&2
    return 2
  fi

  case "${profile}" in
  skx | clx | icx | spr | h100 | pvc | amd-rtx) ;;
  gg | gh)
    echo "ERROR: profile ${profile} is Vista; use a future Vista validate wrapper / --system vista" >&2
    return 2
    ;;
  *)
    echo "ERROR: unknown Stampede3 profile: ${profile}" >&2
    return 2
    ;;
  esac

  local monitor_dir ws py build_dir enable_slow tier expectations report_dir slug
  monitor_dir="$(cd "${MONITOR_DIR}" && pwd)"
  ws="${WORKSPACE_ROOT:-$(resolve_workspace_root "${monitor_dir}")}"
  py="$(resolve_python "${ws}")"

  caps="${caps:-${CAPS_JSON:-}}"
  build_dir="${BUILD_DIR:-${monitor_dir}/.build-static}"
  if test -z "${caps}"; then
    caps="${build_dir}/monitor-build-capabilities.json"
  fi
  if test ! -f "${caps}"; then
    echo "ERROR: capabilities JSON not found: ${caps}" >&2
    echo "Emit with: make -C ${build_dir} capabilities  (or set CAPS_JSON=)" >&2
    return 1
  fi

  shm_dir="${shm_dir:-${HPCPERFSTATS_DEBUG_SHM_DIR:-/dev/shm/hpcperfstatsd-debug}}"
  enable_slow="${ENABLE_SLOW_TIER:-1}"
  case "${enable_slow}" in
  0 | false | FALSE | no | NO | off | OFF) enable_slow=0 ;;
  *) enable_slow=1 ;;
  esac

  slug="$("${py}" -c "import json; print(json.load(open('${caps}'))['capability_slug'])")"
  echo "capability_slug=${slug} profile=${profile}"

  expectations="${build_dir}/expectations_${slug}__${profile}.json"
  report_dir="${ws}/test_runs/monitor"
  mkdir -p "${report_dir}" "${build_dir}"

  echo "Building expectations ..."
  "${py}" "${monitor_dir}/scripts/build_message_expectations.py" \
    --capabilities "${caps}" \
    --shm-dir "${shm_dir}" \
    --enable-slow-tier "${enable_slow}" \
    --profile "${profile}" \
    --system stampede3 \
    --out "${expectations}"

  local validate_args=(
    --capabilities "${caps}"
    --manifest "${expectations}"
    --shm-dir "${shm_dir}"
    --profile "${profile}"
    --system stampede3
    --live-spot-check
    --report "${report_dir}/validate_${slug}__${profile}_$(date +%F).txt"
  )
  if test "${relax}" = "1" || test "${RELAX_PROFILE_CONTRACT:-0}" = "1"; then
    validate_args+=(--relax-profile-contract)
  fi
  if test -n "${WAIT_SHM_SECONDS:-}"; then
    validate_args+=(--wait-shm-seconds "${WAIT_SHM_SECONDS}")
  else
    validate_args+=(--wait-shm-seconds 30)
  fi
  if env_flag_on "${STRICT_LIVE_SPOT_CHECK:-1}"; then
    validate_args+=(--strict-live-spot-check)
  fi
  if env_flag_on "${STRICT_PLAUSIBILITY:-1}"; then
    validate_args+=(--strict-plausibility)
  fi
  if env_flag_on "${CROSS_SAMPLE_CHECK:-1}"; then
    validate_args+=(--cross-sample-check --cross-sample-wait-full)
    if env_flag_on "${STRICT_CROSS_SAMPLE:-1}"; then
      validate_args+=(--strict-cross-sample)
    fi
    if test -n "${HPCPERFSTATS_CONF:-}"; then
      validate_args+=(--conf "${HPCPERFSTATS_CONF}")
    fi
  fi
  if test "${GOLDEN_CHECK:-0}" = "1" || test -n "${GOLDEN_DIR:-}"; then
    resolved_golden="$(
      MONITOR_DIR="${monitor_dir}" SLUG="${slug}" PROFILE="${profile}" \
      ENABLE_SLOW="${enable_slow}" \
      GOLDEN_DIR="${GOLDEN_DIR:-}" GOLDEN_CHECK="${GOLDEN_CHECK:-0}" \
      "${py}" - <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["MONITOR_DIR"]) / "scripts"))
from lib.golden_diff import resolve_optin_golden_dir

slow = os.environ.get("ENABLE_SLOW", "1")
enable_slow = slow not in ("0", "false", "FALSE", "no", "NO", "off", "OFF")
path = resolve_optin_golden_dir(
    monitor_dir=Path(os.environ["MONITOR_DIR"]),
    slug=os.environ["SLUG"],
    golden_dir_env=os.environ.get("GOLDEN_DIR") or None,
    golden_check=os.environ.get("GOLDEN_CHECK", "0") == "1",
    enable_slow_tier=enable_slow,
    profile=os.environ.get("PROFILE") or None,
)
print(path if path is not None else "")
PY
    )"
    if test -n "${resolved_golden}"; then
      echo "Using golden dir: ${resolved_golden}"
      validate_args+=(--golden-dir "${resolved_golden}")
    else
      echo "WARN: golden opted in but no matching shm_*_${slug}__${profile} files"
    fi
  fi

  echo "Validating /dev/shm payloads (profile=${profile}) ..."
  "${py}" "${monitor_dir}/scripts/validate_shm_messages.py" "${validate_args[@]}"
  echo "PASS: validate_stampede3_profile (slug=${slug} profile=${profile})"
}

main "$@"
