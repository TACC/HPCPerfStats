#!/usr/bin/env bash
# shellcheck disable=SC2016 # single-quoted bash -c programs take their values as "$@"
# Regression (via check-local): the counter-accuracy layer of rpm_debug_shm_verify.sh
# (run_counter_accuracy -> canary_counter_check.sh --accuracy -> scripts/lib/counter_accuracy.py)
# against a fake node: a shm writer with the real payload layout for Intel, AMD and Grace
# (blank first line, @fast rows, an R=S key the fast rows skip), a fake powercap tree, a fake
# `perf stat -a` that counts until SIGINT, and a fake load whose exact totals come from the
# writer's own truth counters (FAKE_*_PCT skews them). Locks: memory / FLOP / instruction /
# energy PASS and FAIL per vendor shape, NOT_RUN for a missing source (no perf, no powercap, no
# PMU, no write kernel), COUNTER_CHECK=0 and release builds SKIP, a stale daemon FAILs within
# the bound, Ctrl-C exits 130 and kills the load, the extra canary checks map exit codes, and the
# campaign JSON exits non-zero on any FAIL. Also runs the two Python unit tests.
set -euo pipefail
# check-local runs this as /bin/sh; EL8 bash 4.4 in posix mode keeps `VAR=x fn` after fn
# returns, so the FAKE_* / COUNTER_CHECK prefixes below would leak into later calls.
set +o posix
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERIFY="${ROOT}/scripts/rpm_debug_shm_verify.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/hps-rpm-counter-check.XXXXXX")"
WPID=""
stop_writer() {
  [ -z "${WPID}" ] || { kill "${WPID}" 2>/dev/null || true; wait "${WPID}" 2>/dev/null || true; }
  WPID=""
}
cleanup() {
  stop_writer
  [ ! -f "${T}/load.pid" ] || kill "$(cat "${T}/load.pid")" 2>/dev/null || true
  rm -rf "${T}"
}
trap cleanup EXIT
fail() {
  echo "test_rpm_debug_counter_check: $*" >&2
  exit 1
}
if ! command -v timeout >/dev/null 2>&1 || ! command -v taskset >/dev/null 2>&1; then
  echo "SKIP: no timeout(1) or taskset(1)"
  exit 77
fi
VENV_PY="$(cd "${ROOT}/../.." && pwd)/.venv/bin/python3"
PY="python3"
[ ! -x "${VENV_PY}" ] || PY="${VENV_PY}"

mkdir -p "${T}/shm" "${T}/truth" "${T}/reports" "${T}/nopc" "${T}/sys/cpu0/topology" \
  "${T}/sys/cpu1/topology"
echo 0 >"${T}/sys/cpu0/topology/physical_package_id"
echo 1 >"${T}/sys/cpu1/topology/physical_package_id"
printf '{"capability_slug":"fake-slug","configure":{"debug":true}}\n' >"${T}/caps.json"
printf '{"capability_slug":"fake-slug","configure":{"debug":false}}\n' >"${T}/caps-release.json"

# Fake daemon: one tick per second. Under a load mode it advances the counters that load moves
# and adds the same amount to truth/<mode> (bytes, FLOPs) and truth/instr, which the fake load
# and the fake perf report back. Energy advances every tick (powercap written before fast).
cat >"${T}/writer.sh" <<'EOF'
#!/usr/bin/env bash
D=$1; A=$2; T=$(dirname "$D"); ts=1000
MAX=262143328850
rd=0; wr=0; hr=0; s1=0; fd=0; fs=0; f2=0; f5=0; ins=0; pkg=4294960000; dram=4294967000
pcp=262143000000; pcd=262143300000
tr_read=0; tr_write=0; tr_dp=0; tr_sp=0; tr_instr=0
pct=${FAKE_PC_PCT:-100}
case $A in
intel)
  hw='!host_cpu_hw instr_retired_any,E,W=48 instr_retired,E,W=48 cpu_util_total_accum_us,E,W=64,R=S'
  for w in scalar 128b_packed 256b_packed 512b_packed; do
    hw="$hw fp_arith_inst_retired_${w}_double,E,W=48"
  done
  for w in scalar 128b_packed 256b_packed 512b_packed; do
    hw="$hw fp_arith_inst_retired_${w}_single,E,W=48"
  done
  mem='!intel_x86_uncore_imc_spr dram_cas_reads,E,W=48 dram_cas_writes,E,W=48 hbm_cas_reads,E,W=48 hbm_cas_writes,E,W=48'
  rapl='!intel_x86_rapl pkg_energy,E,W=32,U=mJ pp0_energy,E,W=32,U=mJ pp1_energy,E,W=32,U=mJ dram_energy,E,W=32,U=mJ' ;;
