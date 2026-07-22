#!/bin/sh
# Regression: rpm_debug_shm_verify.sh prefers debug-verify stash; BUILD optional.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
verify="${ROOT}/scripts/rpm_debug_shm_verify.sh"
spec="${ROOT}/hpcperfstats.spec"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"

test -x "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must be executable" >&2; exit 1; }

# Missing stash + missing BUILD → clear stash error (not sole BUILD-tree dependency).
err="$(RPM_TOPDIR=/tmp/hps_missing_rpmbuild DIST_TOP=hpcperfstats-3.0 MONITOR_DIR="${ROOT}" \
  SKIP_INSTALL=1 SKIP_SHM_LS=1 bash "${verify}" 2>&1)" || true
echo "${err}" | grep -Fe 'debug-verify stash missing' >/dev/null \
  || { echo "expected debug-verify stash missing error, got: ${err}" >&2; exit 1; }
echo "${err}" | grep -Fe 'RPM build tree missing' >/dev/null \
  && { echo "must not hard-require BUILD/.build-static: ${err}" >&2; exit 1; }

# Present stash + absent BUILD → past capabilities gate (Using stashed ...); may fail later on shm.
tmpdir="$(mktemp -d /tmp/hps_rpm_debug_verify.XXXXXX)"
cleanup() { rm -rf "${tmpdir}"; }
trap cleanup EXIT
mkdir -p "${tmpdir}/debug-verify" "${tmpdir}/RPMS/aarch64"
printf '%s\n' '{"capability_slug":"fixture-slug","tier":"slowtier1"}' \
  > "${tmpdir}/debug-verify/monitor-build-capabilities.json"
touch "${tmpdir}/RPMS/aarch64/hpcperfstatsd-3.0-6.el10.aarch64.rpm"
stash_out="$(RPM_TOPDIR="${tmpdir}" DIST_TOP=hpcperfstats-3.0 MONITOR_DIR="${ROOT}" \
  SKIP_INSTALL=1 SKIP_SHM_LS=1 bash "${verify}" 2>&1)" || true
echo "${stash_out}" | grep -Fe 'Using stashed capabilities' >/dev/null \
  || { echo "expected Using stashed capabilities with present stash, got: ${stash_out}" >&2; exit 1; }
echo "${stash_out}" | grep -Fe 'RPM build tree missing' >/dev/null \
  && { echo "stash path must not require BUILD tree: ${stash_out}" >&2; exit 1; }
echo "${stash_out}" | grep -Fe 'debug-verify stash missing' >/dev/null \
  && { echo "must not report stash missing when stash exists: ${stash_out}" >&2; exit 1; }

grep -q 'debug-verify' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must reference debug-verify stash" >&2; exit 1; }

grep -q 'ensure_capabilities_json' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must define ensure_capabilities_json" >&2; exit 1; }

grep -q '%{_topdir}/debug-verify' "${spec}" \
  || { echo "hpcperfstats.spec must stash caps under %{_topdir}/debug-verify" >&2; exit 1; }

grep -q 'debug-verify' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must document debug-verify stash" >&2; exit 1; }

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

grep -q 'ensure_stress_ng' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must define ensure_stress_ng" >&2; exit 1; }

grep -q 'stress-ng --cpu 0' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must run stress-ng --cpu 0 for post-install soak" >&2; exit 1; }

grep -q '\-\-timeout "\${post_install_sleep}s"' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must timeout stress-ng with post_install_sleep" >&2; exit 1; }

grep -q 'dnf install -y stress-ng' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must dnf install stress-ng when missing" >&2; exit 1; }

grep -E 'sleep "\$\{post_install_sleep\}"' "${verify}" \
  && { echo "rpm_debug_shm_verify.sh must not bare-sleep post_install_sleep" >&2; exit 1; } \
  || true

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

grep -q 'is-active hpcperfstats.service' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must require active hpcperfstats.service after install" >&2; exit 1; }

grep -q 'refusing shm validate' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must refuse validate when service not active" >&2; exit 1; }

grep -q 'STRICT_CROSS_SAMPLE:-1' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default STRICT_CROSS_SAMPLE on" >&2; exit 1; }

grep -q 'resolve_optin_golden_dir' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must resolve golden dir when opted in" >&2; exit 1; }

grep -q 'GOLDEN_CHECK' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must support GOLDEN_CHECK opt-in" >&2; exit 1; }

grep -q 'GOLDEN_CHECK:-0' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must keep golden opt-in (GOLDEN_CHECK default 0)" >&2; exit 1; }

echo "test_rpm_debug_shm_verify passed"
