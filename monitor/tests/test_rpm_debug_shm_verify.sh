#!/bin/sh
# Regression: rpm_debug_shm_verify.sh checks BUILD tree and emits capabilities first.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
verify="${ROOT}/scripts/rpm_debug_shm_verify.sh"

test -x "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must be executable" >&2; exit 1; }

err="$(RPM_TOPDIR=/tmp/hps_missing_rpmbuild DIST_TOP=hpcperfstats-3.0 MONITOR_DIR="${ROOT}" \
  SKIP_INSTALL=1 SKIP_DAEMON=1 SKIP_SHM_LS=1 bash "${verify}" 2>&1)" || true
echo "${err}" | grep -Fe 'RPM build tree missing' >/dev/null \
  || { echo "expected missing BUILD tree error, got: ${err}" >&2; exit 1; }

grep -q 'emit_build_capabilities.py' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must emit capabilities before expectations" >&2; exit 1; }

echo "test_rpm_debug_shm_verify passed"
