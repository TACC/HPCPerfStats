#!/bin/sh
# Stampede3 prepare wrapper: calls prepare_rpmbuild_dirs.sh; same rpmbuild footer shape.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x ./scripts/prepare_rpmbuild_stampede3.sh
test -f ./scripts/fleet/stampede3.force

# Wrapper must delegate to prepare_rpmbuild_dirs.sh (not invent an alternate footer).
grep -q 'prepare_rpmbuild_dirs.sh' ./scripts/prepare_rpmbuild_stampede3.sh \
  || { echo "prepare_rpmbuild_stampede3.sh must call prepare_rpmbuild_dirs.sh" >&2; exit 1; }
grep -q 'HPCS_BUNDLE_FLEET=stampede3' ./scripts/prepare_rpmbuild_stampede3.sh \
  || { echo "prepare_rpmbuild_stampede3.sh must export HPCS_BUNDLE_FLEET=stampede3" >&2; exit 1; }

# Normal prepare footer shape (documented in prepare_rpmbuild_dirs.sh).
grep -q 'rpmbuild -ba' ./scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must print rpmbuild -ba footer" >&2; exit 1; }

echo "test_prepare_rpmbuild_stampede3 passed"
