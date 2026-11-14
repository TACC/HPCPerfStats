#!/usr/bin/env bash
# Regression (via check-local): scripts/canary_counter_check.sh against a fake node (shm writer
# with the real payload layout, i.e. a blank first line and @fast rows that carry fast keys
# only; fake perf that also writes GEMM log lines; fake load). Locks: monitor/perf rates,
# precision slices and the GEMM flops-per-op accuracy check PASS; a count that reads low
# against the GEMM FAILs; a stale fast file ends with FAIL instead of hanging; Ctrl-C exits 130
# and stops the load; a perf "<not counted>" event keeps its slot; bad options exit 2.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/scripts/canary_counter_check.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/hps-counter-check-test.XXXXXX")"
WPID=""
cleanup() {
  [ -z "${WPID}" ] || kill "${WPID}" 2>/dev/null || true
  rm -rf "${T}"
}
trap cleanup EXIT
fail() {
  echo "test_canary_counter_check: $*" >&2
  exit 1
}
test -x "${SCRIPT}" || fail "missing or not executable: ${SCRIPT}"
command -v timeout >/dev/null 2>&1 || {
  echo "SKIP: no timeout(1)"
  exit 77
}
mkdir -p "${T}/shm" "${T}/log"
echo idle >"${T}/mode"
: >"${T}/gemm.log"

# Fake daemon: rewrites fast every second; per CPU, instr +1000/s and the mode's FP key
# +100/s (2 CPUs: 2000 and 200 per second); per SCF socket under dp load, dram_cas_reads
# +2e7/s and dram_cas_writes +1e7/s (64-byte transfers: 2 sockets = 2.56 / 1.28 GB/s).
cat >"${T}/writer.sh" <<'EOF'
#!/usr/bin/env bash
D=$1; t=1000; i=0; d=0; s=0; h=0; r=0; w=0
{ printf '$x\n!host_cpu_hw cpu_util_total_accum_us,E,R=S instr_retired,E,W=48 arm_fp_dp_spec,E,W=48 arm_fp_sp_spec,E,W=48 arm_fp_hp_spec,E,W=48\n'
  printf '!arm_aarch64_imc dram_cas_reads,E,W=48 dram_cas_writes,E,W=48\n'; } >"$D/schema"
while :; do
  m=$(cat "$D/../mode" 2>/dev/null || echo idle)
  i=$((i + 1000))
  case $m in dp) d=$((d + 100)); r=$((r + 20000000)); w=$((w + 10000000)) ;;
    sp) s=$((s + 100)) ;; hp) h=$((h + 100)) ;; esac
  t=$((t + 1))
  { printf '\n%d.000000 job host\n' "$t"
    printf 'host_cpu_hw 0 @fast %d %d %d %d\n' "$i" "$d" "$s" "$h"
    printf 'host_cpu_hw 1 @fast %d %d %d %d\n' "$i" "$d" "$s" "$h"
    printf 'arm_aarch64_imc scf0 @fast %d %d\n' "$r" "$w"
    printf 'arm_aarch64_imc scf1 @fast %d %d\n' "$r" "$w"
  } >"$D/fast.tmp" && mv "$D/fast.tmp" "$D/fast"
  sleep 1
done
EOF
# Fake perf stat: instructions 2000/s, the mode's FP event 200/s; appends one GEMM log line
# per second of the window (FAKE_GFLOPS, default 7e-7, i.e. 3.5 flops per FP op at 200/s;
# the sp / hp ceilings are 8 / 16). FAKE_NOT_COUNTED=1 makes instructions "<not counted>".
cat >"${T}/perf" <<'EOF'
#!/usr/bin/env bash
w="${*: -1}"
m=$(cat "$(dirname "$0")/mode" 2>/dev/null || echo idle)
for _ in $(seq "$w"); do
  sleep 1
  [ "$m" = idle ] || echo "gemm_load $m GFLOP/s=${FAKE_GFLOPS:-7e-7} elapsed=1s check=1.000" \
    >>"$(dirname "$0")/gemm.log"
done
case "$*" in
*cmem_rd_data*)
  # SCF: per PMU, read beats (2 per 64-byte transfer) and write bytes; FAKE_SCF_SKEW scales
  # the read count to model a monitor/perf mismatch.
  rd=$(awk -v w="$w" -v k="${FAKE_SCF_SKEW:-1}" 'BEGIN { printf "%.0f", 40000000 * w * k }')
  for p in $(echo "$*" | grep -o 'nvidia_scf_pmu_[0-9]*/cmem_rd_data' | cut -d/ -f1); do
    echo "${rd},,${p}/cmem_rd_data/,100,100.00,," >&2
    echo "$((640000000 * w)),,${p}/cmem_wr_total_bytes/,100,100.00,," >&2
  done
  exit 0
  ;;
esac
dp=0; sp=0; hp=0
case $m in dp) dp=$((200 * w)) ;; sp) sp=$((200 * w)) ;; hp) hp=$((200 * w)) ;; esac
ins=$((2000 * w)); [ "${FAKE_NOT_COUNTED:-0}" = 1 ] && ins="<not counted>"
{ echo "${ins},,instructions,100,50.00,,"
  echo "${dp},,armv8_pmuv3_0/event=0x801c/,100,50.00,,"
  echo "${sp},,armv8_pmuv3_0/event=0x8018/,100,50.00,,"
  echo "${hp},,armv8_pmuv3_0/event=0x8014/,100,50.00,,"; } >&2