amd)
  hw='!host_cpu_hw instr_retired,E,W=48 cpu_util_total_accum_us,E,W=64,R=S fp_ops_retired,E,W=64,U=FLOP amd_fp_dp_ops,E,W=64,U=FLOP'
  mem='!amd_x86_uncore_df_turin dram_data_beats,E,W=48 dram_read_beats,E,W=48 dram_write_beats,E,W=48'
  rapl='!amd_x86_rapl core_energy,E,W=32,U=mJ pkg_energy,E,W=32,U=mJ' ;;
grace)
  hw='!host_cpu_hw instr_retired,E,W=48 cpu_util_total_accum_us,E,W=64,R=S arm_fp_dp_spec,E,W=64 arm_fp_sp_spec,E,W=64 arm_fp_hp_spec,E,W=64'
  mem='!arm_aarch64_imc dram_cas_reads,E,W=48 dram_cas_writes,E,W=48'
  rapl='' ;;
esac
printf '$x\n%s\n%s\n%s\n' "$hw" "$mem" "$rapl" >"$D/schema.tmp" && mv "$D/schema.tmp" "$D/schema"
zone() { # zone NAME LABEL UJ
  mkdir -p "$T/pc/$1"
  echo "$2" >"$T/pc/$1/name"
  echo "$MAX" >"$T/pc/$1/max_energy_range_uj"
  echo "$3" >"$T/pc/$1/energy_uj.tmp" && mv "$T/pc/$1/energy_uj.tmp" "$T/pc/$1/energy_uj"
}
put() { echo "$2" >"$T/truth/$1.tmp" && mv "$T/truth/$1.tmp" "$T/truth/$1"; }
while :; do
  m=$(cat "$T/mode" 2>/dev/null || echo idle)
  case $m in
  read)
    rd=$((rd + 1000000)); hr=$((hr + 1000000)); s1=$((s1 + 1000000 * ${FAKE_SCF1:-0}))
    case $A in
    intel) tr_read=$((tr_read + 2 * 64000000 + 32000000)) ;;
    amd) tr_read=$((tr_read + 4 * 64000000)) ;;
    grace) tr_read=$((tr_read + 64000000 + 64000000 * ${FAKE_SCF1:-0})) ;;
    esac ;;
  write)
    wr=$((wr + 1000000))
    case $A in
    intel) tr_write=$((tr_write + 128000000)) ;;
    amd) tr_write=$((tr_write + 4 * 64000000)) ;;
    *) tr_write=$((tr_write + 64000000)) ;;
    esac ;;
  dp)
    fd=$((fd + 1000)); f2=$((f2 + 100)); ins=$((ins + 50000)); tr_instr=$((tr_instr + 100000))
    case $A in intel) tr_dp=$((tr_dp + 8200)) ;; amd) tr_dp=$((tr_dp + 12000)) ;; grace) tr_dp=$((tr_dp + 8000)) ;; esac ;;
  sp)
    fs=$((fs + 1000)); f5=$((f5 + 10)); ins=$((ins + 50000)); tr_instr=$((tr_instr + 100000))
    case $A in intel) tr_sp=$((tr_sp + 16320)) ;; amd) tr_sp=$((tr_sp + 12000)) ;; grace) tr_sp=$((tr_sp + 16000)) ;; esac ;;
  esac
  put read "$tr_read"; put write "$tr_write"; put dp "$tr_dp"; put sp "$tr_sp"; put instr "$tr_instr"
  pkg=$(((pkg + 1500) % 4294967296)); dram=$(((dram + 300) % 4294967296))
  pcp=$(((pcp + 1500000 * pct / 100) % (MAX + 1))); pcd=$(((pcd + 300000 * pct / 100) % (MAX + 1)))
  if [ "$A" != grace ]; then
    zone intel-rapl:0 package-0 "$pcp"; zone intel-rapl:1 package-1 "$pcp"
    zone intel-rapl-mmio:0 package-0 1
    if [ "$A" = intel ]; then zone intel-rapl:0:0 dram "$pcd"; zone intel-rapl:1:0 dram "$pcd"; fi
  fi
  ts=$((ts + 1))
  {
    printf '\n%d.000000 1234 fakehost\n' "$ts"
    for c in 0 1; do
      case $A in
      intel)
        if [ "${FAKE_NO_PMU:-0}" = 1 ]; then
          echo "host_cpu_hw $c @fast -2 -2 -2 -2 -2 -2 -2 -2 -2 -2"
        else
          echo "host_cpu_hw $c @fast $ins $ins $f2 0 $fd 0 0 0 $fs $f5"
        fi ;;
      amd) echo "host_cpu_hw $c @fast $ins $((fd * 6 + fs * 6)) $((fd * 6))" ;;
      grace) echo "host_cpu_hw $c @fast $ins $fd $fs 0" ;;
      esac
    done
    case $A in
    intel)
      echo "intel_x86_uncore_imc_spr mbox0 @fast $rd $wr 0 0"
      echo "intel_x86_uncore_imc_spr mbox1 @fast $rd $wr 0 0"
      echo "intel_x86_uncore_imc_spr hbm0 @fast 0 0 $hr 0"
      for s in 0 1; do echo "intel_x86_rapl $s @fast $pkg 0 0 $dram"; done ;;
    amd)
      # Turin: chan<N> rows split read / write beats; channels 4-11 have no verified code.
      for n in 0 1 2 3; do echo "amd_x86_uncore_df_turin chan$n @fast -2 $rd $wr"; done
      for n in 4 5 6 7 8 9 10 11; do echo "amd_x86_uncore_df_turin chan$n @fast -2 -10 -10"; done
      for s in 0 1; do echo "amd_x86_rapl $s @fast 0 $pkg"; done ;;
    grace)
      echo "arm_aarch64_imc scf0 @fast $rd $wr"
      echo "arm_aarch64_imc scf1 @fast $s1 0" ;;
    esac
  } >"$D/fast.tmp" && mv "$D/fast.tmp" "$D/fast"
  sleep 1
