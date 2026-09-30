#!/usr/bin/env bash
# Contract: hpcperfstats.service ships F6 sandbox directives (see f6-privilege-hardening plan).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="${ROOT}/src/hpcperfstats.service"

test -f "${UNIT}" || { echo "missing unit ${UNIT}" >&2; exit 1; }

required=(
  '^PrivateTmp=true'
  '^ProtectSystem=strict'
  '^ProtectHome=true'
  '^ProtectKernelTunables=true'
  '^ProtectControlGroups=true'
  '^NoNewPrivileges=true'
  '^RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK'
  '^ReadWritePaths=/var/lib/hpcperfstats /run'
)

for pat in "${required[@]}"; do
  grep -qE "${pat}" "${UNIT}" || { echo "missing unit directive matching ${pat}" >&2; exit 1; }
done

if grep -qE '^PrivateDevices=' "${UNIT}"; then
  echo "PrivateDevices must stay unset (IB/GPU/perf)" >&2
  exit 1
fi

echo "test_hpcperfstats_service_sandbox.sh passed"
