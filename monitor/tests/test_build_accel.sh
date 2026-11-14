#!/bin/sh
# shellcheck shell=bash disable=SC2016 # single-quoted $ strings are literal on purpose
# Contract: scripts/lib/build_accel.sh turns on ccache / mold only when they exist and
# work, and leaves CC / LDFLAGS untouched otherwise (builds must not need either tool).
# Uses fake ccache / mold so the result does not depend on what the host has installed.
# Exit 77 (skip) when no C compiler is available.
if test -z "${BASH_VERSION:-}"; then
  exec bash "$0" "$@"
fi
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="${ROOT}/scripts/lib/build_accel.sh"
REAL_CC="${TEST_CC:-cc}"
command -v "${REAL_CC}" >/dev/null 2>&1 || { echo "skip: no C compiler (${REAL_CC})"; exit 77; }
REAL_LD="$(command -v ld.bfd || command -v ld)" || { echo "skip: no ld"; exit 77; }

fail() {
  echo "test_build_accel: FAIL: $*" >&2
  exit 1
}

# Wiring contracts: both build scripts source and call the helper; dist ships it.
for s in build_static_bundle.sh run_valgrind_check.sh; do
  grep -q 'source "${SCRIPT_DIR}/lib/build_accel.sh"' "${ROOT}/scripts/${s}" ||
    fail "${s} must source lib/build_accel.sh"
  grep -q '^ *build_accel_apply' "${ROOT}/scripts/${s}" || fail "${s} must call build_accel_apply"
done
grep -q 'scripts/lib/build_accel.sh' "${ROOT}/tests/Makefile.am" ||
  fail "tests/Makefile.am EXTRA_DIST must ship scripts/lib/build_accel.sh (RPM %build sources it)"
# Exports must not be lost in a pipeline subshell.
if grep -Eq 'build_accel_apply[^#]*\|' "${ROOT}/scripts/run_valgrind_check.sh" "${ROOT}/scripts/build_static_bundle.sh"; then
  fail "build_accel_apply must not run in a pipeline (subshell drops exports)"
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/test_build_accel.XXXXXX")"
trap 'rm -rf "${tmp}"' EXIT

# Fake ccache that works (runs the compiler) and one that always fails.
mkdir -p "${tmp}/good" "${tmp}/bad"
printf '#!/bin/sh\nexec "$@"\n' >"${tmp}/good/ccache"
printf '#!/bin/sh\nexit 1\n' >"${tmp}/bad/ccache"
# Fake mold install: bin/mold plus libexec/mold/ld (a real linker, so the probe links).
mkdir -p "${tmp}/mold/bin" "${tmp}/mold/libexec/mold" "${tmp}/brokenmold/bin"
printf '#!/bin/sh\nexit 0\n' >"${tmp}/mold/bin/mold"
ln -s "${REAL_LD}" "${tmp}/mold/libexec/mold/ld"
printf '#!/bin/sh\nexit 0\n' >"${tmp}/brokenmold/bin/mold" # no ld shim next to it
chmod +x "${tmp}"/good/ccache "${tmp}"/bad/ccache "${tmp}"/mold/bin/mold "${tmp}"/brokenmold/bin/mold
MOLD_DIR="$(cd "${tmp}/mold/libexec/mold" && pwd)"

# run_case VAR=VALUE... : source the helper in a clean shell, print "CC|LDFLAGS".
run_case() {
  env -i PATH="${PATH}" TMPDIR="${TMPDIR:-/tmp}" CC="${REAL_CC}" LDFLAGS="-L/base" \
    HPC_BUNDLE_CCACHE_BIN=/nonexistent/ccache HPC_BUNDLE_MOLD_BIN=/nonexistent/mold "$@" \
    bash -c '. "$1"; build_accel_apply 2>/dev/null; printf "%s|%s\n" "${CC}" "${LDFLAGS}"' _ "${HELPER}"
}

expect() {
  local name="$1" want="$2" got
  shift 2
  got="$(run_case "$@")"
  test "${got}" = "${want}" || fail "${name}: want '${want}' got '${got}'"
  echo "ok: ${name}"
}

base="${REAL_CC}|-L/base"
expect "neither tool installed -> unchanged" "${base}"
expect "ccache works -> CC wrapped" "${tmp}/good/ccache ${REAL_CC}|-L/base" \
  HPC_BUNDLE_CCACHE_BIN="${tmp}/good/ccache"
expect "ccache probe fails -> unchanged" "${base}" HPC_BUNDLE_CCACHE_BIN="${tmp}/bad/ccache"
expect "HPC_BUNDLE_CCACHE=0 -> unchanged" "${base}" \
  HPC_BUNDLE_CCACHE=0 HPC_BUNDLE_CCACHE_BIN="${tmp}/good/ccache"
expect "CC already has ccache -> not wrapped twice" "ccache ${REAL_CC}|-L/base" \
  CC="ccache ${REAL_CC}" HPC_BUNDLE_CCACHE_BIN="${tmp}/good/ccache"
expect "mold works -> -B shim dir prepended" "${REAL_CC}|-B${MOLD_DIR} -L/base" \
  HPC_BUNDLE_MOLD_BIN="${tmp}/mold/bin/mold"
expect "mold without ld shim -> unchanged" "${base}" HPC_BUNDLE_MOLD_BIN="${tmp}/brokenmold/bin/mold"
expect "HPC_BUNDLE_MOLD=0 -> unchanged" "${base}" \
  HPC_BUNDLE_MOLD=0 HPC_BUNDLE_MOLD_BIN="${tmp}/mold/bin/mold"
expect "release build -> system linker by default" "${base}" \
  HPC_BUNDLE_RELEASE_BUILD=1 HPC_BUNDLE_MOLD_BIN="${tmp}/mold/bin/mold"
expect "release build + HPC_BUNDLE_MOLD=1 -> mold" "${REAL_CC}|-B${MOLD_DIR} -L/base" \
  HPC_BUNDLE_RELEASE_BUILD=1 HPC_BUNDLE_MOLD=1 HPC_BUNDLE_MOLD_BIN="${tmp}/mold/bin/mold"
expect "release build keeps ccache" "${tmp}/good/ccache ${REAL_CC}|-L/base" \
  HPC_BUNDLE_RELEASE_BUILD=1 HPC_BUNDLE_CCACHE_BIN="${tmp}/good/ccache"

echo "test_build_accel passed"
