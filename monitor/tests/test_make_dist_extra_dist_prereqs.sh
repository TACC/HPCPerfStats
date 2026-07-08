#!/bin/sh
# Regression: EXTRA_DIST / make dist prerequisites exist and prepare preflight covers them.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

for path in \
  tests/scripts/bootstrap_local_rabbitmq.sh \
  scripts/check_unsafe_c_patterns.sh \
  scripts/check_unsafe_c_patterns.allowlist \
  scripts/check_emitted_variable_names.py \
  scripts/emit_build_capabilities.py \
  scripts/build_message_expectations.py \
  scripts/validate_shm_messages.py \
  scripts/lib/__init__.py \
  scripts/lib/message_parse.py \
  scripts/lib/row_validate.py \
  scripts/lib/payload_parse.py \
  scripts/lib/device_validate.py \
  scripts/lib/listend_contract.py \
  scripts/lib/host_live_probes.py \
  scripts/lib/value_plausibility.py \
  scripts/lib/live_spot_check.py \
  scripts/lib/golden_diff.py \
  scripts/lib/daemon_conf.py \
  scripts/lib/shm_snapshot.py \
  scripts/lib/cross_sample_validate.py \
  scripts/rpm_debug_shm_verify.sh \
  scripts/gpu_lspci_probe.sh \
  scripts/gpu_lspci_detect.awk
do
  test -f "${path}" \
    || { echo "missing ${path} (required for make dist)" >&2; exit 1; }
done

test -x tests/scripts/bootstrap_local_rabbitmq.sh \
  || { echo "tests/scripts/bootstrap_local_rabbitmq.sh must be executable" >&2; exit 1; }

grep -q 'tests/scripts/bootstrap_local_rabbitmq.sh' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight tests/scripts/bootstrap_local_rabbitmq.sh" >&2; exit 1; }
grep -q 'scripts/lib/message_parse.py' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/lib/message_parse.py" >&2; exit 1; }
grep -q 'scripts/rpm_debug_shm_verify.sh' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/rpm_debug_shm_verify.sh" >&2; exit 1; }

for hdr in host_cpu.h host_mem.h host_net.h host_ps.h; do
  test -f "src/${hdr}" \
    || { echo "missing src/${hdr} (required for RPM build)" >&2; exit 1; }
  grep -q "${hdr}" src/Makefile.am \
    || { echo "src/Makefile.am must list ${hdr} in hpcperfstatsd_SOURCES" >&2; exit 1; }
done

grep -q 'verify_dist_tarball_host_headers' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must verify host_*.h in dist tarball" >&2; exit 1; }

echo "test_make_dist_extra_dist_prereqs passed"
