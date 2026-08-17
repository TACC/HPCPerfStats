#!/usr/bin/env bash
# Lonestar6 fleet RPM prepare: export fleet env, ensure dist marker, then the
# normal prepare_rpmbuild_dirs.sh path (same footer: only the requested rpmbuild).
#
# Usage (from HPCPerfStats/monitor):
#   ./scripts/prepare_rpmbuild_ls6.sh
#   ./scripts/prepare_rpmbuild_ls6.sh --debug-build
#
# Fleet matrix (IB MAD dlopen, --disable-amd-gpu, --disable-intel-gpu; no OPA MAD):
#   HPCS_BUNDLE_FLEET=ls6 for this prepare host, and
#   scripts/fleet/ls6.force created here and embedded by dist-hook for rpm %build.
#   Do not commit ls6.force; default prepare_rpmbuild_dirs.sh does not ship it.
#
set -euo pipefail
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly FLEET_MARKER="${MONITOR_DIR}/scripts/fleet/ls6.force"

if test ! -f "${FLEET_MARKER}"; then
  mkdir -p "$(dirname "${FLEET_MARKER}")"
  : > "${FLEET_MARKER}"
fi

export HPCS_BUNDLE_FLEET=ls6

exec "${SCRIPT_DIR}/prepare_rpmbuild_dirs.sh" "$@"
