#!/usr/bin/env bash
# x86 FLOP-counter verification for cpu-precision Phase 2 (O4 / Groups X and B): checks perf
# FLOP events against a GEMM whose FLOP count is known exactly, before any event code goes into
# the monitor's x86 event table. Run as root on an otherwise idle compute node.
#
# perf only, no MSR access: every event is opened through perf_event_open (perf stat), so the
# kernel programs the counters and reports enabled/running time. An event is either a name from
# the node's perf event list (vendor JSON shipped with perf) or a raw spec such as
# cpu/event=0xNN,umask=0xMM/ taken from vendor docs; codes are verified here, never guessed.
#
# For each precision (dp, sp) the script rebuilds scripts/canary_gemm_load.sh, runs a bounded
# GEMM under `perf stat`, and compares sum(weight x count) with the GEMM's exact FLOP total
# (its "done total_flops=" line):
#   ratio  FLOPs counted by events of that precision (plus "all" events) / GEMM FLOPs,
#          PASS within --tolerance percent of 1.00
#   leak   FLOPs counted by events of the other precision / GEMM FLOPs, PASS below 2%
# perf scales each count by enabled/running time; a running share below 100% means the event
# was multiplexed (other counter users, NMI watchdog), so the result is an estimate.
#
# Usage:
#   ./scripts/perf_raw_flops_check.sh --list
#   ./scripts/perf_raw_flops_check.sh [--precisions "dp sp"] [--seconds S] [--size N]
#        [--threads N] [--tolerance PCT] [--event CLASS-LABEL:WEIGHT:EVENT ...] [--prefix DIR]
#        [--gemm-cflags "FLAGS"]
#
# --list      prints the CPU, the perf version and the FP events this perf knows, with the
#             encodings perf uses (paste this back; it is where raw codes come from)
# --event     replaces the default event set (repeatable). CLASS is dp, sp or all (counts both);
#             WEIGHT is FLOPs per count; EVENT is a perf name or a raw cpu/.../ spec.
#             Example: --event dp-512:8:cpu/event=0xc7,umask=0x40/
# --gemm-cflags  extra compiler flags for the GEMM. GCC keeps 256-bit vectors on Skylake
#             and later, so the 512-bit FLOP events stay at 0 unless the GEMM is built with
#             --gemm-cflags "-mprefer-vector-width=512".
# Defaults: --precisions "dp sp", --seconds 20, --size 1024, --threads = online CPUs,
# --tolerance 10, --prefix ${HPCPERFSTATS_GEMM_PREFIX:-$HOME/hpcperfstats-gemm-load}.
# Default events by vendor (perf names; ones this perf does not know are reported and skipped):
#   GenuineIntel  fp_arith_inst_retired.{scalar,128b_packed,256b_packed,512b_packed}_{double,
#                 single}, weights 1/2/4/8 (dp) and 1/4/8/16 (sp); FMA counts twice in these
#   AuthenticAMD  fp_ret_sse_avx_ops.all, weight 1 (counts FLOPs, total only: Zen3 has no
#                 precision split, D10); add Zen4/Zen5 events with --event after --list
# Exit 0 only on RESULT: PASS; 1 on FAIL; 2 on bad options or missing tools.
# Environment: PERF (perf binary), PERF_RAW_FLOPS_LOAD (load script), CC (GEMM compiler);
# PERF_RAW_FLOPS_CPUINFO (cpuinfo file, tests only).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PERF="${PERF:-perf}"
LOAD="${PERF_RAW_FLOPS_LOAD:-${HERE}/canary_gemm_load.sh}"
PREFIX="${HPCPERFSTATS_GEMM_PREFIX:-${HOME}/hpcperfstats-gemm-load}"
PRECISIONS="dp sp"
SECONDS_RUN=20
SIZE=1024
THREADS=""
TOLERANCE=10
GEMM_CFLAGS=""
LIST=0
EVENTS=()

usage() { sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; }
die() {
  echo "perf_raw_flops_check: $*" >&2
  exit 2
}
is_uint() {
  case "$1" in
  '' | *[!0-9]*) return 1 ;;
  *) return 0 ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
  --list)
    LIST=1
    shift
    ;;
  --precisions | --seconds | --size | --threads | --tolerance | --event | --prefix | --gemm-cflags)
    [ $# -ge 2 ] || die "$1 requires a value"
    case "$1" in
    --precisions) PRECISIONS="$2" ;;
    --seconds) SECONDS_RUN="$2" ;;
    --size) SIZE="$2" ;;
    --threads) THREADS="$2" ;;
    --tolerance) TOLERANCE="$2" ;;
    --event) EVENTS+=("$2") ;;
    --prefix) PREFIX="$2" ;;
    --gemm-cflags) GEMM_CFLAGS="$2" ;;
    esac
    shift 2
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *) die "unknown option '$1'" ;;
  esac
