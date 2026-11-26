#!/usr/bin/env bash
# Verify Grace DRAM CAS (arm_aarch64_imc via direct perf on the SCF PMUs) end to end on a Grace
# node, as root.
#
# Known loads: scripts/canary_gemm_load.sh run --kernel read (load phase) and --kernel write
# (store phase), pinned to NUMA node 0 (socket 0 CPUs, so first touch puts the array in socket 0
# memory), bounded by --seconds; their "read_load done total_bytes_read=" and "write_load done
# total_bytes_written=" lines are the exact byte counts of the timed passes. The write load's
# write-allocate fills add DRAM reads, so the store phase checks write counters only.
#
#   A. Units, independent of the daemon: kernel perf stat on nvidia_scf_pmu_0 (cmem_rd_data,
#      cmem_wr_total_bytes) around each load (reads must be 32-byte beats, writes bytes; idle
#      background removed). NOT_RUN (not FAIL) when perf or those PMU events are missing; the
#      PMU's event list is kept in OUT.
#   B. Daemon: a DEBUG build of hpcperfstatsd runs as a transient unit (broker unreachable, dumps
#      to a temp dir, lshw stub bound like the shipped unit). Its /dev/shm mirror must show
#      arm_aarch64_imc scf0/scf1, and the scf0 dram_cas_reads (read load) and dram_cas_writes
#      (write load) deltas must match the bytes (64-byte CAS) while scf1 stays quiet.
#   B2. Round-1 DCGM cadence (watch refresh = sample_freq): host_cpu_hw DCGM user util on 8 pinned
#      busy CPUs must match host_cpu /proc/stat user time within 5% over the window, and must not
#      trail it by a sample mid-load.
#   C. Round-1 checks on the same daemon: NVSwitch module denied, libdcgmmodulenvswitch not mapped.
#
# On a daemon failure the shm mirror and a DEBUG-noise-filtered journal are kept in OUT.
# Stops hpcperfstats.service for the run and restores its state on every exit path.
# Usage: verify_grace_scf_imc.sh [--no-build] [--out DIR]
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MON="$(cd "${HERE}/../.." && pwd)"
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
SCF0_PMU=/sys/bus/event_source/devices/nvidia_scf_pmu_0
PERF_RD="nvidia_scf_pmu_0/cmem_rd_data/"
PERF_WR="nvidia_scf_pmu_0/cmem_wr_total_bytes/"
LOAD="${MON}/scripts/canary_gemm_load.sh"
LOAD_PREFIX="${HPCPERFSTATS_GEMM_PREFIX:-${OUT}/load}"
# 8 GiB (far above the caches) on 8 threads, read or written pass after pass for 20 s: the
# one-time first touch (and its kernel page zeroing) is a small share of the traffic, and each
# load spans several 5 s samples.
LOAD_MIB=8192
LOAD_THREADS=8
LOAD_SECONDS=20
FAILS=0
NOTRUN=0
WAS_ACTIVE=$(systemctl is-active hpcperfstats 2>/dev/null)

say() { printf '%s\n' "$*" | tee -a "${OUT}/summary.txt"; }
verdict() { # name json-line
  if printf '%s' "$2" | grep -q '"ok": true'; then say "PASS $1 $2"; else say "FAIL $1 $2"; FAILS=$((FAILS + 1)); fi
}
# DEBUG noise excluded from journal excerpts (per-key schema/stats lines).
NOISE='parse_schema_entry|schema_fill_entry_array|stats_type_init|stats_set:|path_open_record|stats_wr_hdr|proc_filter_pid_at_root|get_current_stats:'
# The DEBUG daemon logs ~25k lines/30 s; journald rate-limits that and drops the begin lines the
# checks need (c122-018), so its stdout/stderr go to a file instead of the journal.
DAEMON_LOG="${OUT}/daemon_output.log"
save_daemon_evidence() { # copy the shm mirror and a filtered daemon log into OUT before cleanup
  journalctl -u "${UNIT}" --no-pager > "${OUT}/daemon_journal.txt" 2>&1
  grep -vE "${NOISE}" "${DAEMON_LOG}" > "${OUT}/daemon_journal_filtered.txt" 2>/dev/null
  [ -d "${SHM}" ] && cp -a "${SHM}" "${OUT}/shm" 2>/dev/null
}
# shellcheck disable=SC2317,SC2329  # invoked via trap
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
NODE0_CPUS=$(cat /sys/devices/system/node/node0/cpulist 2>/dev/null)
[ -n "${NODE0_CPUS}" ] || { say "FAIL preflight no /sys/devices/system/node/node0/cpulist"; exit 1; }
"${LOAD}" install --prefix "${LOAD_PREFIX}" > "${OUT}/load_install.log" 2>&1 \
  || { say "FAIL preflight load build (see load_install.log; needs cc with -fopenmp)"; exit 1; }
