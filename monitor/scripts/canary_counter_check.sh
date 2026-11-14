#!/usr/bin/env bash
# shellcheck disable=SC2016 # awk programs are single-quoted on purpose
# Grace counter canary (cpu-precision Phase 1, O3 + precision slices): for each precision it
# starts scripts/canary_gemm_load.sh, measures the monitor's host_cpu_hw rates from the DEBUG
# shm mirror and `perf stat -a` over the same window, then prints monitor/perf ratios and a
# PASS/FAIL line. monitor/perf agreement cannot prove absolute accuracy (both scale by
# enabled/running), so it also divides the GEMM's own GFLOP/s over the same window by the
# monitor FP rate: a 128-bit SVE FMA is at most 4 / 8 / 16 FLOPs per dp / sp / hp operation,
# and a ratio above that ceiling means the scaled count reads low. Run as root on a node with
# the 3.1 debug RPM installed, from the monitor checkout. Nothing here reads the terminal,
# every wait and external command is time-limited, and the GEMM load is stopped on any exit
# (including Ctrl-C).
#
# Usage:
#   ./scripts/canary_counter_check.sh [--precisions "dp sp hp idle"] [--window S] [--warm S]
#
# Output goes to the terminal and to $HOME/hpcperfstats-counter-check/<host>-<epoch>.log.
# Test overrides (env): CANARY_SHM, CANARY_PERF, CANARY_LOAD, CANARY_WAIT_MAX, CANARY_LOG_DIR,
# CANARY_GEMM_LOG.
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
  -h | --help) awk 'NR>2 && /^#/ { sub(/^# ?/, ""); print; next } NR>2 { exit }' "$0"; exit 0 ;;
  *) die "unknown option '$1'" ;;
  esac
done
if ! is_uint "${WINDOW}" || [ "${WINDOW}" -eq 0 ]; then
  die "--window must be a positive integer"
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
exec > >(tee -a "${LOG}") 2>&1

stop_load() { timeout 60 "${LOAD}" stop >/dev/null 2>&1 || true; }
cleanup() {
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

# Per-CPU values of every KEY from the fast payload (fast keys only: skip ,R=S schema tokens).
# Output: "TS <ts>" then "<cpu> <v1> <v2> <v3> <v4>".
snap() {
  timeout 30 awk -v keys="${KEYS}" '
    BEGIN { nk = split(keys, want, " ") }
    FNR==NR { if ($1=="!host_cpu_hw") { n=0
                for (i=2;i<=NF;i++) { if ($i ~ /,R=S/) continue
                  n++; split($i,a,","); for (j=1;j<=nk;j++) if (a[1]==want[j]) col[j]=n } }
              next }
    !h && NF { print "TS", $1; h=1; next }
    $1=="host_cpu_hw" && $3=="@fast" {
      line=$2; for (j=1;j<=nk;j++) line=line " " (col[j] ? $(3+col[j]) : "NA"); print line }
  ' "${SHM}/schema" "${SHM}/fast"
}

# Rates per KEY between two snaps; 48-bit wrap aware. Output: "<key> <rate|NA> <dt> <ncpu>".
rates() {
  awk -v keys="${KEYS}" '
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

SUMMARY="${WORK}/summary"
: >"${SUMMARY}"
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
  snap >"${WORK}/a"
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
  snap >"${WORK}/b"
  rates "${WORK}/a" "${WORK}/b" >"${WORK}/m"
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
awk '
  $2=="instr_retired" && $5!="NA" && $1!="idle" {
    d=$5-1; if (d<0) d=-d; v=(d<=0.05)?"PASS":"FAIL"; if (v=="FAIL") bad=1
    printf "O3 %-4s instr_retired monitor/perf %s %s (limit 5%%)\n", $1, $5, v; seen=1 }
  $2=="gemm" && $3!="NA" {
    low = 100*(1 - $4/$3); v=($3<=$4*1.01)?"PASS":"FAIL"; if (v=="FAIL") bad=1
    if (v=="PASS") printf "accuracy %-2s flops/op %s <= %d %s\n", $1, $3, $4, v
    else printf "accuracy %-2s flops/op %s > %d: monitor FP count at least %.1f%% low %s\n",
      $1, $3, $4, low, v
    next }
  $2=="gemm" { print "accuracy " $1 " not measured (no GEMM log lines)"; next }
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