done

command -v "${PERF}" >/dev/null 2>&1 || die "perf not found (set PERF=/path/to/perf)"

CPUINFO="${PERF_RAW_FLOPS_CPUINFO:-/proc/cpuinfo}"
cpu_field() { grep -m1 -E "^$1[[:space:]]*:" "${CPUINFO}" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//'; }
VENDOR="$(cpu_field vendor_id)"

if [ "${LIST}" = 1 ]; then
  echo "host $(hostname -f 2>/dev/null || hostname)"
  echo "cpu ${VENDOR:-unknown} family $(cpu_field 'cpu family') model $(cpu_field model): $(cpu_field 'model name')"
  echo "perf $("${PERF}" --version 2>&1 | head -n 1)"
  echo "pmu_name $(cat /sys/bus/event_source/devices/cpu/caps/pmu_name 2>/dev/null || echo none)"
  echo "===== FP events known to this perf (name, description, encoding)"
  timeout 60 "${PERF}" list --details 2>/dev/null |
    grep -i -A4 -E '^[[:space:]]*(fp_|fp[0-9]|.*flop|.*sse_avx|.*fp_arith|.*fp_ops|.*fpu_pipe)' |
    head -n 300 || true
  exit 0
fi

for p in ${PRECISIONS}; do
  case "${p}" in dp | sp) ;; *) die "--precisions takes dp and/or sp (got '${p}')" ;; esac
done
[ -n "${PRECISIONS// /}" ] || die "--precisions is empty"
{ is_uint "${SECONDS_RUN}" && [ "${SECONDS_RUN}" -ge 1 ] && [ "${SECONDS_RUN}" -le 3600 ]; } ||
  die "--seconds must be an integer in 1..3600"
{ is_uint "${SIZE}" && [ "${SIZE}" -ge 16 ] && [ "${SIZE}" -le 8192 ]; } ||
  die "--size must be an integer in 16..8192"
[ -z "${THREADS}" ] || { is_uint "${THREADS}" && [ "${THREADS}" -gt 0 ]; } ||
  die "--threads must be a positive integer"
{ is_uint "${TOLERANCE}" && [ "${TOLERANCE}" -ge 1 ] && [ "${TOLERANCE}" -le 50 ]; } ||
  die "--tolerance must be an integer percent in 1..50"
[ -n "${THREADS}" ] || THREADS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
[ -x "${LOAD}" ] || die "load script not executable: ${LOAD}"

if [ "${#EVENTS[@]}" -eq 0 ]; then
  case "${VENDOR}" in
  GenuineIntel)
    for w in scalar:1:1 128b_packed:2:4 256b_packed:4:8 512b_packed:8:16; do
      IFS=: read -r width wd ws <<<"${w}"
      EVENTS+=("dp-${width}:${wd}:fp_arith_inst_retired.${width}_double")
      EVENTS+=("sp-${width}:${ws}:fp_arith_inst_retired.${width}_single")
    done
    ;;
  AuthenticAMD) EVENTS+=("all-flops:1:fp_ret_sse_avx_ops.all") ;;
  *) die "no default events for vendor '${VENDOR:-unknown}'; pass --event (see --list)" ;;
  esac
fi

# Validate every --event spec and keep the ones this perf can open (a 0.1 s probe each).
LABELS=()
WEIGHTS=()
SPECS=()
for e in "${EVENTS[@]}"; do
  label="${e%%:*}"
  rest="${e#*:}"
  weight="${rest%%:*}"
  spec="${rest#*:}"
  if [ "${label}" = "${e}" ] || [ "${weight}" = "${rest}" ] || [ -z "${spec}" ]; then
    die "--event '${e}' is not CLASS-LABEL:WEIGHT:EVENT"
  fi
  case "${label}" in
  *[[:space:]]*) die "--event label '${label}' has a space" ;;
  dp-?* | sp-?* | all-?*) ;;
  *) die "--event label '${label}' must start with dp-, sp- or all-" ;;
  esac
  if ! is_uint "${weight}" || [ "${weight}" -lt 1 ]; then
    die "--event '${e}': WEIGHT must be a positive integer"
  fi
  # perf exits 0 for <not supported> / <not counted>, so require a numeric count and show
  # perf's own line when there is none.
  probe="$(timeout 20 "${PERF}" stat -x ';' -e "${spec}" -- sleep 0.1 2>&1 >/dev/null |
    grep -v -e '^#' -e '^[[:space:]]*$' || true)"
  if echo "${probe}" | awk -F';' 'NF >= 3 && $1 ~ /^[0-9.]+$/ { ok = 1 } END { exit !ok }'; then
    LABELS+=("${label}")
    WEIGHTS+=("${weight}")
    SPECS+=("${spec}")
  else
    echo "event ${label} (${spec}): not usable with this perf/kernel, skipped; perf: $(echo "${probe:-no output}" | head -n 1)"
  fi
