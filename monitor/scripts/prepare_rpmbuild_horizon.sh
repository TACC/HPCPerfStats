#!/usr/bin/env bash
# Horizon fleet RPM prepare: export fleet env, ensure dist marker, then the
# normal prepare_rpmbuild_dirs.sh path (same footer: only the requested rpmbuild).
#
# Usage (from HPCPerfStats/monitor):
#   ./scripts/prepare_rpmbuild_horizon.sh
#   ./scripts/prepare_rpmbuild_horizon.sh --debug-build
#
# Fleet matrix (first shared-node cluster):
#   --enable-shared-node-multi-jid, IB MAD dlopen, --disable-amd-gpu,
#   --disable-intel-gpu; no OPA MAD.
#   HPCS_BUNDLE_FLEET=horizon for this prepare host, and
#   scripts/fleet/horizon.force created here and embedded by dist-hook for rpm %build.
#   Do not commit horizon.force; default prepare_rpmbuild_dirs.sh does not ship it.
#   Deploy listend (consumer) before Horizon shared-node monitor RPMs.
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly MONITOR_DIR
readonly FLEET_MARKER="${MONITOR_DIR}/scripts/fleet/horizon.force"

if test ! -f "${FLEET_MARKER}"; then
  mkdir -p "$(dirname "${FLEET_MARKER}")"
  : > "${FLEET_MARKER}"
fi

export HPCS_BUNDLE_FLEET=horizon

exec "${SCRIPT_DIR}/prepare_rpmbuild_dirs.sh" "$@"
