#!/bin/sh
# Lonestar6 prepare wrapper: creates opt-in force marker; calls prepare_rpmbuild_dirs.sh.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x ./scripts/prepare_rpmbuild_ls6.sh
test -f ./scripts/fleet/README

# Force marker must not be committed; ls6 prepare creates it before dist.
if test -f ./scripts/fleet/ls6.force; then
  echo "scripts/fleet/ls6.force must not be committed; remove it (ls6 prepare recreates)" >&2
  exit 1
fi

# Wrapper must create the force file then delegate to prepare_rpmbuild_dirs.sh.
grep -q 'ls6.force' ./scripts/prepare_rpmbuild_ls6.sh \
  || { echo "prepare_rpmbuild_ls6.sh must create scripts/fleet/ls6.force" >&2; exit 1; }
grep -q 'prepare_rpmbuild_dirs.sh' ./scripts/prepare_rpmbuild_ls6.sh \
  || { echo "prepare_rpmbuild_ls6.sh must call prepare_rpmbuild_dirs.sh" >&2; exit 1; }
grep -q 'HPCS_BUNDLE_FLEET=ls6' ./scripts/prepare_rpmbuild_ls6.sh \
  || { echo "prepare_rpmbuild_ls6.sh must export HPCS_BUNDLE_FLEET=ls6" >&2; exit 1; }

# Default prepare must not require the force file.
if grep -q 'scripts/fleet/ls6.force' ./scripts/prepare_rpmbuild_dirs.sh; then
  echo "prepare_rpmbuild_dirs.sh must not preflight scripts/fleet/ls6.force" >&2
  exit 1
fi

# Normal prepare footer shape (documented in prepare_rpmbuild_dirs.sh).
grep -q 'rpmbuild -ba' ./scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must print rpmbuild -ba footer" >&2; exit 1; }
# Release path must not print the optional debug rpmbuild / verify runbook.
if grep -q 'Optional debug' ./scripts/prepare_rpmbuild_dirs.sh; then
  echo "prepare_rpmbuild_dirs.sh must not advertise optional debug on release path" >&2
  exit 1
fi

# dist-hook embeds force when present (Lonestar6 prepare path).
grep -q 'ls6.force' ./Makefile.am \
  || { echo "Makefile.am dist-hook must embed ls6.force when present" >&2; exit 1; }

echo "test_prepare_rpmbuild_ls6 passed"
