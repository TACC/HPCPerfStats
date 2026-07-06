#!/bin/sh
# Regression: rpm_debug_shm_verify.sh checks BUILD tree, main-RPM install, emits capabilities.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
verify="${ROOT}/scripts/rpm_debug_shm_verify.sh"

test -x "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must be executable" >&2; exit 1; }

err="$(RPM_TOPDIR=/tmp/hps_missing_rpmbuild DIST_TOP=hpcperfstats-3.0 MONITOR_DIR="${ROOT}" \
  SKIP_INSTALL=1 SKIP_SHM_LS=1 bash "${verify}" 2>&1)" || true
echo "${err}" | grep -Fe 'RPM build tree missing' >/dev/null \
  || { echo "expected missing BUILD tree error, got: ${err}" >&2; exit 1; }

grep -q 'emit_build_capabilities.py' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must emit capabilities before expectations" >&2; exit 1; }

grep -q 'hpcperfstatsd-\[0-9\]' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must install main hpcperfstatsd RPM only" >&2; exit 1; }

grep -q 'rpm -q hpcperfstatsd' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must tolerate already-installed main RPM" >&2; exit 1; }

grep -q 'systemctl restart' "${verify}" \
  && { echo "rpm_debug_shm_verify.sh must not call systemctl restart (spec %post)" >&2; exit 1; } \
  || true

grep -q 'RPM %post' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must not restart daemon (spec %post handles it)" >&2; exit 1; }

grep -q 'resolve_dist_top' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default DIST_TOP from spec/configure" >&2; exit 1; }

echo "test_rpm_debug_shm_verify passed"