done
EOF
# Fake load (canary_gemm_load.sh interface): runs its mode for --seconds, then reports the
# writer's truth delta as the exact total (FAKE_MEM_PCT / FAKE_FLOP_PCT skew it).
cat >"${T}/load" <<'EOF'
#!/usr/bin/env bash
T=$(dirname "$0")
[ "$1" = stop ] && { echo idle >"$T/mode"; exit 0; }
shift
kernel=gemm; prec=dp; secs=1
while [ $# -gt 0 ]; do
  case $1 in --kernel) kernel=$2 ;; --precision) prec=$2 ;; --seconds) secs=$2 ;; esac
  shift 2
done
if [ "$kernel" = write ] && [ "${FAKE_NO_WRITE:-0}" = 1 ]; then
  echo "canary_gemm_load: --kernel must be gemm or read (got 'write')" >&2
  exit 2
fi
mode=$kernel; [ "$kernel" = gemm ] && mode=$prec
echo $$ >"$T/load.pid"
before=$(cat "$T/truth/$mode")
echo "$mode" >"$T/mode"
sleep "$secs"
echo idle >"$T/mode"
sleep 1.3
d=$(($(cat "$T/truth/$mode") - before))
case $kernel in
read) echo "read_load done total_bytes_read=$((d * 100 / ${FAKE_MEM_PCT:-100})) passes=1 elapsed=${secs}.0s" ;;
write) echo "write_load done total_bytes_written=$((d * 100 / ${FAKE_MEM_PCT:-100})) passes=1 elapsed=${secs}.0s" ;;
*) echo "gemm_load $prec done total_flops=$((d * ${FAKE_FLOP_PCT:-100} / 100)) elapsed=${secs}.000s" ;;
esac
rm -f "$T/load.pid"
EOF
# Fake `perf stat -a -x, -o FILE -e instructions`: counts truth/instr until SIGINT.
cat >"${T}/perf" <<'EOF'
#!/usr/bin/env bash
T=$(dirname "$0"); out=/dev/null
while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done
s=$(cat "$T/truth/instr")
trap 'echo "$((($(cat "$T/truth/instr") - s) * ${FAKE_PERF_PCT:-100} / 100)),,instructions,100,100.00,," >"$out"; exit 0' INT
while :; do sleep 0.1; done
EOF
chmod +x "${T}/writer.sh" "${T}/load" "${T}/perf"
printf 'vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 143\n' >"${T}/cpuinfo.intel"
printf 'vendor_id\t: AuthenticAMD\ncpu family\t: 26\nmodel\t\t: 2\n' >"${T}/cpuinfo.amd"
printf 'processor\t: 0\nCPU implementer\t: 0x41\nCPU part\t: 0xd4f\n' >"${T}/cpuinfo.grace"
echo sapphire_rapids >"${T}/pmu_name"