done
[ "${#SPECS[@]}" -gt 0 ] || {
  echo "RESULT: FAIL (no usable events; run --list and pass --event)"
  exit 1
}

OUTDIR="${PREFIX}/perf_raw_flops"
mkdir -p "${OUTDIR}"
"${LOAD}" install --prefix "${PREFIX}" --cflags "${GEMM_CFLAGS}" >"${OUTDIR}/install.log" 2>&1 || {
  cat "${OUTDIR}/install.log" >&2
  die "GEMM load install failed (module load gcc?)"
}

echo "host $(hostname -f 2>/dev/null || hostname), cpu ${VENDOR} family $(cpu_field 'cpu family') model $(cpu_field model)"
echo "events: ${#SPECS[@]} (perf scales multiplexed counts by enabled/running)"
echo "gemm extra cflags: ${GEMM_CFLAGS:-none}"
SUMMARY="${OUTDIR}/summary.txt"
: >"${SUMMARY}"
perf_args=()
for s in "${SPECS[@]}"; do perf_args+=(-e "${s}"); done

for p in ${PRECISIONS}; do
  out="${OUTDIR}/perf_${p}.txt"
  log="${OUTDIR}/gemm_${p}.log"
  echo "===== ${p} (${SECONDS_RUN} s, n=${SIZE}, ${THREADS} threads)"
  rm -f "${out}"
  set +e
  timeout $((SECONDS_RUN + 120)) "${PERF}" stat -x ';' -o "${out}" "${perf_args[@]}" -- \
    "${LOAD}" run --prefix "${PREFIX}" --precision "${p}" --seconds "${SECONDS_RUN}" \
    --size "${SIZE}" --threads "${THREADS}" >"${log}" 2>&1
  rc=$?
  set -e
  total="$(sed -n 's/.* done total_flops=\([0-9][0-9]*\) .*/\1/p' "${log}" | tail -n 1)"
  if [ "${rc}" -ne 0 ] || [ -z "${total}" ] || [ ! -s "${out}" ]; then
    echo "  FAIL: perf/GEMM run exit ${rc}; GEMM total ${total:-missing}; see ${log} ${out}"
    echo "${p} run-failed" >>"${SUMMARY}"
    continue
  fi
  # Counter lines in -e order: count;unit;event;run_time;pcnt_running;...
  grep -v -e '^#' -e '^[[:space:]]*$' "${out}" |
    awk -F';' -v p="${p}" -v total="${total}" -v tol="${TOLERANCE}" \
      -v labels="${LABELS[*]}" -v weights="${WEIGHTS[*]}" -v sum="${SUMMARY}" '
      BEGIN { n = split(labels, L, " "); split(weights, W, " ");
              other = (p == "dp") ? "sp" : "dp"; i = 0 }
      i < n { i++; cls = substr(L[i], 1, index(L[i], "-") - 1)
              run = ($5 == "" ? "NA" : $5 "%")
              if ($1 !~ /^[0-9.]+$/) { printf "  event %-16s %s\n", L[i], $1; next }
              f = $1 * W[i]
              printf "  event %-16s weight %2d  count %.4g  running %s  FLOPs/GEMM %.4f\n", L[i], W[i], $1, run, f / total
              if (cls == p || cls == "all") { main += f; nmain++ }
              if (cls == other) { leak += f; nleak++ } }
      END {
        printf "  gemm  %.4g FLOPs (exact)\n", total
        if (nmain == 0) { printf "  FAIL: no counted %s or all event\n", p; print p, "no-events" >> sum; exit }
        r = main / total; ok = (r >= 1 - tol / 100 && r <= 1 + tol / 100)
        printf "  ratio %.3f  %s (counted %s+all FLOPs / GEMM FLOPs, within %d%%)\n", r, ok ? "PASS" : "FAIL", p, tol
        if (main == 0) print "  every counted event read 0: wrong code, or a virtualized PMU that does not count"
        print p, "ratio", sprintf("%.3f", r), ok ? "PASS" : "FAIL" >> sum
        if (nleak > 0) { l = leak / total; lk = (l < 0.02)
          printf "  leak  %.3f  %s (counted %s FLOPs / GEMM FLOPs, below 0.02)\n", l, lk ? "PASS" : "FAIL", other
          print p, "leak", sprintf("%.3f", l), lk ? "PASS" : "FAIL" >> sum }
      }'
done

echo "===== summary"
awk '$2 == "run-failed" || $2 == "no-events" { bad = 1; print "FAIL: " $1 " " $2; next }
     { print $1 " " $2 " " $3 " " $4; if ($4 != "PASS") bad = 1 }
     END { print (NR == 0 || bad) ? "RESULT: FAIL" : "RESULT: PASS"; exit (NR == 0 || bad) ? 1 : 0 }' "${SUMMARY}"
