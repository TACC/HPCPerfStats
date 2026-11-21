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
  SKIP_INSTALL=1 SKIP_SHM_LS=1 WAIT_FULL_SECONDS=0 bash "${verify}" 2>&1)" || true
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
  SKIP_INSTALL=1 SKIP_SHM_LS=1 WAIT_FULL_SECONDS=0 bash "${verify}" 2>&1)" || true
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

# The daemon deletes the previous run's fast/full at startup, so fast may not exist yet when
# the listing runs under errexit, and the freshness wait must cover one sample_freq of a
# production conf.
# shellcheck disable=SC2016  # literal $ text matched in script source
grep -q 'ls -la "\${shm_dir}"/{schema,fast,full} || true' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh shm listing must tolerate a missing fast" >&2; exit 1; }

grep -q '\-\-wait-shm-seconds 300' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default the shm freshness wait to 300 s" >&2; exit 1; }

grep -q 'FULL=' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must define FULL for post-install sleep" >&2; exit 1; }

# shellcheck disable=SC2016  # literal $ text matched in script source
grep -q 'POST_INSTALL_SLEEP_SECONDS:-\${FULL}' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default post-install sleep to FULL" >&2; exit 1; }

grep -q 'ensure_stress_ng' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must define ensure_stress_ng" >&2; exit 1; }

grep -q 'stress-ng --cpu 0' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must run stress-ng --cpu 0 for post-install soak" >&2; exit 1; }

# shellcheck disable=SC2016  # literal $ text matched in script source
grep -q '\-\-timeout "\${post_install_sleep}s"' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must timeout stress-ng with post_install_sleep" >&2; exit 1; }

grep -q 'dnf install -y stress-ng' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must dnf install stress-ng when missing" >&2; exit 1; }

if grep -E 'sleep "\$\{post_install_sleep\}"' "${verify}"; then
  echo "rpm_debug_shm_verify.sh must not bare-sleep post_install_sleep" >&2; exit 1
fi

grep -q 'hpcperfstatsd-\[0-9\]' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must install main hpcperfstatsd RPM only" >&2; exit 1; }

grep -q '\-\-replacepkgs \-\-replacefiles' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must reinstall with rpm --replacepkgs --replacefiles" >&2; exit 1; }

if grep -q 'systemctl restart' "${verify}"; then
  echo "rpm_debug_shm_verify.sh must not call systemctl restart (spec %post)" >&2; exit 1
fi

# perf_raw_flops reads <not counted> while the daemon holds the FP counters: stop before it,
# start and wait for a fresh full payload after it.
canary_order="$(awk '/^run_canary_checks\(\)/,/^}/' "${verify}" \
  | grep -o 'systemctl stop hpcperfstats.service\|perf_raw_flops_check.sh\|systemctl start hpcperfstats.service\|wait_full_payload' \
  | tr '\n' ' ')"
test "${canary_order}" = "systemctl stop hpcperfstats.service perf_raw_flops_check.sh systemctl start hpcperfstats.service wait_full_payload " \
  || { echo "perf_raw_flops must run between service stop and start+wait_full_payload: ${canary_order}" >&2; exit 1; }

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

grep -Eq 'field group creation failed|nvidia_gpu: source|stage=6' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must hint NVML/DCGM source and FieldGroupCreate on validate fail" >&2; exit 1; }

grep -qF "grep -qF \"type 'nvidia_gpu': no rows\"" "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must print the nvidia_gpu HINT only when the failure names nvidia_gpu" >&2; exit 1; }

grep -Eq 'Driver/stack probe|libdcgm' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must mention Driver/stack probe libdcgm on validate fail" >&2; exit 1; }

grep -Eq 'field group creation failed|stage=6|libdcgm' "${ROOT}/scripts/lib/device_validate.py" \
  || { echo "device_validate.py must hint field group / libdcgm for nvidia_gpu no rows" >&2; exit 1; }

grep -q 'STRICT_CROSS_SAMPLE:-1' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must default STRICT_CROSS_SAMPLE on" >&2; exit 1; }

grep -q 'resolve_optin_golden_dir' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must resolve golden dir when opted in" >&2; exit 1; }