export CANARY_PERF="${T}/perf" CANARY_LOAD="${T}/load" CANARY_SYS_CPU="${T}/sys"
export CANARY_PMU_NAME="${T}/pmu_name" CANARY_WAIT_MAX=5 CANARY_LOAD_GRACE=30
export COUNTER_CHECK_SECONDS=1 HOME="${T}"

start_writer() { # start_writer ARCH
  stop_writer
  echo idle >"${T}/mode"
  rm -rf "${T}/pc" "${T}/shm"/*
  mkdir -p "${T}/pc"
  "${T}/writer.sh" "${T}/shm" "$1" &
  WPID=$!
  for _ in $(seq 20); do [ -s "${T}/shm/fast" ] && break; sleep 0.25; done
  export CANARY_CPUINFO="${T}/cpuinfo.$1" CANARY_POWERCAP="${T}/pc"
}
# accuracy NAME [CAPS]: run the layer through rpm_debug_shm_verify.sh's function.
accuracy() {
  local caps="${2:-${T}/caps.json}"
  RES="${T}/$1.tsv"
  : >"${RES}"
  set +e
  out="$(cd "${T}" && timeout 300 bash -c '. "$1"; run_counter_accuracy "$2" "$3" "$4" "$5" "$6"' \
    _ "${VERIFY}" "${caps}" "${T}/shm" "${PY}" "${RES}" "${T}/reports" 2>&1)"
  rc=$?
  set -e
  [ "${rc}" -ne 124 ] || fail "$1: hung: ${out}"
  [ "$(cat "${T}/mode")" = idle ] || fail "$1: load still running"
}
# want NAME STATUS: last status for NAME in the current results file.
want() {
  local got
  got="$(awk -F'\t' -v n="$1" '$1==n { s=$2 } END { print s }' "${RES}")"
  [ "${got}" = "$2" ] || fail "${RES##*/}: $1 is '${got}', want $2: $(cat "${RES}") ${out}"
}

# --- Intel: everything PASS, socket share NOT_RUN (x86 rows sum all sockets) ---
start_writer intel
accuracy intel-pass
for c in mem_read mem_write flops_dp flops_sp instructions energy_pkg energy_dram; do
  want "${c}" PASS
