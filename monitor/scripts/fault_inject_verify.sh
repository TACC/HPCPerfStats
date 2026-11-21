#!/usr/bin/env bash
# Live fault-injection step of rpm_debug_shm_verify.sh (DEBUG RPMs only).
#
# Restarts the daemon with `debug_fault_inject <spec>` appended to its conf (every injectable
# point this node has, for FAULT_INJECT_TICKS collect cycles after one clean cycle; the IB
# opendir fault last, see fault_inject_check.py), captures the samples the plan needs, restores
# the conf and restarts again, then runs scripts/fault_inject_check.py: codes on
# exactly the planned keys, values back after release, bridge validity, and every registered
# failure site fired (NOTE for sites whose collector is not active on this node).
#
# Required env: CAPS, MANIFEST, SHM_DIR, PY. Optional: FAULT_INJECT (default 1; 0 = SKIP),
# FAULT_INJECT_CONF (default HPCPERFSTATS_CONF or /etc/hpcperfstats/hpcperfstats.conf),
# FAULT_INJECT_TICKS (default 3), FAULT_INJECT_SAMPLE_FREQ (default 30),
# FAULT_INJECT_RESTART_CMD (default `systemctl restart hpcperfstats.service`),
# FAULT_INJECT_REPORT (full report path), FAULT_INJECT_SYS_ROOT (default /sys).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHECK="${SCRIPT_DIR}/fault_inject_check.py"

case "${FAULT_INJECT:-1}" in
0 | false | FALSE | no | NO | off | OFF)
  echo "SKIP: fault injection (FAULT_INJECT=0)"
  exit 0
  ;;
esac

debug="$("${PY}" -c "import json,sys; print(int(bool(json.load(open(sys.argv[1])).get('configure', {}).get('debug'))))" "${CAPS}")"
if test "${debug}" != "1"; then
  echo "SKIP: fault injection (release build: debug_fault_inject is parsed only by DEBUG daemons)"
  exit 0
fi

conf="${FAULT_INJECT_CONF:-${HPCPERFSTATS_CONF:-/etc/hpcperfstats/hpcperfstats.conf}}"
ticks="${FAULT_INJECT_TICKS:-3}"
freq="${FAULT_INJECT_SAMPLE_FREQ:-30}"
restart="${FAULT_INJECT_RESTART_CMD:-systemctl restart hpcperfstats.service}"
work="$(mktemp -d)"
backup="${work}/conf.orig"
trap 'if test -f "${backup}"; then cp -p "${backup}" "${conf}"; sh -c "${restart}" || true; fi; rm -rf "${work}"' EXIT

spec="$("${PY}" "${CHECK}" plan --capabilities "${CAPS}" --manifest "${MANIFEST}" --ticks "${ticks}" \
  --sys-root "${FAULT_INJECT_SYS_ROOT:-/sys}" --out "${work}/plan.json")"
if test -z "${spec}"; then
  echo "SKIP: fault injection (no injectable fault point on this node)"
  exit 0
fi
echo "fault injection: debug_fault_inject ${spec}"

cp -p "${conf}" "${backup}"
printf '\ndebug_fault_inject %s\n' "${spec}" >>"${conf}"
"${PY}" "${CHECK}" capture --shm-dir "${SHM_DIR}" --out-dir "${work}/cap" --restart-cmd "${restart}" \
  --plan "${work}/plan.json" --timeout "$((freq * 3 + 30))"

# Restore before judging, so a failed check still leaves the node on its real conf.
cp -p "${backup}" "${conf}"
rm -f "${backup}"
sh -c "${restart}"

report_args=()
if test -n "${FAULT_INJECT_REPORT:-}"; then
  report_args=(--report "${FAULT_INJECT_REPORT}")
fi
"${PY}" "${CHECK}" check --plan "${work}/plan.json" --manifest "${MANIFEST}" \
  --capture-dir "${work}/cap" --sample-freq "${freq}" "${report_args[@]}"
