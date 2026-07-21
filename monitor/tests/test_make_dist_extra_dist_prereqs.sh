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
  scripts/lib/papi_shm_validate.py \
  scripts/lib/live_spot_check.py \
  scripts/lib/golden_diff.py \
  scripts/lib/tacc_system_profiles.py \
  scripts/validate_stampede3_profile.sh \
  scripts/lib/daemon_conf.py \
  scripts/lib/shm_snapshot.py \
  scripts/lib/cross_sample_validate.py \
  scripts/lib/cross_sample_stimulus.py \
  scripts/rpm_debug_shm_verify.sh \
  scripts/gpu_lspci_probe.sh \
  scripts/gpu_lspci_detect.awk \
  scripts/run_valgrind_check.sh \
  scripts/valgrind.supp \
  scripts/run_cpp_linter.sh
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

grep -q 'scripts/lib/cross_sample_validate.py' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/lib/cross_sample_validate.py" >&2; exit 1; }
grep -q 'scripts/lib/cross_sample_stimulus.py' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/lib/cross_sample_stimulus.py" >&2; exit 1; }

grep -q 'scripts/lib/tacc_system_profiles.py' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/lib/tacc_system_profiles.py" >&2; exit 1; }
grep -q 'scripts/validate_stampede3_profile.sh' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/validate_stampede3_profile.sh" >&2; exit 1; }

grep -q 'scripts/gpu_lspci_probe.sh' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight scripts/gpu_lspci_probe.sh" >&2; exit 1; }

for script in scripts/gpu_lspci_probe.sh scripts/gpu_lspci_detect.awk \
  scripts/run_valgrind_check.sh scripts/valgrind.supp scripts/run_cpp_linter.sh; do
  grep -q "${script}" src/Makefile.am \
    || { echo "src/Makefile.am EXTRA_DIST must list ${script}" >&2; exit 1; }
done

grep -q 'verify_dist_tarball_build_scripts' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must verify gpu_lspci scripts in dist tarball" >&2; exit 1; }

for hdr in host_cpu.h host_mem.h host_net.h host_ps.h; do
  test -f "src/${hdr}" \
    || { echo "missing src/${hdr} (required for RPM build)" >&2; exit 1; }
  grep -q "${hdr}" src/Makefile.am \
    || { echo "src/Makefile.am must list ${hdr} in hpcperfstatsd_SOURCES" >&2; exit 1; }
done

grep -q 'verify_dist_tarball_host_headers' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must verify host_*.h in dist tarball" >&2; exit 1; }

# Regression: doubled extensions break make dist (e.g. test_foo.c.c → no rule).
if grep -nE '[[:alnum:]_.-]+\.c\.c([[:space:]]|$)' tests/Makefile.am src/Makefile.am; then
  echo "FAIL: Makefile.am EXTRA_DIST/SOURCES must not list *.c.c paths" >&2
  exit 1
fi
test -f tests/test_arm_aarch64_imc_schema.c \
  || { echo "missing tests/test_arm_aarch64_imc_schema.c" >&2; exit 1; }
grep -q 'test_arm_aarch64_imc_schema\.c\.c' tests/Makefile.am \
  && { echo "FAIL: test_arm_aarch64_imc_schema.c.c still in tests/Makefile.am" >&2; exit 1; }
grep -qE '(^|[[:space:]])test_arm_aarch64_imc_schema\.c([[:space:]]|$)' tests/Makefile.am \
  || { echo "FAIL: tests/Makefile.am must list test_arm_aarch64_imc_schema.c" >&2; exit 1; }

echo "test_make_dist_extra_dist_prereqs passed"
