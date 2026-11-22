#!/usr/bin/env bash
# shellcheck disable=SC2016 # awk programs are single-quoted on purpose
# Grace counter canary (cpu-precision Phase 1, O3 + precision slices): for each precision it
# starts scripts/canary_gemm_load.sh, measures the monitor's host_cpu_hw rates from the DEBUG
# shm mirror and `perf stat -a` over the same window, then prints monitor/perf ratios and a
# PASS/FAIL line. monitor/perf agreement cannot prove absolute accuracy (both scale by
# enabled/running), so it also divides the GEMM's own GFLOP/s over the same window by the
# monitor FP rate: a 128-bit SVE FMA is at most 4 / 8 / 16 FLOPs per dp / sp / hp operation,
# and a ratio above that ceiling (1% margin, 5% when the core events ran under half of enabled
# time because another PMU user holds counters) means the scaled count reads low. On builds with the Phase 1b keys it also
# prints the D6 sharing keys per load: the share of enabled time the core events ran, the share
# expected with no other PMU user (solo), and the outside share; running above 100% fails, and a
# daemon built before Phase 1b (keys absent) is reported, not failed. Run as root on a node with
# the 3.1 debug RPM installed, from the monitor checkout. Nothing here reads the terminal,
# every wait and external command is time-limited, and the GEMM load is stopped on any exit
# (including Ctrl-C).
#
# --scf instead checks the Grace SCF memory counters: it runs a DRAM-bound dp GEMM
# (--size 4096, matrices larger than L3) and compares the monitor's arm_aarch64_imc
# dram_cas_reads / dram_cas_writes (64-byte transfers, summed over every scf<N> row) with
# `perf stat -a` on every nvidia_scf_pmu_<N> (cmem_rd_data 32-byte beats, cmem_wr_total_bytes)
# over the same window (perf runs until the daemon's next sample and is divided by its own wall
# time); PASS when both agree within 5% and reads exceed 1 GB/s.
#
# --sharing-only reads just the D6 sharing keys over one --window with no load and no perf,
# so this script holds no counters: a quiet Neoverse V2 node reads running ~75% against
# solo-expected 77.8%, outside ~3% (kernel counter rotation; measured 75.3 / 3.2 on Vista,
# 2026-11-14); a clearly higher outside share is another PMU user (nvidia-powerd: 73.6% on
# Horizon GB200, a profiler). FAILs when the daemon has no Phase 1b keys.
#
# --accuracy is the counter-accuracy layer of rpm_debug_shm_verify.sh on every architecture
# (Intel, AMD, Grace): the daemon's own counter deltas against loads with exact totals, each in
# one window between two fast samples (scripts/lib/counter_accuracy.py holds the parsing, the
# tolerance table and the verdicts). Memory: the read and write kernels pinned (taskset) to the
# socket-0 CPUs vs dram_cas_* / hbm_cas_* / DF beats summed over every memory-controller row
# (Grace: plus the scf0 share). FLOPs: dp and sp GEMMs with exact FLOP totals vs Intel
# fp_arith_inst_retired_* lane-weighted, AMD fp_ops_retired (Zen5 amd_fp_dp_ops split) or the
# Grace arm_fp_*_spec flops-per-op ceiling above. Instructions: instr_retired vs `perf stat -a
# instructions` over the dp window. Energy (x86): pkg_energy / dram_energy vs powercap
# energy_uj (package-N and dram zones) over the dp window. A missing source is NOT_RUN, never
# PASS; RESULT: FAIL only on a measured mismatch or a daemon that stopped publishing.
#
# Usage:
#   ./scripts/canary_counter_check.sh [--precisions "dp sp hp idle"] [--window S] [--warm S]
#   ./scripts/canary_counter_check.sh --scf [--window S] [--warm S]
#   ./scripts/canary_counter_check.sh --sharing-only [--window S]
#   ./scripts/canary_counter_check.sh --accuracy [--seconds S] [--results FILE]
#
# Output goes to the terminal and to $HOME/hpcperfstats-counter-check/<host>-<epoch>.log; the
# --accuracy results TSV defaults to the same name with .accuracy.tsv.
# Test overrides (env): CANARY_SHM, CANARY_PERF, CANARY_LOAD, CANARY_WAIT_MAX, CANARY_LOG_DIR,
# CANARY_GEMM_LOG, CANARY_PMU_DIR, CANARY_SCF_SIZE; --accuracy: CANARY_PY, CANARY_SYS_CPU,
# CANARY_POWERCAP, CANARY_CPUINFO, CANARY_PMU_NAME, CANARY_LOAD_GRACE (load start-up allowance,
# default 600 s for the first build of the load binaries), CANARY_ACC_MIB, CANARY_ACC_SIZE.
set -uo pipefail
exec </dev/null

