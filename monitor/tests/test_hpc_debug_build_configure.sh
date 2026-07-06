#!/bin/sh
# Regression: hpc_debug_build RPM path enables --enable-debug; release path unchanged.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

spec="${ROOT}/hpcperfstats.spec"
bundle="${ROOT}/scripts/build_static_bundle.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"

# Debug RPM: spec exports HPC_BUNDLE_ENABLE_DEBUG inside hpc_debug_build block only.
awk '/%if 0%\{\?hpc_debug_build\}/,/%else/' "${spec}" \
  | grep -q 'HPC_BUNDLE_ENABLE_DEBUG=1' \
  || { echo "hpcperfstats.spec must export HPC_BUNDLE_ENABLE_DEBUG=1 in hpc_debug_build block" >&2; exit 1; }

# Release RPM: default %build must not set HPC_BUNDLE_ENABLE_DEBUG.
awk '/%else/,/%endif/' "${spec}" | head -20 | grep -q 'HPC_BUNDLE_RELEASE_BUILD=1' \
  || { echo "hpcperfstats.spec release block must set HPC_BUNDLE_RELEASE_BUILD=1" >&2; exit 1; }
if awk '/%else/,/%endif/' "${spec}" | head -20 | grep -q 'HPC_BUNDLE_ENABLE_DEBUG'; then
  echo "release spec block must not set HPC_BUNDLE_ENABLE_DEBUG" >&2
  exit 1
fi

grep -q 'static_bundle_enable_debug_enabled' "${bundle}" \
  || { echo "build_static_bundle.sh must define static_bundle_enable_debug_enabled" >&2; exit 1; }
grep -q 'HPC_BUNDLE_ENABLE_DEBUG' "${bundle}" \
  || { echo "build_static_bundle.sh must document HPC_BUNDLE_ENABLE_DEBUG" >&2; exit 1; }
grep -q '\-\-enable-debug' "${bundle}" \
  || { echo "build_static_bundle.sh must pass --enable-debug when HPC_BUNDLE_ENABLE_DEBUG is set" >&2; exit 1; }
grep -q '\-\-disable-debug' "${bundle}" \
  || { echo "build_static_bundle.sh must still pass --disable-debug for release builds" >&2; exit 1; }

grep -q 'hpc_debug_build' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must mention hpc_debug_build in final instructions" >&2; exit 1; }

grep -q 'print_debug_shm_verify' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must invoke print_debug_shm_verify for debug runbook" >&2; exit 1; }

verify_lib="${ROOT}/scripts/lib/print_debug_shm_verify.sh"
test -f "${verify_lib}" \
  || { echo "missing ${verify_lib}" >&2; exit 1; }
grep -q 'validate_shm_messages.py' "${verify_lib}" \
  || { echo "print_debug_shm_verify.sh must reference validate_shm_messages.py" >&2; exit 1; }
grep -q 'build_message_expectations.py' "${verify_lib}" \
  || { echo "print_debug_shm_verify.sh must reference build_message_expectations.py" >&2; exit 1; }

echo "test_hpc_debug_build_configure passed"
