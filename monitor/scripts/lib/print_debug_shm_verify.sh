#!/usr/bin/env bash
# Print copy-paste /dev/shm verification commands after debug RPM (hpc_debug_build) rpmbuild.
# Invoked from prepare_rpmbuild_dirs.sh --debug-build.
#
# Required environment:
#   RPM_TOPDIR   rpmbuild _topdir (e.g. monitor/rpmbuild)
#   DIST_TOP     unpacked source dir name (e.g. hpcperfstats-3.0)
#   MONITOR_DIR  HPCPerfStats/monitor checkout
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
  local ws py

  ws="$(resolve_workspace_root "${monitor_dir}")"
  py="$(resolve_python "${ws}")"

  cat <<EOF

=== DEBUG /dev/shm verification (after debug rpmbuild completes) ===

Prerequisite: run the debug rpmbuild line printed above (hpc_debug_build 1).
prepare does not run rpmbuild, install, or start the daemon.

Copy/paste the block below after rpmbuild succeeds, the debug RPM is installed
(or use the foreground daemon path), and hpcperfstatsd has written shm payloads.

# --- begin copy/paste ---
set -euo pipefail

RPM_TOPDIR=${rpm_topdir@Q}
DIST_TOP=${dist_top@Q}
MONITOR_DIR=${monitor_dir@Q}
WORKSPACE_ROOT=${ws@Q}
PY=${py@Q}

BUILD_SRC="\${RPM_TOPDIR}/BUILD/\${DIST_TOP}"
BUILD_STATIC="\${BUILD_SRC}/.build-static"
CAPS="\${BUILD_STATIC}/monitor-build-capabilities.json"
SHM_DIR="\${HPCPERFSTATS_DEBUG_SHM_DIR:-/dev/shm/hpcperfstatsd-debug}"
# Match CAPABILITIES_TIER / --enable-slow-tier to enable_slow_tier in hpcperfstats.conf
ENABLE_SLOW_TIER="\${ENABLE_SLOW_TIER:-1}"

# Install debug RPM (skip if testing uninstalled build tree binary)
sudo rpm -Uvh "\${RPM_TOPDIR}"/RPMS/*/"hpcperfstatsd-"*.rpm
sudo systemctl restart hpcperfstatsd
# Foreground alternative (no install):
# sudo systemctl stop hpcperfstatsd 2>/dev/null || true
# "\${BUILD_STATIC}/src/hpcperfstatsd" -f /etc/hpcperfstats/hpcperfstats.conf

ls -la "\${SHM_DIR}"/{schema,fast,full}

make -C "\${BUILD_STATIC}" capabilities

SLUG="\$("\${PY}" -c "import json; print(json.load(open('\${CAPS}'))['capability_slug'])")"
echo "capability_slug=\${SLUG}"

"\${PY}" "\${MONITOR_DIR}/scripts/build_message_expectations.py" \\
  --capabilities "\${CAPS}" \\
  --shm-dir "\${SHM_DIR}" \\
  --enable-slow-tier "\${ENABLE_SLOW_TIER}" \\
  --out "\${BUILD_STATIC}/expectations_\${SLUG}.json"

"\${PY}" "\${MONITOR_DIR}/scripts/validate_shm_messages.py" \\
  --capabilities "\${CAPS}" \\
  --manifest "\${BUILD_STATIC}/expectations_\${SLUG}.json" \\
  --shm-dir "\${SHM_DIR}" \\
  --report "\${WORKSPACE_ROOT}/test_runs/monitor/validate_rpm_debug_\${SLUG}_\$(date +%F).txt"

echo "PASS: validate_shm_messages exit \$? (expect 0; slug mismatch is exit 2)"
# --- end copy/paste ---

Pass criteria: validate_shm_messages.py exits 0; report shows PASS schema, fast, full
(when enable_slow_tier 1). Override shm base: export HPCPERFSTATS_DEBUG_SHM_DIR=...
EOF
}

if test "$(basename "$0")" = "print_debug_shm_verify.sh"; then
  print_debug_shm_verify_runbook
fi