PRECISIONS="dp sp hp idle"
WINDOW=120
WARM=60
SHM="${CANARY_SHM:-/dev/shm/hpcperfstatsd-debug}"
PERF="${CANARY_PERF:-perf}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOAD="${CANARY_LOAD:-${HERE}/canary_gemm_load.sh}"
WAIT_MAX="${CANARY_WAIT_MAX:-150}"
LOG_DIR="${CANARY_LOG_DIR:-${HOME}/hpcperfstats-counter-check}"
GEMM_LOG="${CANARY_GEMM_LOG:-${HPCPERFSTATS_GEMM_PREFIX:-${HOME}/hpcperfstats-gemm-load}/gemm_load.log}"
KEYS="instr_retired arm_fp_dp_spec arm_fp_sp_spec arm_fp_hp_spec"
# perf events in the same order as KEYS (instructions, FP_DP_SPEC, FP_SP_SPEC, FP_HP_SPEC).
PMU="armv8_pmuv3_0"
EVENTS="instructions,${PMU}/event=0x801c/,${PMU}/event=0x8018/,${PMU}/event=0x8014/"
SCF=0
SHARING_ONLY=0
PMU_DIR="${CANARY_PMU_DIR:-/sys/bus/event_source/devices}"
SCF_SIZE="${CANARY_SCF_SIZE:-4096}"
ACCURACY=0
ACC_SECONDS=20
RESULTS=""
ACC_PY="${CANARY_PY:-python3}"
ACC_LIB="${HERE}/lib/counter_accuracy.py"
ACC_PID=""
IMC_KEYS="dram_cas_reads dram_cas_writes"
SHARE_KEYS="pmu_enabled_time pmu_running_time pmu_solo_running_time"
# flops/op ceiling margin: strict, or wider when the core events ran under SHARED_RUNNING_PCT
# of enabled time (another PMU user; the scaled count then scatters by a few percent).
FLOPS_MARGIN=0.01
SHARED_FLOPS_MARGIN=0.05
SHARED_RUNNING_PCT=50

die() {
  echo "canary_counter_check: $*" >&2
  exit 2
}
is_uint() {
  case "$1" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac
}

while [ $# -gt 0 ]; do
  case "$1" in
  --precisions) PRECISIONS="${2:-}"; shift 2 ;;
  --window) WINDOW="${2:-}"; shift 2 ;;
  --warm) WARM="${2:-}"; shift 2 ;;
  --scf) SCF=1; shift ;;
  --sharing-only) SHARING_ONLY=1; shift ;;
  --accuracy) ACCURACY=1; shift ;;
  --seconds) ACC_SECONDS="${2:-}"; shift 2 ;;
  --results) RESULTS="${2:-}"; shift 2 ;;
  -h | --help) awk 'NR>2 && /^#/ { sub(/^# ?/, ""); print; next } NR>2 { exit }' "$0"; exit 0 ;;
  *) die "unknown option '$1'" ;;
  esac
done
if ! is_uint "${WINDOW}" || [ "${WINDOW}" -eq 0 ]; then
  die "--window must be a positive integer"
