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

grep -q 'validate_shm_messages.py' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must run validate_shm_messages.py" >&2; exit 1; }

grep -q '\-\-live-spot-check' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must pass --live-spot-check for live shm" >&2; exit 1; }

grep -q '\-\-wait-shm-seconds' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must wait for shm payloads" >&2; exit 1; }

grep -q 'FULL=' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must define FULL for post-install sleep" >&2; exit 1; }

grep -q 'POST_INSTALL_SLEEP_SECONDS:-\${FULL}' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default post-install sleep to FULL" >&2; exit 1; }

grep -q 'hpcperfstatsd-\[0-9\]' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must install main hpcperfstatsd RPM only" >&2; exit 1; }

grep -q '\-\-replacepkgs' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must reinstall with rpm --replacepkgs" >&2; exit 1; }

grep -q 'systemctl restart' "${verify}" \
  && { echo "rpm_debug_shm_verify.sh must not call systemctl restart (spec %post)" >&2; exit 1; } \
  || true

grep -q 'RPM %post' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must not restart daemon (spec %post handles it)" >&2; exit 1; }

grep -q 'resolve_dist_top' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default DIST_TOP from spec/configure" >&2; exit 1; }

echo "test_rpm_debug_shm_verify passed"
