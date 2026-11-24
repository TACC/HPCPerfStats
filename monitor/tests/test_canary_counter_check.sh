#!/usr/bin/env bash
# Regression (via check-local): scripts/canary_counter_check.sh against a fake node (shm writer
# with the real payload layout, i.e. a blank first line and @fast rows that carry fast keys
# only; fake perf that also writes GEMM log lines; fake load). Locks: monitor/perf rates,
# precision slices and the GEMM flops-per-op accuracy check PASS; a count that reads low
# against the GEMM FAILs; a stale fast file ends with FAIL instead of hanging; Ctrl-C exits 130
# and stops the load (the "interrupted" line still reaches the output); a perf "<not counted>"
# event keeps its slot; bad options exit 2.
set -euo pipefail
# check-local runs this as /bin/sh; EL8 bash 4.4 in posix mode keeps `VAR=x run` after run
# returns (c313-031: CANARY_SHM=.../none leaked into every later run).
set +o posix
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
# has PATTERN: grep the last run's output. A here-string, not echo | grep -q: grep -q exits on
# the first match, and under pipefail the writer's SIGPIPE (141) would fail a passing check.
has() { grep -q -- "$1" <<<"${out}"; }
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
# ../sharing selects the D6 keys (monitor 3.1 Phase 1b): "solo" = enabled 9000, running 7000, solo 7000
# per CPU per second (own oversubscription only); "outside" = running 2500 (another PMU user);
# "none" = a daemon built before Phase 1b (no keys).
cat >"${T}/writer.sh" <<'EOF'
#!/usr/bin/env bash
D=$1; t=1000; i=0; d=0; s=0; h=0; r=0; w=0; en=0; run=0; solo=0
while :; do
  m=$(cat "$D/../mode" 2>/dev/null || echo idle)
  sh_mode=$(cat "$D/../sharing" 2>/dev/null || echo solo)
  hw='!host_cpu_hw cpu_util_total_accum_us,E,R=S instr_retired,E,W=48 arm_fp_dp_spec,E,W=48 arm_fp_sp_spec,E,W=48 arm_fp_hp_spec,E,W=48'
  [ "$sh_mode" = none ] ||
    hw="$hw pmu_enabled_time,E,W=64,U=ns pmu_running_time,E,W=64,U=ns pmu_solo_running_time,E,W=64,U=ns"
  { printf '$x\n%s\n' "$hw"
    printf '!arm_aarch64_imc dram_cas_reads,E,W=48 dram_cas_writes,E,W=48\n'; } >"$D/schema.tmp" &&
    mv "$D/schema.tmp" "$D/schema"
  # ../tick: seconds per sample (default 1). ../scfvary: the SCF load runs 3x faster whenever
  # perf is not counting (../perfon absent), a load that varies across the perf window.
  k=$(cat "$D/../tick" 2>/dev/null || echo 1); f=1
  [ -e "$D/../scfvary" ] && [ ! -e "$D/../perfon" ] && f=3
  i=$((i + 1000 * k))
  case $m in dp) d=$((d + 100 * k)); r=$((r + 20000000 * k * f)); w=$((w + 10000000 * k * f)) ;;
    sp) s=$((s + 100 * k)) ;; hp) h=$((h + 100 * k)) ;; esac
  en=$((en + 9000 * k)); solo=$((solo + 7000 * k))
  if [ "$sh_mode" = outside ]; then run=$((run + 2500 * k)); else run=$((run + 7000 * k)); fi
  extra=""
  [ "$sh_mode" = none ] || extra=" $en $run $solo"
  t=$((t + k))
  { printf '\n%d.000000 job host\n' "$t"
    printf 'host_cpu_hw 0 @fast %d %d %d %d%s\n' "$i" "$d" "$s" "$h" "$extra"
    printf 'host_cpu_hw 1 @fast %d %d %d %d%s\n' "$i" "$d" "$s" "$h" "$extra"
    printf 'arm_aarch64_imc scf0 @fast %d %d\n' "$r" "$w"
    printf 'arm_aarch64_imc scf1 @fast %d %d\n' "$r" "$w"
  } >"$D/fast.tmp" && mv "$D/fast.tmp" "$D/fast"
  sleep "$k"
