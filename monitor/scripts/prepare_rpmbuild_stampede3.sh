#!/usr/bin/env bash
# Stampede3 fleet RPM prepare: export fleet env, ensure dist marker, then the
# normal prepare_rpmbuild_dirs.sh path (same rpmbuild -ba footer).
#
# Usage (from HPCPerfStats/monitor):
#   ./scripts/prepare_rpmbuild_stampede3.sh
#   ./scripts/prepare_rpmbuild_stampede3.sh --debug-build
#
# Fleet matrix (IB/OPA MAD dlopen, --disable-amd-gpu, intel_gpu when vendored):
#   HPCS_BUNDLE_FLEET=stampede3 for this prepare host, and
#   scripts/fleet/stampede3.force shipped in make dist for rpm %build.
#
set -euo pipefail
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly FLEET_MARKER="${MONITOR_DIR}/scripts/fleet/stampede3.force"

if test ! -f "${FLEET_MARKER}"; then
  mkdir -p "$(dirname "${FLEET_MARKER}")"
  : > "${FLEET_MARKER}"
fi

export HPCS_BUNDLE_FLEET=stampede3

exec "${SCRIPT_DIR}/prepare_rpmbuild_dirs.sh" "$@"
