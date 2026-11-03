#!/bin/sh
# Horizon prepare wrapper: creates opt-in force marker; calls prepare_rpmbuild_dirs.sh.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x ./scripts/prepare_rpmbuild_horizon.sh
test -f ./scripts/fleet/README

# Force marker must not be committed; horizon prepare creates it before dist.
if test -f ./scripts/fleet/horizon.force; then
  echo "scripts/fleet/horizon.force must not be committed; remove it (horizon prepare recreates)" >&2
  exit 1
fi

# Wrapper must create the force file then delegate to prepare_rpmbuild_dirs.sh.
grep -q 'horizon.force' ./scripts/prepare_rpmbuild_horizon.sh \
  || { echo "prepare_rpmbuild_horizon.sh must create scripts/fleet/horizon.force" >&2; exit 1; }
grep -q 'prepare_rpmbuild_dirs.sh' ./scripts/prepare_rpmbuild_horizon.sh \
  || { echo "prepare_rpmbuild_horizon.sh must call prepare_rpmbuild_dirs.sh" >&2; exit 1; }
grep -q 'HPCS_BUNDLE_FLEET=horizon' ./scripts/prepare_rpmbuild_horizon.sh \
  || { echo "prepare_rpmbuild_horizon.sh must export HPCS_BUNDLE_FLEET=horizon" >&2; exit 1; }
grep -q 'enable-shared-node-multi-jid' ./scripts/prepare_rpmbuild_horizon.sh \
  || { echo "prepare_rpmbuild_horizon.sh must document --enable-shared-node-multi-jid" >&2; exit 1; }

# Default prepare must not require the force file.
if grep -q 'scripts/fleet/horizon.force' ./scripts/prepare_rpmbuild_dirs.sh; then
  echo "prepare_rpmbuild_dirs.sh must not preflight scripts/fleet/horizon.force" >&2
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

# dist-hook embeds force when present (Horizon prepare path).
grep -q 'horizon.force' ./Makefile.am \
  || { echo "Makefile.am dist-hook must embed horizon.force when present" >&2; exit 1; }

# Bundle must opt in shared-node for horizon fleet.
grep -q 'fleet_horizon_enabled' ./scripts/build_static_bundle.sh \
  || { echo "build_static_bundle.sh must define fleet_horizon_enabled" >&2; exit 1; }
horizon_flags="$(HPCS_BUNDLE_FLEET=horizon ./scripts/build_static_bundle.sh --print-configure-flags 2>/dev/null)"
echo "${horizon_flags}" | grep -q -- '--enable-shared-node-multi-jid' \
  || { echo "horizon fleet must pass --enable-shared-node-multi-jid" >&2; exit 1; }

# --measure-overhead / --measure-arg reach prepare_rpmbuild_dirs.sh unchanged via "$@".
grep -q 'exec "${SCRIPT_DIR}/prepare_rpmbuild_dirs.sh" "\$@"' ./scripts/prepare_rpmbuild_horizon.sh \
  || { echo "prepare_rpmbuild_horizon.sh must exec prepare_rpmbuild_dirs.sh with \"\$@\"" >&2; exit 1; }
grep -q -- '--measure-overhead)' ./scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must accept --measure-overhead" >&2; exit 1; }

echo "test_prepare_rpmbuild_horizon passed"