fi
if ! is_uint "${ACC_SECONDS}" || [ "${ACC_SECONDS}" -eq 0 ]; then
  die "--seconds must be a positive integer"
fi
is_uint "${WARM}" || die "--warm must be a non-negative integer"
for p in ${PRECISIONS}; do
  case "${p}" in dp | sp | hp | idle) ;; *) die "bad precision '${p}'" ;; esac
done
if [ ! -r "${SHM}/fast" ] || [ ! -r "${SHM}/schema" ]; then
  die "no ${SHM}/fast or schema (is the 3.1 debug RPM running?)"
fi
[ -x "${LOAD}" ] || die "missing ${LOAD}"

mkdir -p "${LOG_DIR}" || die "cannot create ${LOG_DIR}"
LOG="${LOG_DIR}/$(hostname -s)-$(date +%s).log"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/canary-counter.XXXXXX")" || die "mktemp failed"
# The log tee ignores INT / TERM: timeout(1) signals its whole process group, and a tee that
# died first made the INT trap's echo hit a closed pipe (SIGPIPE, exit 141 instead of 130). It
# still exits on EOF when this script ends.
exec > >(trap '' INT TERM; exec tee -a "${LOG}") 2>&1

stop_load() { timeout 60 "${LOAD}" stop >/dev/null 2>&1 || true; }
cleanup() {
  # An --accuracy window runs in the background under timeout(1) (its own process group, so a
  # terminal Ctrl-C does not reach it): TERM makes counter_accuracy.py kill the load and perf.
  if [ -n "${ACC_PID}" ]; then
    kill -TERM "${ACC_PID}" 2>/dev/null
    for _ in $(seq 40); do kill -0 "${ACC_PID}" 2>/dev/null || break; sleep 0.25; done
  fi
  stop_load
  rm -rf "${WORK}"
}
trap cleanup EXIT
trap 'echo "interrupted"; exit 130' INT TERM

say() { echo "[$(date +%H:%M:%S)] $*"; }

# Monitor payloads start with a blank line; the header is the first non-blank line.
fast_ts() { awk 'NF { print $1; exit }' "${SHM}/fast" 2>/dev/null; }

# Wait (bounded) until the fast payload has a new header timestamp.
wait_new() {
  local t i=0
  t="$(fast_ts)"
  while [ "$(fast_ts)" = "${t}" ] && [ "${i}" -lt "${WAIT_MAX}" ]; do
    sleep 1
    i=$((i + 1))
  done
  [ "${i}" -lt "${WAIT_MAX}" ] || say "WARN: fast unchanged for ${WAIT_MAX} s (ts ${t})"
}

# snap TYPE "KEYS": per-device values of KEYS for TYPE rows in the fast payload (fast keys
# only: skip ,R=S schema tokens). Output: "TS <ts>" then "<dev> <v1> ... <vN>".
snap() {
  timeout 30 awk -v type="$1" -v keys="$2" '
    BEGIN { nk = split(keys, want, " ") }
    FNR==NR { if ($1==("!" type)) { n=0
                for (i=2;i<=NF;i++) { if ($i ~ /,R=S/) continue
                  n++; split($i,a,","); for (j=1;j<=nk;j++) if (a[1]==want[j]) col[j]=n } }
              next }
    !h && NF { print "TS", $1; h=1; next }
    $1==type && $3=="@fast" {
      line=$2; for (j=1;j<=nk;j++) line=line " " (col[j] ? $(3+col[j]) : "NA"); print line }
  ' "${SHM}/schema" "${SHM}/fast"
}