grep -q 'GOLDEN_CHECK' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must support GOLDEN_CHECK opt-in" >&2; exit 1; }

grep -q 'GOLDEN_CHECK:-0' "${verify}" \
  || { echo "rpm_debug_shm_verify.sh must keep golden opt-in (GOLDEN_CHECK default 0)" >&2; exit 1; }

# --- Behavior (functions sourced; main only runs under the script's own name) ---
fake="${tmpdir}/fake"
mkdir -p "${fake}/bin" "${fake}/ws" "${fake}/shm" "${fake}/top/SPECS" "${fake}/top/RPMS/x86_64"
# shellcheck disable=SC1090
run_fn() { (PATH="${fake}/bin:/usr/bin:/bin"; . "${verify}"; "$@"); }

# EL8: python3 is 3.6, so a python3.N on PATH wins over python3 when there is no repo venv.
printf '#!/bin/sh\n' > "${fake}/bin/python3.9"
printf '#!/bin/sh\n' > "${fake}/bin/python3"
chmod +x "${fake}/bin/python3.9" "${fake}/bin/python3"
got="$(run_fn resolve_python "${fake}/ws")"
test "${got}" = "${fake}/bin/python3.9" \
  || { echo "resolve_python picked ${got}, want python3.9 before python3" >&2; exit 1; }

# Same-NVR rebuild: install passes --replacefiles (rpm refused the files otherwise, #77).
printf '#!/bin/sh\necho "rpm $*" > "%s/rpm.args"\n' "${fake}" > "${fake}/bin/rpm"
printf '#!/bin/sh\nexec "$@"\n' > "${fake}/bin/sudo"
chmod +x "${fake}/bin/rpm" "${fake}/bin/sudo"
touch "${fake}/top/RPMS/x86_64/hpcperfstatsd-3.1-1.el9.x86_64.rpm"
run_fn install_main_daemon_rpm "${fake}/top" >/dev/null
grep -q -- '--replacepkgs --replacefiles' "${fake}/rpm.args" \
  || { echo "install must pass --replacefiles: $(cat "${fake}/rpm.args")" >&2; exit 1; }

# RPM_BUILD=1: stale daemon RPMs are removed before rpmbuild, so a failed build installs none.
printf 'Name: x\n' > "${fake}/top/SPECS/hpcperfstats.spec"
printf '#!/bin/sh\nexit 1\n' > "${fake}/bin/rpmbuild"
chmod +x "${fake}/bin/rpmbuild"
if run_fn build_debug_rpm "${fake}/top" >/dev/null 2>&1; then
  echo "build_debug_rpm must fail when rpmbuild fails" >&2; exit 1
fi
test ! -e "${fake}/top/RPMS/x86_64/hpcperfstatsd-3.1-1.el9.x86_64.rpm" \
  || { echo "build_debug_rpm must remove stale daemon RPMs first" >&2; exit 1; }
printf '#!/bin/sh\necho "$*" > "%s/rpmbuild.args"\n' "${fake}" > "${fake}/bin/rpmbuild"
run_fn build_debug_rpm "${fake}/top" >/dev/null \
  || { echo "build_debug_rpm must succeed when rpmbuild does" >&2; exit 1; }
grep -q 'hpc_debug_build 1' "${fake}/rpmbuild.args" \
  || { echo "build_debug_rpm must define hpc_debug_build 1" >&2; exit 1; }

# Slow-tier restart: shm files exist before the first full sample; wait for a metric row.
printf '\n1795000000.0 - h1\n' > "${fake}/shm/full"
if WAIT_FULL_POLL_SECONDS=1 run_fn wait_full_payload "${fake}/shm" 2 >/dev/null 2>&1; then
  echo "wait_full_payload must fail when full has no metric rows" >&2; exit 1
fi
printf 'host_cpu - 1 2 3\n' >> "${fake}/shm/full"
run_fn wait_full_payload "${fake}/shm" 2 >/dev/null \
  || { echo "wait_full_payload must pass once full has a metric row" >&2; exit 1; }
run_fn wait_full_payload "${fake}/shm/missing" 0 \
  || { echo "wait_full_payload 0 must not wait" >&2; exit 1; }

echo "test_rpm_debug_shm_verify passed"
