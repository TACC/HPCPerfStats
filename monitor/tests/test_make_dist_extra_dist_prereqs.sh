#!/bin/sh
# Regression: EXTRA_DIST / make dist prerequisites exist and prepare preflight covers them.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -f tests/scripts/bootstrap_local_rabbitmq.sh \
  || { echo "missing tests/scripts/bootstrap_local_rabbitmq.sh (required for make dist)" >&2; exit 1; }
test -x tests/scripts/bootstrap_local_rabbitmq.sh \
  || { echo "tests/scripts/bootstrap_local_rabbitmq.sh must be executable" >&2; exit 1; }

test -f scripts/check_unsafe_c_patterns.sh \
  || { echo "missing scripts/check_unsafe_c_patterns.sh (EXTRA_DIST via top_srcdir)" >&2; exit 1; }
test -f scripts/check_unsafe_c_patterns.allowlist \
  || { echo "missing scripts/check_unsafe_c_patterns.allowlist (EXTRA_DIST via top_srcdir)" >&2; exit 1; }

grep -q 'tests/scripts/bootstrap_local_rabbitmq.sh' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must preflight tests/scripts/bootstrap_local_rabbitmq.sh" >&2; exit 1; }

echo "test_make_dist_extra_dist_prereqs passed"
