#!/bin/sh
# Regression: print_debug_shm_verify.sh emits manifest + validate flags for RPM BUILD tree.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
helper="${ROOT}/scripts/lib/print_debug_shm_verify.sh"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"

test -x "${helper}" \
  || { echo "print_debug_shm_verify.sh must be executable" >&2; exit 1; }

out="$(RPM_TOPDIR=/tmp/hps_rpmbuild DIST_TOP=hpcperfstats-3.0 MONITOR_DIR="${ROOT}" \
  bash "${helper}")"

for needle in \
  'build_message_expectations.py' \
  'validate_shm_messages.py' \
  '--capabilities' \
  '--manifest' \
  '--shm-dir' \
  '--enable-slow-tier' \
  '--report' \
  'monitor-build-capabilities.json' \
  '.build-static' \
  'expectations_${SLUG}.json' \
  'test_runs/monitor/validate_rpm_debug_'
do
  echo "${out}" | grep -Fe "${needle}" >/dev/null \
    || { echo "print_debug_shm_verify.sh output missing: ${needle}" >&2; exit 1; }
done

grep -q 'env RPM_TOPDIR=' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must invoke print_debug_shm_verify via env" >&2; exit 1; }

# readonly MONITOR_DIR in prepare is not exported; env must pass it to the helper.
out_readonly="$(bash -c 'readonly MONITOR_DIR="'"${ROOT}"'"; env MONITOR_DIR="'"${ROOT}"'" RPM_TOPDIR=/tmp/hps_rpmbuild DIST_TOP=hpcperfstats-3.0 bash "'"${helper}"'"')"
echo "${out_readonly}" | grep -Fe "${ROOT}" >/dev/null \
  || { echo "print_debug_shm_verify must receive MONITOR_DIR via env" >&2; exit 1; }

echo "test_print_debug_shm_verify passed"