# rates A B "KEYS": per-KEY rates between two snaps, summed over devices; 48-bit wrap aware.
# Output: "<key> <rate|NA> <dt> <ndev>".
rates() {
  awk -v keys="$3" '
    BEGIN { nk = split(keys, k, " ") }
    FNR==NR { if ($1=="TS") t0=$2; else for (j=1;j<=nk;j++) a[$1,j]=$(j+1); next }
    $1=="TS" { t1=$2; next }
    { cpu[$1]=1; for (j=1;j<=nk;j++) b[$1,j]=$(j+1) }
    END { dt=t1-t0; n=0; for (c in cpu) n++
          for (j=1;j<=nk;j++) {
            s=0; ok=(dt>0 && n>0)
            for (c in cpu) { if (b[c,j]=="NA" || a[c,j]=="" ) { ok=0; break }
                             d=b[c,j]-a[c,j]; if (d<0) d+=2^48; s+=d }
            if (ok) printf "%s %.6g %d %d\n", k[j], s/dt, dt, n
            else printf "%s NA %d %d\n", k[j], dt, n } }
  ' "$1" "$2"
}

# perf rates in KEYS order (one slot per event line, NA when not counted).
# Output: "<key> <rate|NA> <running%|NA>".
perf_rates() {
  awk -F, -v keys="${KEYS}" -v win="$2" '
    BEGIN { nk = split(keys, k, " ") }
    NF>4 { n++; run[n]=$5; if ($1 ~ /^[0-9.]+$/) r[n]=$1/win }
    END { for (j=1;j<=nk;j++)
            printf "%s %s %s\n", k[j], (j in r) ? sprintf("%.6g", r[j]) : "NA",
                   (j in run) ? run[j] : "NA" }
  ' "$1"
}

say "host $(hostname -s), $(rpm -q hpcperfstatsd 2>/dev/null || echo 'rpm query failed')"
grep -hE '^[[:space:]]*(sample_freq|sample_freq_slow|enable_slow_tier)\b' \
  /etc/hpcperfstats/hpcperfstats.conf 2>/dev/null
say "governor $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)," \
  "nvidia-powerd $(timeout 10 systemctl is-active nvidia-powerd 2>/dev/null)"
say "log ${LOG}; precisions: ${PRECISIONS}; about $((WARM + WINDOW + 2 * 60))" \
  "s each"
stop_load