# Bounded socket-0 loads (argvs, so perf stat can exec them); their output ends with
# "read_load done total_bytes_read=N" / "write_load done total_bytes_written=N".
READ_LOAD=(taskset -c "${NODE0_CPUS}" "${LOAD}" run --kernel read --mib "${LOAD_MIB}"
  --threads "${LOAD_THREADS}" --seconds "${LOAD_SECONDS}" --prefix "${LOAD_PREFIX}")
WRITE_LOAD=(taskset -c "${NODE0_CPUS}" "${LOAD}" run --kernel write --mib "${LOAD_MIB}"
  --threads "${LOAD_THREADS}" --seconds "${LOAD_SECONDS}" --prefix "${LOAD_PREFIX}")
say "host $(hostname -s) kernel $(uname -r) git $(git -C "${MON}" log --oneline -1 2>/dev/null) out ${OUT}"

if [ "${WAS_ACTIVE}" = active ]; then systemctl stop hpcperfstats; say "stopped hpcperfstats.service for the run (restored on exit)"; fi

# ---- A. units ------------------------------------------------------------------------------
now() { date +%s.%N; }
ls "${SCF0_PMU}/events" > "${OUT}/scf_pmu0_events.txt" 2>&1
UNITS_SKIP=""
if ! command -v perf >/dev/null; then
  UNITS_SKIP="perf not installed"
elif [ ! -e "${SCF0_PMU}/events/cmem_rd_data" ] || [ ! -e "${SCF0_PMU}/events/cmem_wr_total_bytes" ]; then
  UNITS_SKIP="nvidia_scf_pmu_0 lacks cmem_rd_data/cmem_wr_total_bytes (see scf_pmu0_events.txt)"
fi
perfstat() { local o=$1; shift; perf stat -x, -a -e "${PERF_RD},${PERF_WR}" -o "$o" -- "$@"; }
units_phase() { # phase event bytes-per-unit load-argv...: SCF unit vs the load's exact bytes
  local k=$1 ev=$2 bpu=$3 t0 t1 secs
  shift 3
  t0=$(now)
  if perfstat "${OUT}/${k}_perf.txt" "$@" > "${OUT}/${k}_output_perf.txt" 2>&1; then
    t1=$(now)
    secs=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
    verdict "units_${k}_${ev}_bytes_per_unit" \
      "$("${PY}" "${LIB}" unit "${OUT}/${k}_perf.txt" "${OUT}/idle_perf.txt" "${OUT}/${k}_output_perf.txt" "${ev}" "${bpu}" "$secs" "${IDLE_S}")"
  else
    say "FAIL units_${k} perf stat or the ${k}-phase load failed (see ${k}_perf.txt, ${k}_output_perf.txt)"; FAILS=$((FAILS + 1))
  fi
}
if [ -n "${UNITS_SKIP}" ]; then
  for k in load store; do say "NOT_RUN units_${k} ${UNITS_SKIP}"; NOTRUN=$((NOTRUN + 1)); done
else
  t0=$(now); perfstat "${OUT}/idle_perf.txt" sleep 5 > /dev/null 2>&1; t1=$(now)
  IDLE_S=$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b-a}')
  units_phase load "${PERF_RD}" 32 "${READ_LOAD[@]}"
  units_phase store "${PERF_WR}" 1 "${WRITE_LOAD[@]}"
fi

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
: > "${DAEMON_LOG}"
systemd-run --quiet --unit "${UNIT}" "${BIND[@]}" --setenv=HPCPERFSTATS_DEBUG_SHM_DIR="${SHM}" \
  -p StandardOutput="append:${DAEMON_LOG}" -p StandardError="append:${DAEMON_LOG}" \
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
  save_daemon_evidence
  if [ -s "${SHM}/full" ]; then
    say "FAIL daemon writes ${SHM}/full but it has no arm_aarch64_imc rows after 180 s (see shm/, daemon_journal_filtered.txt)"
  else
    say "FAIL daemon wrote no ${SHM}/full payload within 180 s (see daemon_journal_filtered.txt)"
  fi
  grep -iE 'arm_aarch64_imc|SCF|overlay|DCGM|Enabled types' "${OUT}/daemon_journal_filtered.txt" | tail -15 | tee -a "${OUT}/summary.txt"
  exit 1
