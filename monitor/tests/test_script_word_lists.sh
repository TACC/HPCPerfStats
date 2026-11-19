#!/usr/bin/env bash
# Regression: flag / package lists held in one string (CFLAGS, CXXFLAGS, LDFLAGS,
# ROCKY9_RPM_TOOLCHAIN_PACKAGES) are split on whitespace but never
# glob-expanded. Unquoted $VAR expansion used to turn `-I*` into matching file names
# in the current directory. Runs the real functions extracted from the scripts.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${ROOT}/scripts/build_static_bundle.sh"
CROSS="${ROOT}/scripts/cross_compile_test.sh"

fail() { echo "test_script_word_lists: $*" >&2; exit 1; }

# Print one top-level function definition from a script.
extract_fn() {
  awk -v name="$2" '$0 ~ "^" name "\\(\\) \\{" { on = 1 } on { print } on && /^}/ { exit }' "$1"
}

load_fn() {
  local body
  body="$(extract_fn "$1" "$2")"
  [ -n "${body}" ] || fail "function $2 not found in $1"
  eval "${body}"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/word_lists.XXXXXX")"
trap 'rm -rf "${WORK}"' EXIT
# A file a stray glob in a flag list would expand to.
: >"${WORK}/-Iinjected"
cd "${WORK}"

# 1. build_static_bundle.sh: release flags keep a literal -I* and drop -O2 / -g.
load_fn "${BUNDLE}" static_bundle_release_build_enabled
load_fn "${BUNDLE}" static_bundle_sanitize_compiler_flags
load_fn "${BUNDLE}" static_bundle_apply_release_build_flags
check_release_flags() {
  local base="-O3 -pipe -DNDEBUG -ffunction-sections -fdata-sections"
  export HPC_BUNDLE_RELEASE_BUILD=1 CFLAGS=$'-O2 -g\t-I* -fstack-protector-strong' CXXFLAGS='' LDFLAGS=''
  static_bundle_apply_release_build_flags
  [ "${CFLAGS}" = "${base} -I* -fstack-protector-strong" ] || fail "CFLAGS=[${CFLAGS}]"
  [ "${CXXFLAGS}" = "${base}" ] || fail "CXXFLAGS=[${CXXFLAGS}]"
}
(check_release_flags)

# 2. cross_compile_test.sh: the package-list fingerprint is unchanged for the default list
#    (cached toolchain layers stay valid) and keeps glob characters literal.
load_fn "${CROSS}" rpm_toolchain_packages_fp
eval "export $(grep -m1 '^ROCKY9_RPM_TOOLCHAIN_PACKAGES=' "${CROSS}")"
[ "$(rpm_toolchain_packages_fp)" = dd59495a75bfb21d6c15ba8b703eb0d0769e5822a0613b8d28ff42cbc6ca9386 ] \
  || fail "default toolchain package fingerprint changed"
export ROCKY9_RPM_TOOLCHAIN_PACKAGES=$'make\n  -I*\tgcc'
want="$(printf '%s\n' '-I*' gcc make | LC_ALL=C sort | sha256sum | awk '{print $1}')"
[ "$(rpm_toolchain_packages_fp)" = "${want}" ] || fail "fingerprint must split on any whitespace, no glob"
export ROCKY9_RPM_TOOLCHAIN_PACKAGES=""
[ "$(rpm_toolchain_packages_fp)" = 01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b ] \
  || fail "empty package list fingerprint changed"

echo "test_script_word_lists passed"