done
EOF
# Fake perf stat: instructions 2000/s, the mode's FP event 200/s; appends one GEMM log line
# per second of the window (FAKE_GFLOPS, default 7e-7, i.e. 3.5 flops per FP op at 200/s;
# the sp / hp ceilings are 8 / 16). FAKE_NOT_COUNTED=1 makes instructions "<not counted>".
cat >"${T}/perf" <<'EOF'
#!/usr/bin/env bash
s=$(date +%s.%N)
H=$(dirname "$0")
case "$*" in
*cmem_rd_data*)
  # SCF: runs the command after --, counting the base load (per PMU 4e7 read beats/s, 2 per
  # 64-byte transfer, and 6.4e8 write bytes/s) over its own lifetime like perf, with ../perfon
  # present while the command runs. FAKE_SCF_SKEW scales the read count (monitor/perf mismatch).
  ev=""
  while [ "$#" -gt 0 ] && [ "$1" != -- ]; do ev="$ev $1"; shift; done
  shift
  touch "$H/perfon"; "$@"; rm -f "$H/perfon"
  read -r rd wr < <(awk -v a="$s" -v b="$(date +%s.%N)" -v k="${FAKE_SCF_SKEW:-1}" \
    'BEGIN { printf "%.0f %.0f\n", 4e7 * (b - a) * k, 6.4e8 * (b - a) }')
  for p in $(echo "$ev" | grep -o 'nvidia_scf_pmu_[0-9]*/cmem_rd_data' | cut -d/ -f1); do
    echo "${rd},,${p}/cmem_rd_data/,100,100.00,," >&2
    echo "${wr},,${p}/cmem_wr_total_bytes/,100,100.00,," >&2
  done
  exit 0
  ;;
esac
w="${*: -1}"
m=$(cat "$H/mode" 2>/dev/null || echo idle)
for _ in $(seq "$w"); do
  sleep 1
  [ "$m" = idle ] || echo "gemm_load $m GFLOP/s=${FAKE_GFLOPS:-7e-7} elapsed=1s check=1.000" \
    >>"$H/gemm.log"
done
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
has 'flops per dp' || has 'ceiling' ||
  fail "--help does not describe the accuracy check"

"${T}/writer.sh" "${T}/shm" &
WPID=$!
for _ in 1 2 3 4 5; do [ -s "${T}/shm/fast" ] && break; sleep 1; done

# Pass path: ratios 1.000, slices clean, flops per op under the ceiling, load stopped.
run 0 --warm 0 --window 2
has '^RESULT: PASS' || fail "no RESULT: PASS: ${out}"
for p in dp sp hp; do
  has "^O3 ${p} .*1.000 PASS" || fail "O3 ${p} not 1.000 PASS"
  has "^slice ${p} .*PASS" || fail "slice ${p} not PASS"
  has "^accuracy ${p} .*PASS" || fail "accuracy ${p} not PASS: ${out}"
  # D6: own oversubscription only: running == solo-expected (7000 / 9000), no outside share.
  has "^sharing ${p} .*running 77.8% solo-expected 77.8% outside 0.0% PASS" ||
    fail "sharing ${p} not 77.8 / 77.8 / 0.0: ${out}"
