#!/usr/bin/env bash
# Contract: hpcperfstats.service must bind-mount the shipped lshw stub over the
# absolute paths libdcgm execs (/usr/bin/lshw, /usr/sbin/lshw). PATH alone is not
# enforcement (DCGM uses absolute execve with a minimal env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT="${ROOT}/src/hpcperfstats.service"
SPEC="${ROOT}/hpcperfstats.spec"
STUB_INSTALL='/usr/libexec/hpcperfstats/stubs/lshw'
STUB_DIR='/usr/libexec/hpcperfstats/stubs'

test -f "${UNIT}" || { echo "missing unit ${UNIT}" >&2; exit 1; }
test -f "${SPEC}" || { echo "missing spec ${SPEC}" >&2; exit 1; }

# Spec must install the stub at the same path the unit binds.
if ! grep -qE 'install .*scripts/stubs/lshw.*%\{_libexecdir\}/hpcperfstats/stubs/lshw' "${SPEC}" \
  && ! grep -q '%{_libexecdir}/hpcperfstats/stubs/lshw' "${SPEC}"; then
  echo "spec does not ship %{_libexecdir}/hpcperfstats/stubs/lshw" >&2
  exit 1
fi

bind_bin="$(grep -E '^BindReadOnlyPaths=.*:/usr/bin/lshw[[:space:]]*$' "${UNIT}" || true)"
bind_sbin="$(grep -E '^BindReadOnlyPaths=.*:/usr/sbin/lshw[[:space:]]*$' "${UNIT}" || true)"
test -n "${bind_bin}" || { echo "missing BindReadOnlyPaths for /usr/bin/lshw" >&2; exit 1; }
test -n "${bind_sbin}" || { echo "missing BindReadOnlyPaths for /usr/sbin/lshw" >&2; exit 1; }

# Source must be the installed stub (optional leading '-' for missing-source ignore).
echo "${bind_bin}" | grep -qE "BindReadOnlyPaths=-?${STUB_INSTALL}:/usr/bin/lshw" \
  || { echo "bin bind source drift: ${bind_bin}" >&2; exit 1; }
echo "${bind_sbin}" | grep -qE "BindReadOnlyPaths=-?${STUB_INSTALL}:/usr/sbin/lshw" \
  || { echo "sbin bind source drift: ${bind_sbin}" >&2; exit 1; }

path_line="$(grep -E '^Environment=PATH=' "${UNIT}" || true)"
test -n "${path_line}" || { echo "missing Environment=PATH= in unit" >&2; exit 1; }
echo "${path_line}" | grep -q "${STUB_DIR}" \
  || { echo "PATH does not list stub dir ${STUB_DIR}: ${path_line}" >&2; exit 1; }

echo "test_service_unit_lshw.sh passed"