fi
cp "${SHM}/schema" "${OUT}/schema.txt"
verdict daemon_rows "$("${PY}" "${LIB}" rows "${OUT}/schema.txt" "${SHM}/full")"

daemon_phase() { # phase key load-argv...: scf0 CAS delta across the load vs its exact bytes
  local k=$1 key=$2
  shift 2
  if ! wait_new_full "$(date +%s)" 30; then
    say "FAIL daemon stopped sampling before the ${k} phase"; FAILS=$((FAILS + 1)); return
  fi
  cp "${SHM}/full" "${OUT}/${k}_before.txt"
  "$@" > "${OUT}/${k}_output_daemon.txt" 2>&1 \
    || say "WARN ${k}-phase load exited non-zero (see ${k}_output_daemon.txt)"
  wait_new_full "$(date +%s)" 30 && wait_new_full "$(date +%s)" 30
  cp "${SHM}/full" "${OUT}/${k}_after.txt"
  verdict "daemon_${k}_${key}_scf0_vs_volume" \
    "$("${PY}" "${LIB}" daemon "${OUT}/schema.txt" "${OUT}/${k}_before.txt" "${OUT}/${k}_after.txt" "${OUT}/${k}_output_daemon.txt" scf0 "${key}" scf1)"
}
daemon_phase load dram_cas_reads "${READ_LOAD[@]}"
daemon_phase store dram_cas_writes "${WRITE_LOAD[@]}"

# ---- B2. DCGM util fidelity at sample_freq cadence -----------------------------------------
LOADCPUS=8-15
if wait_new_full "$(date +%s)" 30; then
  cp "${SHM}/full" "${OUT}/util_before.txt"
  PIDS=()
  for c in $(seq 8 15); do taskset -c "$c" timeout 30 sh -c 'while :; do :; done' & PIDS+=($!); done
  sleep 15
  wait_new_full "$(date +%s)" 10 && cp "${SHM}/full" "${OUT}/util_mid.txt"
  wait "${PIDS[@]}" 2>/dev/null
  wait_new_full "$(date +%s)" 30 && wait_new_full "$(date +%s)" 30
  cp "${SHM}/full" "${OUT}/util_after.txt"
  verdict "dcgm_util_vs_proc_stat_cpus_${LOADCPUS}" \
    "$("${PY}" "${LIB}" util "${OUT}/schema.txt" "${OUT}/util_before.txt" "${OUT}/util_mid.txt" "${OUT}/util_after.txt" "${LOADCPUS}")"
else
  say "FAIL daemon stopped sampling before the util check"; FAILS=$((FAILS + 1))
fi

# ---- C. round-1 checks on the same daemon --------------------------------------------------
save_daemon_evidence
PID=$(systemctl show -p MainPID --value "${UNIT}")
if grep -aq 'Grace SCF DRAM CAS via direct perf on [0-9]* socket' "${DAEMON_LOG}"; then
  say "PASS journal arm_aarch64_imc reads SCF via direct perf"
else
  say "FAIL journal has no 'Grace SCF DRAM CAS via direct perf' line"; FAILS=$((FAILS + 1))
fi
if grep -aq 'DCGM embedded engine started (v2, NVSwitch module denied)' "${DAEMON_LOG}"; then
  say "PASS journal DCGM v2 start with NVSwitch denied"
else
  say "FAIL journal has no NVSwitch-denied DCGM start line"; FAILS=$((FAILS + 1))
fi
if [ -n "${PID}" ] && [ "${PID}" != 0 ]; then
  if grep -q libdcgmmodulenvswitch "/proc/${PID}/maps"; then say "FAIL libdcgmmodulenvswitch mapped"; FAILS=$((FAILS + 1)); else say "PASS libdcgmmodulenvswitch not mapped"; fi
fi
grep -E 'Enabled types|SCF|DCGM embedded|arm_aarch64_imc' "${OUT}/daemon_journal_filtered.txt" | tail -8 | tee -a "${OUT}/summary.txt"

if [ "${FAILS}" -eq 0 ]; then say "GRACE_SCF_VERIFY_OK (${NOTRUN} not run) results in ${OUT}"; exit 0; fi
say "GRACE_SCF_VERIFY_FAIL ${FAILS} check(s) failed; results in ${OUT}"
exit 1