# SCF memory counters: monitor arm_aarch64_imc vs perf nvidia_scf_pmu_<N> over one window.
scf_check() {
  local d ev="" n=0 prc
  for d in "${PMU_DIR}"/nvidia_scf_pmu_*; do
    [ -e "${d}" ] || continue
    ev="${ev:+${ev},}${d##*/}/cmem_rd_data/,${d##*/}/cmem_wr_total_bytes/"
    n=$((n + 1))
  done
  if [ "${n}" -eq 0 ]; then
    say "FAIL: no nvidia_scf_pmu_* under ${PMU_DIR}"
    echo "RESULT: FAIL"
    return 1
  fi
  say "===== scf (${n} SCF PMU(s), dp load --size ${SCF_SIZE})"
  if ! timeout 120 "${LOAD}" start --precision dp --size "${SCF_SIZE}"; then
    say "FAIL: load start"
    echo "RESULT: FAIL"
    return 1
  fi
  sleep "${WARM}"
  wait_new
  snap arm_aarch64_imc "${IMC_KEYS}" >"${WORK}/a"
  # perf runs from just after sample a until the daemon writes the first sample after WINDOW,
  # and is divided by its own wall time, so both rates cover the same span of a varying load.
  t0=$(date +%s.%N)
  timeout $((WINDOW + WAIT_MAX + 60)) "${PERF}" stat -a -x, -e "${ev}" -- sh -c '
    sleep "$1"; ts() { awk "NF { print \$1; exit }" "$2" 2>/dev/null; }
    t=$(ts "$@"); i=0
    while [ "$(ts "$@")" = "$t" ] && [ "$i" -lt "$3" ]; do sleep 1; i=$((i + 1)); done
  ' sh "${WINDOW}" "${SHM}/fast" "${WAIT_MAX}" 2>"${WORK}/perf" >/dev/null
  prc=$?
  pwin=$(awk -v a="${t0}" -v b="$(date +%s.%N)" 'BEGIN { printf "%.3f", b - a }')
  [ "${prc}" -eq 0 ] || say "WARN: perf exit ${prc}"
  snap arm_aarch64_imc "${IMC_KEYS}" >"${WORK}/b"
  stop_load
  rates "${WORK}/a" "${WORK}/b" "${IMC_KEYS}" >"${WORK}/m"
  # perf lines alternate rd (32-byte beats), wr (bytes) per PMU; sum each, keep any unit.
  awk -F, -v win="${pwin}" '
    NF>4 { n++; ok = ($1 ~ /^[0-9.]+$/); if (!ok) bad=1
           if (n % 2) { rd += $1; ru = $2 } else { wr += $1; wu = $2 } }
    END { if (bad || n == 0 || n % 2) print "NA NA - -"
          else printf "%.6g %.6g %s %s\n", rd/win, wr/win, (ru=="" ? "-" : ru),
                      (wu=="" ? "-" : wu) }
  ' "${WORK}/perf" >"${WORK}/p"
  awk '
    FNR==NR { if ($1=="dram_cas_reads") mr=$2; if ($1=="dram_cas_writes") mw=$2
              dt=$3; nd=$4; next }
    { pr=$1; pw=$2; ru=$3; wu=$4 }
    END {
      if (ru != "-" || wu != "-") printf "  WARN: perf applied a unit (%s / %s)\n", ru, wu
      if (mr=="NA" || mw=="NA" || pr=="NA" || pr<=0 || pw<=0) {
        printf "  monitor rd %s wr %s cas/s, perf rd %s beats/s wr %s B/s (%s s, %s rows)\n",
          mr, mw, pr, pw, dt, nd
        print "RESULT: FAIL"; exit 1 }
      mrb = mr*64; prb = pr*32; mwb = mw*64; pwb = pw
      rr = mrb/prb; wr = mwb/pwb
      printf "  read  monitor %.2f GB/s perf %.2f GB/s mon/perf %.3f (%s s, %s scf rows)\n",
        mrb/1e9, prb/1e9, rr, dt, nd
      printf "  write monitor %.2f GB/s perf %.2f GB/s mon/perf %.3f\n", mwb/1e9, pwb/1e9, wr
      okr = (rr>=0.95 && rr<=1.05); okw = (wr>=0.95 && wr<=1.05); bw = (prb >= 1e9)
      printf "scf read  %s (limit 5%%)\n", okr ? "PASS" : "FAIL"
      printf "scf write %s (limit 5%%)\n", okw ? "PASS" : "FAIL"
      printf "scf load  %s (read %.2f GB/s, need >= 1)\n", bw ? "PASS" : "FAIL", prb/1e9
      if (okr && okw && bw) { print "RESULT: PASS"; exit 0 }
      print "RESULT: FAIL"; exit 1 }
  ' "${WORK}/m" "${WORK}/p"
}
acc() { timeout 60 "${ACC_PY}" "${ACC_LIB}" "$@"; }

# acc_window KIND [--perf] -- CMD...: one daemon window around CMD (in the background, so a
# signal reaches the traps at once), then the checks of KIND appended to RESULTS.
acc_window() {
  local kind="$1" perf_args=()
  shift
  if [ "$1" = --perf ]; then
    perf_args=(--perf "${PERF}")
    shift
  fi
  shift
  say "===== accuracy ${kind}"
  timeout $((2 * WAIT_MAX + ACC_SECONDS + ACC_GRACE + 60)) "${ACC_PY}" "${ACC_LIB}" window \
    --shm "${SHM}" --out "${WORK}/${kind}.json" --wait-max "${WAIT_MAX}" \
    --load-timeout $((ACC_SECONDS + ACC_GRACE)) --powercap "${POWERCAP}" \
    --log "${WORK}/${kind}.log" ${perf_args[@]+"${perf_args[@]}"} -- "$@" &
  ACC_PID=$!
  wait "${ACC_PID}"
  ACC_PID=""
  acc evaluate --window "${WORK}/${kind}.json" --kind "${kind}" --arch "${ACC_CLASS}" \
    --results "${RESULTS}"
}

