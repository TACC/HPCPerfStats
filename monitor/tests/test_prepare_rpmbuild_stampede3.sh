#!/bin/sh
# Stampede3 prepare wrapper: creates opt-in force marker; calls prepare_rpmbuild_dirs.sh.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x ./scripts/prepare_rpmbuild_stampede3.sh
test -f ./scripts/fleet/README

# Force marker must not be committed; stampede3 prepare creates it before dist.
if test -f ./scripts/fleet/stampede3.force; then
  echo "scripts/fleet/stampede3.force must not be committed; remove it (stampede3 prepare recreates)" >&2
  exit 1
fi

# Wrapper must create the force file then delegate to prepare_rpmbuild_dirs.sh.
grep -q 'stampede3.force' ./scripts/prepare_rpmbuild_stampede3.sh \
  || { echo "prepare_rpmbuild_stampede3.sh must create scripts/fleet/stampede3.force" >&2; exit 1; }
grep -q 'prepare_rpmbuild_dirs.sh' ./scripts/prepare_rpmbuild_stampede3.sh \
  || { echo "prepare_rpmbuild_stampede3.sh must call prepare_rpmbuild_dirs.sh" >&2; exit 1; }
grep -q 'HPCS_BUNDLE_FLEET=stampede3' ./scripts/prepare_rpmbuild_stampede3.sh \
  || { echo "prepare_rpmbuild_stampede3.sh must export HPCS_BUNDLE_FLEET=stampede3" >&2; exit 1; }

# Default prepare must not require the force file.
if grep -q 'scripts/fleet/stampede3.force' ./scripts/prepare_rpmbuild_dirs.sh; then
  echo "prepare_rpmbuild_dirs.sh must not preflight scripts/fleet/stampede3.force" >&2
  exit 1
fi

# Normal prepare footer shape (documented in prepare_rpmbuild_dirs.sh).
grep -q 'rpmbuild -ba' ./scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must print rpmbuild -ba footer" >&2; exit 1; }

# dist-hook embeds force when present (Stampede3 prepare path).
grep -q 'stampede3.force' ./Makefile.am \
  || { echo "Makefile.am dist-hook must embed stampede3.force when present" >&2; exit 1; }

echo "test_prepare_rpmbuild_stampede3 passed"
