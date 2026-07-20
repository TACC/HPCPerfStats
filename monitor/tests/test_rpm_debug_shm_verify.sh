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

grep -q 'CROSS_SAMPLE_CHECK' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must honor CROSS_SAMPLE_CHECK" >&2; exit 1; }

grep -q '\-\-cross-sample-check' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must pass --cross-sample-check" >&2; exit 1; }

grep -q 'CROSS_SAMPLE_CHECK:-1' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default CROSS_SAMPLE_CHECK on" >&2; exit 1; }

grep -q 'STRICT_LIVE_SPOT_CHECK:-1' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default STRICT_LIVE_SPOT_CHECK on" >&2; exit 1; }

grep -q 'STRICT_PLAUSIBILITY:-1' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default STRICT_PLAUSIBILITY on" >&2; exit 1; }

grep -q 'STRICT_CROSS_SAMPLE:-1' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default STRICT_CROSS_SAMPLE on" >&2; exit 1; }

grep -q 'resolve_optin_golden_dir' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must resolve golden dir when opted in" >&2; exit 1; }

grep -q 'GOLDEN_CHECK' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must support GOLDEN_CHECK opt-in" >&2; exit 1; }

# Golden must remain opt-in: do not auto-pass --golden-dir without GOLDEN_* env.
if grep -qE 'validate_args\+\=\(--golden-dir' "${verify}"; then
  # Only allowed inside GOLDEN_CHECK / GOLDEN_DIR gated block (resolve path).
  :
fi
grep -q 'GOLDEN_CHECK:-0' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must keep golden opt-in (GOLDEN_CHECK default 0)" >&2; exit 1; }

echo "test_rpm_debug_shm_verify passed"
