#!/usr/bin/env bash
# Verify Grace DRAM CAS (arm_aarch64_imc via LIKWID SCF) end to end on a Grace node, as root.
#
#   A. Units: likwid-perfctr around likwid-bench load/store with a known data volume
#      (CMEM_RD_DATA must be 32-byte beats, CMEM_WR_TOTAL_BYTES bytes; idle background removed).
#   B. Daemon: a DEBUG build of hpcperfstatsd runs as a transient unit (broker unreachable, dumps
#      to a temp dir, lshw stub bound like the shipped unit). Its /dev/shm mirror must show
#      arm_aarch64_imc scf0/scf1, and the scf0 delta across a known socket-0 load must match the
#      volume (64-byte CAS) while scf1 stays quiet. Same for writes.
#   C. Round-1 checks on the same daemon: NVSwitch module denied, libdcgmmodulenvswitch not mapped.
#
# Stops hpcperfstats.service for the run and restores its state on every exit path.
# Usage: verify_grace_scf_imc.sh [--no-build] [--out DIR]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MON="$(cd "${HERE}/../.." && pwd)"
WS="$(cd "${MON}/../.." && pwd)"
PREFIX="${PREFIX:-${WS}/.build/prefix-static}"
PY="${PYTHON:-python3}"
LIB="${MON}/measurement/lib/grace_scf_verify.py"
BUILD=1
OUT="${MON}/measurement/results/grace_scf_$(hostname -s)_$(date -u +%Y%m%dT%H%M%SZ)"
while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --out) OUT="$2"; shift ;;
    *) echo "usage: $0 [--no-build] [--out DIR]" >&2; exit 2 ;;
  esac
  shift
done
UNIT=hpcperfstats-scf-verify
SHM=/dev/shm/hpcperfstatsd-scf-verify
EVENTS="CMEM_RD_DATA:SCF0,CMEM_WR_TOTAL_BYTES:SCF1"
FAILS=0
WAS_ACTIVE=$(systemctl is-active hpcperfstats 2>/dev/null)

say() { printf '%s\n' "$*" | tee -a "${OUT}/summary.txt"; }
verdict() { # name json-line
  if printf '%s' "$2" | grep -q '"ok": true'; then say "PASS $1 $2"; else say "FAIL $1 $2"; FAILS=$((FAILS + 1)); fi
}
cleanup() {
  systemctl stop "${UNIT}" >/dev/null 2>&1
  systemctl reset-failed "${UNIT}" >/dev/null 2>&1
  rm -rf "${SHM}"
  if [ "${WAS_ACTIVE}" = active ]; then systemctl start hpcperfstats; fi
}
trap cleanup EXIT INT TERM HUP

mkdir -p "${OUT}/dump" || exit 2
: > "${OUT}/summary.txt"
[ "$(id -u)" = 0 ] || { echo "must run as root" >&2; exit 2; }
[ "$(uname -m)" = aarch64 ] || { echo "Grace (aarch64) only" >&2; exit 2; }
ls -d /sys/bus/event_source/devices/nvidia_scf_pmu_* > "${OUT}/scf_pmus.txt" 2>&1 \
  || { say "FAIL preflight no nvidia_scf_pmu_* in sysfs"; exit 1; }
export PATH="${PREFIX}/bin:${PATH}" LD_LIBRARY_PATH="${PREFIX}/lib:${LD_LIBRARY_PATH:-}"
command -v likwid-perfctr >/dev/null && command -v likwid-bench >/dev/null \
  || { say "FAIL preflight likwid-perfctr/likwid-bench not under ${PREFIX}/bin (run build_static_bundle.sh)"; exit 1; }
say "host $(hostname -s) kernel $(uname -r) git $(git -C "${MON}" log --oneline -1 2>/dev/null) out ${OUT}"

if [ "${WAS_ACTIVE}" = active ]; then systemctl stop hpcperfstats; say "stopped hpcperfstats.service for the run (restored on exit)"; fi

# ---- A. units ------------------------------------------------------------------------------
now() { date +%s.%N; }
perfctr() { likwid-perfctr -f -C S0:0 -g "${EVENTS}" "$@"; }
t0=$(now); perfctr sleep 5 > "${OUT}/idle_perfctr.txt" 2>&1; t1=$(now)
IDLE_S=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
for k in load store; do
  t0=$(now)
  perfctr likwid-bench -t "$k" -w S0:8GB:1 > "${OUT}/${k}_perfctr.txt" 2>&1
  t1=$(now)
  secs=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
  if [ "$k" = load ]; then ev=CMEM_RD_DATA; ctr=SCF0; bpu=32; else ev=CMEM_WR_TOTAL_BYTES; ctr=SCF1; bpu=1; fi
  verdict "units_${k}_${ev}_bytes_per_unit" \
    "$("${PY}" "${LIB}" unit "${OUT}/${k}_perfctr.txt" "${OUT}/idle_perfctr.txt" "${OUT}/${k}_perfctr.txt" "$ev" "$ctr" "$bpu" "$secs" "${IDLE_S}")"
done