accuracy_check() {
  local cpus n0 k
  ACC_GRACE="${CANARY_LOAD_GRACE:-600}"
  POWERCAP="${CANARY_POWERCAP:-/sys/class/powercap}"
  ACC_CLASS="$(acc arch --cpuinfo "${CANARY_CPUINFO:-/proc/cpuinfo}" \
    --pmu-name "${CANARY_PMU_NAME:-/sys/bus/event_source/devices/cpu/caps/pmu_name}" \
    --field class)" || ACC_CLASS=other
  read -r cpus n0 <<<"$(acc socket0-cpus --sys-cpu "${CANARY_SYS_CPU:-/sys/devices/system/cpu}")"
  say "accuracy: arch ${ACC_CLASS}, socket-0 CPUs ${cpus:-none} (${n0:-0}), ${ACC_SECONDS} s" \
    "loads, results ${RESULTS}"
  if [ -n "${cpus}" ] && command -v taskset >/dev/null 2>&1; then
    for k in read write; do
      acc_window "memory-${k}" -- taskset -c "${cpus}" "${LOAD}" run --kernel "${k}" \
        --mib "${CANARY_ACC_MIB:-8192}" --threads "${n0}" --seconds "${ACC_SECONDS}"
    done
  else
    acc record --results "${RESULTS}" --status NOT_RUN --reason \
      "no socket-0 CPU list or no taskset to pin the memory load" mem_read mem_socket0 mem_write
  fi
  acc_window gemm-dp --perf -- "${LOAD}" run --precision dp --size "${CANARY_ACC_SIZE:-1024}" \
    --seconds "${ACC_SECONDS}"
  acc_window gemm-sp -- "${LOAD}" run --precision sp --size "${CANARY_ACC_SIZE:-1024}" \
    --seconds "${ACC_SECONDS}"
  say "===== accuracy summary"
  acc summarize --results "${RESULTS}"
}
if [ "${ACCURACY}" -eq 1 ]; then
  RESULTS="${RESULTS:-${LOG%.log}.accuracy.tsv}"
  accuracy_check
  rc=$?
  say "done; log ${LOG}"
  exit "${rc}"
fi

if [ "${SCF}" -eq 1 ]; then
  scf_check
  rc=$?
  say "done; log ${LOG}"
  exit "${rc}"
fi

SUMMARY="${WORK}/summary"
: >"${SUMMARY}"

# sharing_line LABEL: D6 sharing (Phase 1b keys) between the sa / sb snapshots: share of
# enabled time the core events ran, the share expected with no other PMU user, and the
# outside share (another program holding counters). Appends "LABEL sharing ..." to SUMMARY.
sharing_line() {
  rates "${WORK}/sa" "${WORK}/sb" "${SHARE_KEYS}" | awk -v p="$1" -v out="${SUMMARY}" '
    { r[$1]=$2 }
    END { en=r["pmu_enabled_time"]; run=r["pmu_running_time"]; solo=r["pmu_solo_running_time"]
          if (en=="NA" || run=="NA" || en<=0) {
            print "  sharing not published (daemon without the Phase 1b keys)"
            print p, "sharing", "NA" >> out; exit }
          rp=100*run/en; sp=(solo=="NA") ? "NA" : sprintf("%.1f", 100*solo/en)
          op=(solo=="NA" || solo<=0) ? "NA" : sprintf("%.1f", 100*(1-run/solo))
          printf "  sharing running %.1f%% solo-expected %s%% outside %s%%\n", rp, sp, op
          print p, "sharing", sprintf("%.1f", rp), sp, op >> out }'
}

