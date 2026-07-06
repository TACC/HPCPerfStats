#!/bin/sh
# Regression: print_debug_shm_verify.sh emits manifest + validate flags for RPM BUILD tree.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
helper="${ROOT}/scripts/lib/print_debug_shm_verify.sh"

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

echo "test_print_debug_shm_verify passed"
