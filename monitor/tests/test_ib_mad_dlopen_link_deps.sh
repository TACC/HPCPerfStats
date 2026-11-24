#!/bin/bash
# Regression: IB_MAD_DLOPEN test programs must link dyn_lib_path.c (horizon fleet).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MF="${ROOT}/tests/Makefile.am"

test -f "${MF}" || {
  echo "missing ${MF}" >&2
  exit 1
}

# Each ib_mad_* SOURCES += under IB_MAD_DLOPEN must include dyn_lib_path.c
block="$(awk '/^if IB_MAD_DLOPEN$/,/^else$/' "${MF}")"
grep -q 'test_ib_mad_backoff_SOURCES += .*dyn_lib_path\.c' <<<"${block}" \
  || {
    echo "test_ib_mad_backoff_SOURCES must add dyn_lib_path.c under IB_MAD_DLOPEN" >&2
    exit 1
  }
grep -q 'test_ib_mad_sw_swap_SOURCES += .*dyn_lib_path\.c' <<<"${block}" \
  || {
    echo "test_ib_mad_sw_swap_SOURCES must add dyn_lib_path.c under IB_MAD_DLOPEN" >&2
    exit 1
  }

# DEBUG shm unit tests need secure_path for ensure_private_dir
for prog in test_stats_buffer_debug_shm_SOURCES test_debug_shm_emit_golden_SOURCES \
  test_debug_shm_schema_mirror_SOURCES; do
  if ! awk -v p="${prog}" '
    index($0, p " =") == 1 {grab=1}
    grab {print}
    grab && NF && $0 !~ /^[\t ]/ && index($0, p " =") != 1 {exit}
  ' "${MF}" | grep -q 'secure_path\.c'; then
    echo "${prog} must include secure_path.c" >&2
    exit 1
  fi
done

echo "test_ib_mad_dlopen_link_deps passed"