# --sharing-only: no load and no perf, so nothing from this script holds counters; reads the
# D6 keys over one window. On a quiet node running == solo-expected and outside ~ 0; anything
# else is another PMU user (nvidia-powerd, a profiler, perf).
if [ "${SHARING_ONLY}" -eq 1 ]; then
  say "===== sharing-only (no load, no perf; ${WINDOW} s window)"
  if command -v systemctl >/dev/null 2>&1; then
    say "nvidia-powerd $(timeout 5 systemctl is-active nvidia-powerd 2>/dev/null || true)"
  fi
  wait_new
  snap host_cpu_hw "${SHARE_KEYS}" >"${WORK}/sa"
  sleep "${WINDOW}"
  wait_new
  snap host_cpu_hw "${SHARE_KEYS}" >"${WORK}/sb"
  sharing_line quiet
  awk '
    $3=="NA" { print "sharing quiet not published (daemon without the Phase 1b keys)"; bad=1
               next }
    { v=($3<=100.5 && ($4=="NA" || $4<=100.5))?"PASS":"FAIL"; if (v=="FAIL") bad=1
      printf "sharing quiet running %s%% solo-expected %s%% outside %s%% %s\n", $3, $4, $5, v }
    END { print (bad ? "RESULT: FAIL" : "RESULT: PASS"); exit bad }
  ' "${SUMMARY}"
  rc=$?
  say "done; log ${LOG}"
  exit "${rc}"
fi

