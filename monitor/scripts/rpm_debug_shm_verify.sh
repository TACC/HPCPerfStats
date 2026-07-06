#!/usr/bin/env bash
# Post-debug-rpmbuild /dev/shm verification (capabilities + expectations + validate).
# Invoked directly or via scripts/lib/print_debug_shm_verify.sh runbook.
#
# Required:
#   RPM_TOPDIR   rpmbuild _topdir (e.g. monitor/rpmbuild)
#   DIST_TOP     unpacked source dir name (e.g. hpcperfstats-3.0)
#
# Optional:
#   MONITOR_DIR          monitor checkout (default: parent of scripts/)
#   WORKSPACE_ROOT       report parent (default: walk up for .venv)
#   HPCPERFSTATS_DEBUG_SHM_DIR  shm base (default: /dev/shm/hpcperfstatsd-debug)
#   ENABLE_SLOW_TIER     1=slowtier1 (default), 0=slowtier0
#   SKIP_INSTALL=1       skip rpm -Uvh
#   SKIP_DAEMON=1        skip systemctl restart
#   SKIP_SHM_LS=1        skip ls of schema/fast/full
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MONITOR_DIR="${MONITOR_DIR:-${SCRIPT_DIR}/..}"

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
  printf '%s' "$(cd "${monitor_dir}/.." && pwd)"
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
Usage: $(basename "$0")

Environment:
  RPM_TOPDIR, DIST_TOP (required)
  MONITOR_DIR, WORKSPACE_ROOT, ENABLE_SLOW_TIER, HPCPERFSTATS_DEBUG_SHM_DIR
  SKIP_INSTALL=1, SKIP_DAEMON=1, SKIP_SHM_LS=1

Run after debug rpmbuild (hpc_debug_build 1) succeeds.
EOF
}

main() {
  if test "${1:-}" = "-h" || test "${1:-}" = "--help"; then
    usage
    return 0
  fi

  local rpm_topdir="${RPM_TOPDIR:?RPM_TOPDIR required}"
  local dist_top="${DIST_TOP:?DIST_TOP required}"
  local monitor_dir ws py build_src build_static caps shm_dir tier enable_slow
  local slug expectations report_dir

  monitor_dir="$(cd "${MONITOR_DIR}" && pwd)"
  ws="${WORKSPACE_ROOT:-$(resolve_workspace_root "${monitor_dir}")}"
  py="$(resolve_python "${ws}")"

  build_src="${rpm_topdir}/BUILD/${dist_top}"
  build_static="${build_src}/.build-static"
  caps="${build_static}/monitor-build-capabilities.json"
  shm_dir="${HPCPERFSTATS_DEBUG_SHM_DIR:-/dev/shm/hpcperfstatsd-debug}"
  enable_slow="${ENABLE_SLOW_TIER:-1}"
  case "${enable_slow}" in
  0 | false | FALSE | no | NO | off | OFF) tier="slowtier0" ;;
  *) tier="slowtier1" ;;
  esac

  if test ! -d "${build_static}"; then
    echo "ERROR: RPM build tree missing: ${build_static}" >&2
    echo "Run debug rpmbuild first (prepare_rpmbuild_dirs.sh --debug-build prints the command)." >&2
    return 1
  fi
  if test ! -f "${build_static}/src/hpcperfstatsd"; then
    echo "ERROR: debug daemon not built: ${build_static}/src/hpcperfstatsd" >&2
    echo "Complete debug rpmbuild (hpc_debug_build 1) before verification." >&2
    return 1
  fi

  if test "${SKIP_INSTALL:-0}" != "1"; then
    sudo rpm -Uvh "${rpm_topdir}"/RPMS/*/"hpcperfstatsd-"*.rpm
  fi
  if test "${SKIP_DAEMON:-0}" != "1"; then
    sudo systemctl restart hpcperfstatsd
  fi

  echo "Emitting ${caps}"
  CAPABILITIES_TIER="${tier}" "${py}" "${monitor_dir}/scripts/emit_build_capabilities.py" \
    --build-dir "${build_static}" \
    --tier "${tier}"
  test -f "${caps}" || {
    echo "ERROR: failed to write ${caps}" >&2
    return 1
  }

  slug="$("${py}" -c "import json; print(json.load(open('${caps}'))['capability_slug'])")"
  echo "capability_slug=${slug}"

  if test "${SKIP_SHM_LS:-0}" != "1"; then
    ls -la "${shm_dir}"/{schema,fast,full}
  fi

  expectations="${build_static}/expectations_${slug}.json"
  report_dir="${ws}/test_runs/monitor"
  mkdir -p "${report_dir}"

  "${py}" "${monitor_dir}/scripts/build_message_expectations.py" \
    --capabilities "${caps}" \
    --shm-dir "${shm_dir}" \
    --enable-slow-tier "${enable_slow}" \
    --out "${expectations}"

  "${py}" "${monitor_dir}/scripts/validate_shm_messages.py" \
    --capabilities "${caps}" \
    --manifest "${expectations}" \
    --shm-dir "${shm_dir}" \
    --report "${report_dir}/validate_rpm_debug_${slug}_$(date +%F).txt"

  echo "PASS: validate_shm_messages completed (slug mismatch is exit 2)"
}

if test "$(basename "$0")" = "rpm_debug_shm_verify.sh"; then
  main "$@"
fi
