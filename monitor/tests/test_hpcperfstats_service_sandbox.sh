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
  # PrivateTmp hides xpumd's host /tmp sockets: xpumcli then prints "XPUM Service Status Error"
  # and intel_gpu emits no rows (c551-004 PVC 2026-11-22).
  '^BindPaths=-/tmp/xpum_p\.sock -/tmp/xpum_up\.sock$'
)

for pat in "${required[@]}"; do
  grep -qE "${pat}" "${UNIT}" || { echo "missing unit directive matching ${pat}" >&2; exit 1; }
done

# Boot order (c551-004 2026-11-22): xpum.service is Type=simple and creates its sockets ~10 s
# after it reports started, so After= alone still let the bind miss them. A privileged
# ExecStartPre waits for both sockets while xpum.service is active; it must exit 0 in every
# case so the monitor always starts, and return at once on nodes without XPU Manager.
grep -qE '^After=xpum\.service$' "${UNIT}" || { echo "missing After=xpum.service" >&2; exit 1; }
pre="$(sed -n "s/^ExecStartPre=+\/bin\/sh -c '\(.*\)'$/\1/p" "${UNIT}" | sed 's/\$\$/$/g')"
test -n "${pre}" || { echo "missing ExecStartPre=+/bin/sh -c '...' socket wait" >&2; exit 1; }
T="$(mktemp -d "${TMPDIR:-/tmp}/hps-unit-wait.XXXXXX")"
trap 'rm -rf "${T}"' EXIT
mkdir -p "${T}/bin"
# shellcheck disable=SC2016  # $XPUM_ACTIVE expands in the fake systemctl, not here
printf '#!/bin/sh\n[ "$XPUM_ACTIVE" = 1 ]\n' >"${T}/bin/systemctl"
chmod +x "${T}/bin/systemctl"
run_wait() { # run_wait ACTIVE MAX_SECONDS: the unit's wait against ${T} sockets; prints seconds
  local s body
  body="$(printf '%s' "${pre}" | sed "s#/tmp/#${T}/#g; s/-lt 60/-lt $2/")"
  s=$(date +%s)
  PATH="${T}/bin:${PATH}" XPUM_ACTIVE="$1" timeout 30 sh -c "${body}" \
    || { echo "unit wait exited non-zero (active=$1)" >&2; exit 1; }
  echo $(($(date +%s) - s))
}
test "$(run_wait 0 60)" -le 1 || { echo "wait did not return at once without xpumd" >&2; exit 1; }
test "$(run_wait 1 2)" -ge 2 || { echo "wait did not wait for missing sockets" >&2; exit 1; }
for s in xpum_p.sock xpum_up.sock; do
  python3 -c 'import socket, sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "${T}/${s}"
done
test "$(run_wait 1 60)" -le 1 || { echo "wait did not return once sockets exist" >&2; exit 1; }

if grep -qE '^PrivateDevices=' "${UNIT}"; then
  echo "PrivateDevices must stay unset (IB/GPU/perf)" >&2
  exit 1
fi

echo "test_hpcperfstats_service_sandbox.sh passed"