for p in ${PRECISIONS}; do
  say "===== ${p}"
  if [ "${p}" != idle ]; then
    if ! timeout 120 "${LOAD}" start --precision "${p}"; then
      say "FAIL: load start for ${p}"
      echo "${p} load-start-failed" >>"${SUMMARY}"
      continue
    fi
  fi
  sleep "${WARM}"
  wait_new
  snap host_cpu_hw "${KEYS}" >"${WORK}/a"
  snap host_cpu_hw "${SHARE_KEYS}" >"${WORK}/sa"
  g0=$(wc -l <"${GEMM_LOG}" 2>/dev/null || echo 0)
  timeout $((WINDOW + 60)) "${PERF}" stat -a -x, -e "${EVENTS}" -- sleep "${WINDOW}" \
    2>"${WORK}/perf" >/dev/null
  prc=$?
  [ "${prc}" -eq 0 ] || say "WARN: perf exit ${prc}"
  # Mean GEMM GFLOP/s over the lines written during the perf window (one line per ~5 s).
  gflops=NA
  if [ "${p}" != idle ] && [ -r "${GEMM_LOG}" ]; then
    gflops=$(tail -n +"$((g0 + 1))" "${GEMM_LOG}" |
      awk -F'GFLOP/s=' 'NF>1 { split($2,v," "); s+=v[1]; n++ }
        END { if (n) printf "%.6g", s/n; else print "NA" }')
  fi
  wait_new
  snap host_cpu_hw "${KEYS}" >"${WORK}/b"
  snap host_cpu_hw "${SHARE_KEYS}" >"${WORK}/sb"
  rates "${WORK}/a" "${WORK}/b" "${KEYS}" >"${WORK}/m"
  sharing_line "${p}"
  perf_rates "${WORK}/perf" "${WINDOW}" >"${WORK}/p"
  paste -d' ' "${WORK}/m" "${WORK}/p" >"${WORK}/j"
  awk -v p="${p}" -v out="${SUMMARY}" '
    { key=$1; m=$2; dt=$3; n=$4; pr=$6; run=$7
      ratio = (m!="NA" && pr!="NA" && pr>0) ? sprintf("%.3f", m/pr) : "NA"
      printf "  %-15s monitor %-11s perf %-11s mon/perf %-6s running %s%% (%s s, %s CPUs)\n",
        key, m, pr, ratio, run, dt, n
      print p, key, m, pr, ratio, run >> out }
  ' "${WORK}/j"
  if [ "${p}" != idle ]; then
    awk -v p="${p}" -v g="${gflops}" -v out="${SUMMARY}" '
      $1=="arm_fp_" p "_spec" { m=$2 }
      END { ceil = (p=="dp") ? 4 : (p=="sp") ? 8 : 16
            if (g=="NA" || m=="NA" || m<=0) {
              printf "  gemm %s GFLOP/s, flops per FP op NA\n", g
              print p, "gemm", "NA", ceil >> out; exit }
            r = g*1e9/m
            printf "  gemm %s GFLOP/s, flops per FP op %.2f (ceiling %d)\n", g, r, ceil
            print p, "gemm", sprintf("%.3f", r), ceil >> out }
    ' "${WORK}/m"
  fi
  for f in /sys/class/hwmon/hwmon*/power1_oem_info \
    /sys/class/hwmon/hwmon*/device/power1_oem_info; do
    [ -r "${f}" ] || continue
    printf '  power %s: %s W\n' "$(timeout 5 cat "${f}")" \
      "$(timeout 5 awk '{printf "%.1f", $1/1e6}' "${f%oem_info}average")"
  done
  printf '  cur_freq %s MHz\n' "$(awk '{s+=$1;n++} END{printf "%.0f", s/n/1000}' \
    /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq 2>/dev/null)"
  [ "${p}" = idle ] || stop_load
done

say "===== summary"
# A precision's sharing row precedes its gemm row, so run[] is set when the ceiling is judged.
awk -v strict="${FLOPS_MARGIN}" -v wide="${SHARED_FLOPS_MARGIN}" \
  -v shared_pct="${SHARED_RUNNING_PCT}" '
  $2=="instr_retired" && $5!="NA" && $1!="idle" {
    d=$5-1; if (d<0) d=-d; v=(d<=0.05)?"PASS":"FAIL"; if (v=="FAIL") bad=1
    printf "O3 %-4s instr_retired monitor/perf %s %s (limit 5%%)\n", $1, $5, v; seen=1 }
  $2=="gemm" && $3!="NA" {
    shared = ($1 in run) && run[$1] < shared_pct
    low = 100*(1 - $4/$3); v=($3<=$4*(1 + (shared ? wide : strict)))?"PASS":"FAIL"
    if (v=="FAIL") bad=1
    note = shared ? sprintf(" (+%d%% margin, counters shared)", 100*wide) : ""
    if (v=="PASS") printf "accuracy %-2s flops/op %s <= %d%s %s\n", $1, $3, $4, note, v
    else printf "accuracy %-2s flops/op %s > %d%s: monitor FP count at least %.1f%% low %s\n",
      $1, $3, $4, note, low, v
    next }
  $2=="gemm" { print "accuracy " $1 " not measured (no GEMM log lines)"; next }
  $2=="sharing" && $3=="NA" { printf "sharing %-4s not published (daemon without the Phase 1b keys)\n", $1
    next }
  $2=="sharing" {
    run[$1]=$3
    v=($3<=100.5 && ($4=="NA" || $4<=100.5))?"PASS":"FAIL"; if (v=="FAIL") bad=1
    printf "sharing %-4s running %s%% solo-expected %s%% outside %s%% %s\n", $1, $3, $4, $5, v
    next }
  $2!="instr_retired" && $1!="idle" && $3!="NA" { s[$1,$2]=$3 }
  $2=="load-start-failed" { bad=1; print "FAIL: " $1 " load did not start" }
  END {
    split("dp sp hp", ps, " ")
    for (i=1;i<=3;i++) { p=ps[i]; key="arm_fp_" p "_spec"
      if (!((p,key) in s)) continue
      top=s[p,key]; others=0
      for (j=1;j<=3;j++) if (j!=i) others+=s[p,"arm_fp_" ps[j] "_spec"]
      v=(top>0 && others<top/1000)?"PASS":"FAIL"; if (v=="FAIL") bad=1
      printf "slice %-2s %s/s=%s, other precisions %g/s %s\n", p, key, top, others, v }
    if (!seen) { print "FAIL: no instr_retired comparison"; bad=1 }
    print (bad ? "RESULT: FAIL" : "RESULT: PASS") }
' "${SUMMARY}" | tee "${WORK}/result"
say "done; log ${LOG}"
grep -q '^RESULT: PASS' "${WORK}/result"