done
want mem_socket0 NOT_RUN
grep -q '^RESULT: PASS' <<<"${out}" || fail "intel-pass: no RESULT: PASS: ${out}"
ls "${T}/reports"/*.log >/dev/null 2>&1 || fail "canary log not written to the report dir"

# Campaign JSON: preflight + clean validate report + the PASS results -> exit 0.
printf 'preflight\tPASS\t-\tok\nfault_inject\tPASS\t-\tok\n' >>"${RES}"
printf 'PASS schema\nPASS sentinel banner\nPASS plausibility\nPASS cross_sample x\n' >"${T}/val.txt"
set +e
sout="$(bash -c '. "$1"; write_correctness_summary "$2" "$3" "$4" "$5" "$6" "$7"' _ "${VERIFY}" \
  "${PY}" "${RES}" "${T}/reports" "${T}/caps.json" "${T}/val.txt" 0 2>&1)"
src=$?
set -e
[ "${src}" -eq 0 ] || fail "summary of a passing run exit ${src}: ${sout}"
json="$(ls "${T}/reports"/correctness_*_fake-slug_*.json)" || fail "no campaign JSON: ${sout}"
"${PY}" - "${json}" <<'PY' || fail "campaign JSON content"
import json, sys
s = json.load(open(sys.argv[1]))
assert s["schema_version"] == 1 and s["overall"] == "PASS", s
assert s["capability_slug"] == "fake-slug" and s["debug_build"] is True
checks = {c["name"]: c for c in s["checks"]}
assert checks["flops_dp"]["status"] == "PASS" and abs(checks["flops_dp"]["ratio"] - 1) < 1e-9
assert checks["golden"]["status"] == "SKIP", checks["golden"]
PY
rm -f "${json}"

# --- Intel FAIL: memory 2.5x low, FLOPs 0.8, instructions 0.83, energy 0.91 ---
stop_writer
FAKE_PC_PCT=110 start_writer intel
FAKE_MEM_PCT=40 FAKE_FLOP_PCT=125 FAKE_PERF_PCT=120 accuracy intel-fail
for c in mem_read mem_write flops_dp flops_sp instructions energy_pkg energy_dram; do
  want "${c}" FAIL
done
grep -q '^RESULT: FAIL' <<<"${out}" || fail "intel-fail: no RESULT: FAIL"
printf 'preflight\tPASS\t-\tok\n' >>"${RES}"
set +e
sout="$(bash -c '. "$1"; write_correctness_summary "$2" "$3" "$4" "$5" "$6" "$7"' _ "${VERIFY}" \
  "${PY}" "${RES}" "${T}/reports" "${T}/caps.json" "${T}/val.txt" 0 2>&1)"
src=$?
set -e
[ "${src}" -eq 1 ] || fail "summary with FAILs exit ${src}, want 1: ${sout}"
grep -q 'overall=FAIL' <<<"${sout}" || fail "summary did not say overall=FAIL: ${sout}"

# --- AMD (Turin): DF chan<N> rows split read / write beats, both PASS; no DRAM RAPL ---
start_writer amd
accuracy amd-pass
for c in mem_read mem_write flops_dp flops_sp instructions energy_pkg; do want "${c}" PASS; done
for c in mem_socket0 energy_dram; do want "${c}" NOT_RUN; done
stop_writer
FAKE_PC_PCT=110 start_writer amd
FAKE_MEM_PCT=40 FAKE_FLOP_PCT=125 accuracy amd-fail
for c in mem_read flops_dp flops_sp energy_pkg; do want "${c}" FAIL; done

# --- Grace: SCF read / write and scf0 share PASS, flops per op at the ceiling, no RAPL ---
start_writer grace
accuracy grace-pass
for c in mem_read mem_write mem_socket0 flops_dp flops_sp instructions; do want "${c}" PASS; done
for c in energy_pkg energy_dram; do want "${c}" NOT_RUN; done
stop_writer
FAKE_SCF1=1 start_writer grace
FAKE_SCF1=1 FAKE_FLOP_PCT=125 FAKE_PERF_PCT=120 accuracy grace-fail
want mem_read PASS
for c in mem_socket0 flops_dp flops_sp instructions; do want "${c}" FAIL; done

# --- Missing sources: no perf, no powercap, no PMU, no write kernel -> NOT_RUN, never PASS ---
FAKE_NO_PMU=1 start_writer intel
CANARY_PERF="${T}/no-such-perf" CANARY_POWERCAP="${T}/nopc" FAKE_NO_PMU=1 FAKE_NO_WRITE=1 \
  accuracy notrun
want mem_read PASS
for c in mem_write flops_dp flops_sp instructions energy_pkg energy_dram; do
  want "${c}" NOT_RUN
done
grep -q 'emitted sentinel -2' "${RES}" || fail "no-PMU reason missing: $(cat "${RES}")"
grep -q "got 'write'" "${RES}" || fail "write-kernel reason missing: $(cat "${RES}")"

# --- Opt-out and release builds SKIP without touching the node ---
COUNTER_CHECK=0 accuracy optout
grep -q 'SKIP: counter accuracy (COUNTER_CHECK=0)' <<<"${out}" || fail "no opt-out SKIP: ${out}"
for c in mem_read flops_dp energy_pkg; do want "${c}" SKIP; done
accuracy release "${T}/caps-release.json"
grep -q 'SKIP: counter accuracy (release build' <<<"${out}" || fail "no release SKIP: ${out}"
want instructions SKIP

# --- Stale daemon: bounded wait, FAIL ---
stop_writer
CANARY_WAIT_MAX=1 accuracy stale
want mem_read FAIL
want flops_dp FAIL
grep -q 'did not advance' "${RES}" || fail "stale reason missing"

# --- Ctrl-C: exit 130 and the running load process is gone ---
start_writer intel
(cd "${T}" && CANARY_SHM="${T}/shm" CANARY_PY="${PY}" CANARY_LOG_DIR="${T}/reports" \
  exec timeout 120 "${ROOT}/scripts/canary_counter_check.sh" --accuracy --seconds 30 \
  --results "${T}/int.tsv") >"${T}/int.out" 2>&1 &
cpid=$!
for _ in $(seq 40); do [ -s "${T}/load.pid" ] && break; sleep 0.25; done
[ -s "${T}/load.pid" ] || fail "load never started before SIGINT: $(cat "${T}/int.out")"
lpid="$(cat "${T}/load.pid")"
kill -INT "${cpid}"
set +e
wait "${cpid}"
rc=$?
set -e
[ "${rc}" -eq 130 ] || fail "SIGINT exit ${rc}, want 130: $(cat "${T}/int.out")"
for _ in $(seq 20); do kill -0 "${lpid}" 2>/dev/null || break; sleep 0.25; done
if kill -0 "${lpid}" 2>/dev/null; then fail "load ${lpid} still running after SIGINT"; fi
grep -q '^interrupted$' "${T}/int.out" || fail "no interrupted line: $(cat "${T}/int.out")"
stop_writer

# --- Extra wired checks map exit codes: 0 PASS, 1 FAIL, 2 NOT_RUN, off SKIP, n/a NOT_RUN ---
RES="${T}/extra.tsv"
: >"${RES}"
bash -c '. "$1"; r=$2
  run_extra_check "$r" perf_raw_flops PERF_RAW_FLOPS_CHECK 1 x -- sh -c "echo RESULT: PASS"
  run_extra_check "$r" scf_perf SCF_PERF_CHECK 1 x -- sh -c "echo RESULT: FAIL; exit 1"
  run_extra_check "$r" pmu_sharing PMU_SHARING_CHECK 1 x -- sh -c "echo canary: no perf >&2; exit 2"
  FP_SLICES_CHECK=0 run_extra_check "$r" fp_slices FP_SLICES_CHECK 1 x -- true
  run_extra_check "$r" fault_inject FAULT_INJECT 0 "Grace only" -- true' \
  _ "${VERIFY}" "${RES}" >"${T}/extra.out" 2>&1 || fail "run_extra_check: $(cat "${T}/extra.out")"
want perf_raw_flops PASS
want scf_perf FAIL
want pmu_sharing NOT_RUN
want fp_slices SKIP
want fault_inject NOT_RUN
grep -q 'no perf' "${RES}" || fail "NOT_RUN reason lost: $(cat "${RES}")"

# --- fault_inject_verify.sh output -> status: the checker's NOT_RUN line (no planned fault
# point reached on the node) is NOT_RUN, not PASS ---
for c in "PASS 0 fault_inject: PASS (0 failure(s))" "FAIL 1 fault_inject: FAIL (1 failure(s))" \
  "NOT_RUN 0 fault_inject: NOT_RUN (no planned fault point was reached on this node)" \
  "SKIP 0 SKIP: fault injection (FAULT_INJECT=0)"; do
  RES="${T}/fi.tsv"
  : >"${RES}"
  printf '%s\n' "${c#* * }" >"${T}/fi.out"
  bash -c '. "$1"; record_fault_inject "$2" "$3" "$4"' _ "${VERIFY}" "${RES}" "${T}/fi.out" \
    "$(echo "${c}" | cut -d' ' -f2)" >"${T}/fi.log" 2>&1 || fail "record_fault_inject: $(cat "${T}/fi.log")"
  want fault_inject "${c%% *}"
done

"${PY}" "${ROOT}/tests/test_counter_accuracy_lib.py" >"${T}/py.log" 2>&1 ||
  fail "test_counter_accuracy_lib.py: $(tail -n 20 "${T}/py.log")"
"${PY}" "${ROOT}/tests/test_correctness_summary.py" >"${T}/py.log" 2>&1 ||
  fail "test_correctness_summary.py: $(tail -n 20 "${T}/py.log")"
echo "test_rpm_debug_counter_check: OK"