# ---- B. daemon -----------------------------------------------------------------------------
if [ "${BUILD}" = 1 ]; then
  (cd "${MON}" && SKIP_DEPS=1 HPC_BUNDLE_ENABLE_DEBUG=1 ./scripts/build_static_bundle.sh) > "${OUT}/debug_bundle.log" 2>&1 \
    || { say "FAIL build DEBUG bundle (see debug_bundle.log)"; exit 1; }
fi
BIN="${MON}/.build-static/src/hpcperfstatsd"
[ -x "${BIN}" ] || { say "FAIL daemon binary missing: ${BIN}"; exit 1; }
cat > "${OUT}/verify.conf" <<EOF
server 127.0.0.1
port 1
queue scf_verify
user verify
password verify
sample_freq 5
sample_freq_slow 5
enable_slow_tier 0
send_freq 3600
EOF
chmod 600 "${OUT}/verify.conf"
STUB=/usr/libexec/hpcperfstats/stubs/lshw
BIND=()
[ -x "${STUB}" ] && BIND=(-p "BindReadOnlyPaths=-${STUB}:/usr/bin/lshw" -p "BindReadOnlyPaths=-${STUB}:/usr/sbin/lshw")
rm -rf "${SHM}"
systemctl reset-failed "${UNIT}" >/dev/null 2>&1
systemd-run --quiet --unit "${UNIT}" "${BIND[@]}" --setenv=HPCPERFSTATS_DEBUG_SHM_DIR="${SHM}" \
  "${BIN}" -c "${OUT}/verify.conf" -t "${OUT}/dump" || { say "FAIL start transient daemon"; exit 1; }

wait_new_full() { # wait for a full payload newer than $1 (epoch), up to $2 seconds
  local since=$1 deadline=$(( $(date +%s) + $2 ))
  while [ "$(date +%s)" -lt "${deadline}" ]; do
    if [ -s "${SHM}/full" ] && [ "$(stat -c %Y "${SHM}/full")" -gt "${since}" ] \
       && grep -q '^arm_aarch64_imc ' "${SHM}/full"; then return 0; fi
    sleep 1
  done
  return 1
}
if ! wait_new_full 0 180; then
  journalctl -u "${UNIT}" --no-pager > "${OUT}/daemon_journal.txt" 2>&1
  say "FAIL daemon produced no arm_aarch64_imc rows in ${SHM}/full within 180 s (see daemon_journal.txt)"
  exit 1
fi
cp "${SHM}/schema" "${OUT}/schema.txt"
verdict daemon_rows "$("${PY}" "${LIB}" rows "${OUT}/schema.txt" "${SHM}/full")"

for k in load store; do
  if [ "$k" = load ]; then key=dram_cas_reads; else key=dram_cas_writes; fi
  wait_new_full "$(date +%s)" 30 || { say "FAIL daemon stopped sampling before ${k}"; FAILS=$((FAILS + 1)); continue; }
  cp "${SHM}/full" "${OUT}/${k}_before.txt"
  likwid-bench -t "$k" -w S0:8GB:1 > "${OUT}/${k}_bench_daemon.txt" 2>&1
  wait_new_full "$(date +%s)" 30 && wait_new_full "$(date +%s)" 30
  cp "${SHM}/full" "${OUT}/${k}_after.txt"
  verdict "daemon_${k}_${key}_scf0_vs_volume" \
    "$("${PY}" "${LIB}" daemon "${OUT}/schema.txt" "${OUT}/${k}_before.txt" "${OUT}/${k}_after.txt" "${OUT}/${k}_bench_daemon.txt" scf0 "$key" scf1)"
done

# ---- C. round-1 checks on the same daemon --------------------------------------------------
journalctl -u "${UNIT}" --no-pager > "${OUT}/daemon_journal.txt" 2>&1
PID=$(systemctl show -p MainPID --value "${UNIT}")
grep -q 'Grace SCF DRAM CAS via LIKWID on [0-9]* socket' "${OUT}/daemon_journal.txt" \
  && say "PASS journal arm_aarch64_imc uses LIKWID SCF" || { say "FAIL journal has no 'Grace SCF DRAM CAS via LIKWID' line"; FAILS=$((FAILS + 1)); }
grep -q 'DCGM embedded engine started (v2, NVSwitch module denied)' "${OUT}/daemon_journal.txt" \
  && say "PASS journal DCGM v2 start with NVSwitch denied" || { say "FAIL journal has no NVSwitch-denied DCGM start line"; FAILS=$((FAILS + 1)); }
if [ -n "${PID}" ] && [ "${PID}" != 0 ]; then
  if grep -q libdcgmmodulenvswitch "/proc/${PID}/maps"; then say "FAIL libdcgmmodulenvswitch mapped"; FAILS=$((FAILS + 1)); else say "PASS libdcgmmodulenvswitch not mapped"; fi
fi
grep -E 'Enabled types|SCF|DCGM embedded' "${OUT}/daemon_journal.txt" | tail -5 | tee -a "${OUT}/summary.txt"

if [ "${FAILS}" -eq 0 ]; then say "GRACE_SCF_VERIFY_OK results in ${OUT}"; exit 0; fi
say "GRACE_SCF_VERIFY_FAIL ${FAILS} check(s) failed; results in ${OUT}"
exit 1
