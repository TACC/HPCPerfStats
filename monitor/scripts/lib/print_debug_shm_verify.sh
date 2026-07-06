#!/usr/bin/env bash
# Print copy-paste /dev/shm verification commands after debug RPM (hpc_debug_build) rpmbuild.
# Invoked from prepare_rpmbuild_dirs.sh --debug-build.
#
# Required environment:
#   RPM_TOPDIR   rpmbuild _topdir (e.g. monitor/rpmbuild)
#   DIST_TOP     unpacked source dir name (e.g. hpcperfstats-3.0)
#   MONITOR_DIR  HPCPerfStats/monitor checkout (pass via env from prepare)
#
set -euo pipefail

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

print_debug_shm_verify_runbook() {
  local rpm_topdir="${RPM_TOPDIR:?RPM_TOPDIR required}"
  local dist_top="${DIST_TOP:?DIST_TOP required}"
  local monitor_dir="${MONITOR_DIR:?MONITOR_DIR required}"
  local ws py verify_script

  ws="$(resolve_workspace_root "${monitor_dir}")"
  py="$(resolve_python "${ws}")"
  verify_script="${monitor_dir}/scripts/rpm_debug_shm_verify.sh"

  cat <<EOF

=== DEBUG /dev/shm verification (after debug rpmbuild completes) ===

Prerequisite: run the debug rpmbuild line printed above (hpc_debug_build 1).
prepare does not run rpmbuild, install, or start the daemon.

After rpmbuild succeeds, run the wrapper (installs debug RPM, restarts daemon,
emits monitor-build-capabilities.json, then validates shm):

# --- begin copy/paste ---
export RPM_TOPDIR=${rpm_topdir@Q}
export DIST_TOP=${dist_top@Q}
export MONITOR_DIR=${monitor_dir@Q}
export WORKSPACE_ROOT=${ws@Q}
# Match enable_slow_tier in /etc/hpcperfstats/hpcperfstats.conf (default 1):
export ENABLE_SLOW_TIER="\${ENABLE_SLOW_TIER:-1}"

${verify_script@Q}
# --- end copy/paste ---

Optional: already installed / daemon running:
  SKIP_INSTALL=1 SKIP_DAEMON=1 ${verify_script@Q}

Foreground daemon instead of systemctl (after SKIP_INSTALL=1):
  sudo systemctl stop hpcperfstatsd 2>/dev/null || true
  "\${RPM_TOPDIR}/BUILD/\${DIST_TOP}/.build-static/src/hpcperfstatsd" -f /etc/hpcperfstats/hpcperfstats.conf

Manual steps (if not using the wrapper): emit capabilities before expectations:
  ${py@Q} ${monitor_dir@Q}/scripts/emit_build_capabilities.py \\
    --build-dir "\${RPM_TOPDIR}/BUILD/\${DIST_TOP}/.build-static" \\
    --tier slowtier1

Pass criteria: validate_shm_messages.py exits 0; report under
${ws@Q}/test_runs/monitor/validate_rpm_debug_<slug>_*.txt shows PASS schema, fast, full
(when enable_slow_tier 1). Override shm base: export HPCPERFSTATS_DEBUG_SHM_DIR=...
EOF
}

if test "$(basename "$0")" = "print_debug_shm_verify.sh"; then
  print_debug_shm_verify_runbook
fi