EOF
cat >"${T}/load" <<'EOF'
#!/usr/bin/env bash
F="$(dirname "$0")/mode"
case $1 in
start) [ "$(cat "$F")" = idle ] || exit 2; echo "$3" >"$F" ;;
stop) echo idle >"$F" ;;
status) cat "$F" ;;
esac
EOF
chmod +x "${T}/writer.sh" "${T}/perf" "${T}/load"
mkdir -p "${T}/pmu/nvidia_scf_pmu_0" "${T}/pmu/nvidia_scf_pmu_1" "${T}/nopmu"
export CANARY_PMU_DIR="${T}/pmu"
export CANARY_SHM="${T}/shm" CANARY_PERF="${T}/perf" CANARY_LOAD="${T}/load"
export CANARY_LOG_DIR="${T}/log" CANARY_WAIT_MAX=5 CANARY_GEMM_LOG="${T}/gemm.log"

run() { # run <expected-exit> <args...>; output in $out
  local want=$1 rc
  shift
  set +e
  out="$(timeout 120 "${SCRIPT}" "$@" 2>&1)"
  rc=$?
  set -e
  [ "${rc}" -eq "${want}" ] || fail "'$*' exit ${rc}, want ${want} (124 = hung): ${out}"
}

# Bad options exit 2 before touching anything.
for args in "--window 0" "--window x" "--warm -1" "--precisions xx" "--bogus"; do
  # shellcheck disable=SC2086
  run 2 ${args}
done
CANARY_SHM="${T}/none" run 2
run 0 --help
echo "${out}" | grep -q 'flops per dp' || echo "${out}" | grep -q 'ceiling' ||
  fail "--help does not describe the accuracy check"

"${T}/writer.sh" "${T}/shm" &
WPID=$!
for _ in 1 2 3 4 5; do [ -s "${T}/shm/fast" ] && break; sleep 1; done

# Pass path: ratios 1.000, slices clean, flops per op under the ceiling, load stopped.
run 0 --warm 0 --window 2
echo "${out}" | grep -q '^RESULT: PASS' || fail "no RESULT: PASS: ${out}"
for p in dp sp hp; do
  echo "${out}" | grep -q "^O3 ${p} .*1.000 PASS" || fail "O3 ${p} not 1.000 PASS"
  echo "${out}" | grep -q "^slice ${p} .*PASS" || fail "slice ${p} not PASS"
  echo "${out}" | grep -q "^accuracy ${p} .*PASS" || fail "accuracy ${p} not PASS: ${out}"
done
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after pass run"
ls "${T}/log"/*.log >/dev/null 2>&1 || fail "no log file written"

# Count reads low against the GEMM (4.5 flops per dp op > 4): FAIL with the shortfall.
FAKE_GFLOPS=9e-7 run 1 --warm 0 --window 2 --precisions dp
echo "${out}" | grep -q '^accuracy dp .*> 4: monitor FP count at least 11.1% low FAIL' ||
  fail "low count not reported: ${out}"
echo "${out}" | grep -q '^O3 dp .*PASS' || fail "O3 should still pass when both scale alike"

# SCF: monitor arm_aarch64_imc (both sockets) matches perf on both nvidia_scf_pmu_<N>.
run 0 --scf --warm 0 --window 2
echo "${out}" | grep -q '2 SCF PMU(s)' || fail "SCF PMUs not counted: ${out}"
echo "${out}" | grep -q 'read  monitor 2.56 GB/s perf 2.56 GB/s mon/perf 1.000' ||
  fail "SCF read bandwidth wrong: ${out}"
echo "${out}" | grep -q 'write monitor 1.28 GB/s perf 1.28 GB/s mon/perf 1.000' ||
  fail "SCF write bandwidth wrong: ${out}"
echo "${out}" | grep -q '^RESULT: PASS' || fail "SCF run did not PASS: ${out}"
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after SCF run"
# A 20% read mismatch fails; no SCF PMU fails without starting a load.
FAKE_SCF_SKEW=1.2 run 1 --scf --warm 0 --window 2
echo "${out}" | grep -q '^scf read  FAIL' || fail "SCF mismatch not caught: ${out}"
CANARY_PMU_DIR="${T}/nopmu" run 1 --scf --warm 0 --window 1
echo "${out}" | grep -q 'FAIL: no nvidia_scf_pmu_' || fail "missing SCF PMU not reported"

# perf "<not counted>" keeps its slot: instructions NA, FP events still compared.
FAKE_NOT_COUNTED=1 run 1 --warm 0 --window 2 --precisions dp
echo "${out}" | grep -q 'arm_fp_dp_spec  monitor 200 .*mon/perf 1.000' ||
  fail "dp slot shifted after <not counted>: ${out}"

# Ctrl-C: exit 130 and the load is stopped.
timeout 60 "${SCRIPT}" --warm 0 --window 30 --precisions dp >"${T}/int.out" 2>&1 &
cpid=$!
for _ in $(seq 20); do [ "$(cat "${T}/mode")" = dp ] && break; sleep 0.5; done
[ "$(cat "${T}/mode")" = dp ] || fail "load never started before SIGINT"
kill -INT "${cpid}"
set +e
wait "${cpid}"
rc=$?
set -e
[ "${rc}" -eq 130 ] || fail "SIGINT exit ${rc}, want 130"
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after SIGINT"

# Stale fast file (writer stopped): bounded waits, FAIL, exit 1, no hang.
kill "${WPID}"
wait "${WPID}" 2>/dev/null || true
WPID=""
run 1 --warm 0 --window 1 --precisions dp
echo "${out}" | grep -q 'WARN: fast unchanged' || fail "no stale warning: ${out}"
echo "${out}" | grep -q '^RESULT: FAIL' || fail "stale run did not FAIL"
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after stale run"
echo "test_canary_counter_check: OK"