done
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after pass run"
ls "${T}/log"/*.log >/dev/null 2>&1 || fail "no log file written"

# D6: another PMU user leaves the events 2500 of 9000 ns: outside share 1 - 2500/7000 = 64.3%.
echo outside >"${T}/sharing"
run 0 --warm 0 --window 2 --precisions dp
has '^sharing dp .*running 27.8% solo-expected 77.8% outside 64.3% PASS' ||
  fail "outside sharing not reported: ${out}"
# A daemon built before Phase 1b has no sharing keys: reported, not failed.
echo none >"${T}/sharing"
sleep 2
run 0 --warm 0 --window 2 --precisions dp
has '^sharing dp .*not published (daemon without the Phase 1b keys)' ||
  fail "pre-Phase-1b daemon not reported: ${out}"
# --sharing-only needs the keys: a daemon without them FAILs (exit 1).
run 1 --sharing-only --window 2
has '^sharing quiet not published' || fail "sharing-only w/o keys: ${out}"
echo solo >"${T}/sharing"
sleep 2

# --sharing-only: no load and no perf (CANARY_PERF points nowhere), just the D6 keys over
# the window; the quiet node reads running == solo-expected with no outside share.
CANARY_PERF="${T}/no-such-perf" run 0 --sharing-only --window 2
has '^sharing quiet running 77.8% solo-expected 77.8% outside 0.0% PASS' ||
  fail "sharing-only quiet line wrong: ${out}"
has '^RESULT: PASS' || fail "sharing-only did not PASS: ${out}"
[ "$(cat "${T}/mode")" = idle ] || fail "sharing-only started a load"
echo outside >"${T}/sharing"
sleep 2
CANARY_PERF="${T}/no-such-perf" run 0 --sharing-only --window 2
has '^sharing quiet running 27.8% solo-expected 77.8% outside 64.3% PASS' ||
  fail "sharing-only outside line wrong: ${out}"
echo solo >"${T}/sharing"
sleep 2

# Count reads low against the GEMM (4.5 flops per dp op > 4): FAIL with the shortfall.
FAKE_GFLOPS=9e-7 run 1 --warm 0 --window 2 --precisions dp
has '^accuracy dp .*> 4: monitor FP count at least 11.1% low FAIL' ||
  fail "low count not reported: ${out}"
has '^O3 dp .*PASS' || fail "O3 should still pass when both scale alike"
# Shared counters (c122-018: running ~20%, dp read 1-4% low run to run): with running under
# 50% (here 27.8%) 4.1 flops per dp op passes on the wider margin; the same count fails when
# the events ran unshared (77.8%).
echo outside >"${T}/sharing"
sleep 2
FAKE_GFLOPS=8.2e-7 run 0 --warm 0 --window 2 --precisions dp
has '^accuracy dp .*<= 4 (+5% margin, counters shared) PASS' ||
  fail "shared-counter margin not applied: ${out}"
echo solo >"${T}/sharing"
sleep 2
FAKE_GFLOPS=8.2e-7 run 1 --warm 0 --window 2 --precisions dp
has '^accuracy dp .*> 4: monitor FP count at least .* low FAIL' ||
  fail "unshared count 2.5% low not failed: ${out}"

# SCF: monitor arm_aarch64_imc (both sockets) matches perf on both nvidia_scf_pmu_<N>.
run 0 --scf --warm 0 --window 2
has '2 SCF PMU(s)' || fail "SCF PMUs not counted: ${out}"
# perf is divided by its wall time, which includes process start-up: ~50 ms here, ~90 ms on
# c122-018 (write mon/perf 1.031), so up to 4% of this 3 s window, inside the script's 5%
# limit (under 0.1% of a node's 120 s one).
has 'read  monitor 2.56 GB/s perf 2\.[45][0-9] GB/s mon/perf \(1\.0[0-4]\|0\.99\)' ||
  fail "SCF read bandwidth wrong: ${out}"
has 'write monitor 1.28 GB/s perf 1.2[0-9] GB/s mon/perf \(1\.0[0-4]\|0\.99\)' ||
  fail "SCF write bandwidth wrong: ${out}"
has '^RESULT: PASS' || fail "SCF run did not PASS: ${out}"
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after SCF run"
# Varying load (c122-018 2026-11-22, read 1.062 / write 1.058): samples every 3 s and the load
# 3x faster whenever perf is not counting. perf must span the daemon's own sample window, or
# read and write drift together.
echo 3 >"${T}/tick"
touch "${T}/scfvary"
sleep 4
run 0 --scf --warm 0 --window 2
{ has '^scf read  PASS' && has '^scf write PASS'; } ||
  fail "SCF windows differ on a varying load: ${out}"
rm -f "${T}/scfvary"
echo 1 >"${T}/tick"
sleep 4
# A 20% read mismatch fails; no SCF PMU fails without starting a load.
FAKE_SCF_SKEW=1.2 run 1 --scf --warm 0 --window 2
has '^scf read  FAIL' || fail "SCF mismatch not caught: ${out}"
CANARY_PMU_DIR="${T}/nopmu" run 1 --scf --warm 0 --window 1
has 'FAIL: no nvidia_scf_pmu_' || fail "missing SCF PMU not reported"

# perf "<not counted>" keeps its slot: instructions NA, FP events still compared.
FAKE_NOT_COUNTED=1 run 1 --warm 0 --window 2 --precisions dp
has 'arm_fp_dp_spec  monitor 200 .*mon/perf 1.000' ||
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
out="$(cat "${T}/int.out")"
has '^interrupted$' || fail "Ctrl-C message lost (output tee killed by the SIGINT): ${out}"
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after SIGINT"

# Stale fast file (writer stopped): bounded waits, FAIL, exit 1, no hang.
kill "${WPID}"
wait "${WPID}" 2>/dev/null || true
WPID=""
run 1 --warm 0 --window 1 --precisions dp
has 'WARN: fast unchanged' || fail "no stale warning: ${out}"
has '^RESULT: FAIL' || fail "stale run did not FAIL"
[ "$(cat "${T}/mode")" = idle ] || fail "load still running after stale run"
echo "test_canary_counter_check: OK"
