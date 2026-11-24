#!/usr/bin/env bash
# Regression (via check-local): scripts/perf_raw_flops_check.sh against a fake perf, a fake GEMM
# load and fake cpuinfo files: Intel / AMD default event sets, weighted ratio PASS, precision
# leak FAIL, undercount FAIL (AMD MERGE-pairing symptom), events perf cannot open are skipped
# (perf exits 0 on <not supported>), <not counted> in a run, multiplexed running share shown,
# missing GEMM total, all-zero hint, --list encodings, perf's reason shown for a skipped event,
# --gemm-cflags passed to the load install, and bad options (exit 2).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${ROOT}/scripts/perf_raw_flops_check.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/hps-perf-raw-flops.XXXXXX")"
trap 'rm -rf "${T}"' EXIT

fail() {
  echo "test_perf_raw_flops_check: $*" >&2
  exit 1
}
test -x "${SCRIPT}" || fail "missing or not executable: ${SCRIPT}"

# Fake perf. Table lines: SPEC DP_COUNT SP_COUNT [RUNNING]; a spec not in the table is
# <not supported> (exit 0, like real perf); a count of "nc" prints <not counted> in the run.
cat >"${T}/perf" <<'EOF'
#!/usr/bin/env bash
case "$1" in
--version) echo "perf version fake"; exit 0 ;;
list) printf '  fp_ret_sse_avx_ops.all\n       [All FLOPS. Unit: cpu]\n        cpu/event=0x3,umask=0xff/\n'; exit 0 ;;
stat) shift ;;
*) exit 2 ;;
esac
out=/dev/stderr specs=()
while [ $# -gt 0 ]; do
  case "$1" in
  -x) shift 2 ;;
  -o) out="$2"; shift 2 ;;
  -e) specs+=("$2"); shift 2 ;;
  --) shift; break ;;
  *) shift ;;
  esac
done
prec=dp
for a in "$@"; do [ "${prev:-}" = --precision ] && prec="$a"; prev="$a"; done
"$@" || exit $?
{
  echo "# started fake"
  echo
  for s in "${specs[@]}"; do
    row="$(awk -v s="$s" '$1 == s { print; exit }' "${FAKE_PERF_TABLE}")"
    if [ -z "${row}" ]; then echo "<not supported>;;${s};0;100.00;;"; continue; fi
    set -- ${row}
    c=$2; [ "${prec}" = sp ] && c=$3
    # "nc" is <not counted> only in the measured run (-o), so the 0.1 s probe accepts it.
    if [ "$c" = nc ]; then if [ "${out}" = /dev/stderr ]; then c=1; else c="<not counted>"; fi; fi
    echo "${c};;${s}:u;1000;${4:-100.00};;"
  done
} >"${out}" 2>&1
EOF
# Fake GEMM load: install (fails with FAKE_INSTALL_FAIL=1); run prints the exact total unless
# FAKE_NO_TOTAL=1.
cat >"${T}/load" <<'EOF'
#!/usr/bin/env bash
cmd="$1"; shift
if [ "${cmd}" = install ]; then
  echo "$*" >"${FAKE_INSTALL_ARGS:-/dev/null}"
  exit "${FAKE_INSTALL_FAIL:-0}"
fi
p=dp
while [ $# -gt 0 ]; do [ "$1" = --precision ] && p="$2"; shift; done
echo "gemm_load ${p} n=64 threads=1 seconds=1 started"
[ "${FAKE_NO_TOTAL:-0}" = 1 ] || echo "gemm_load ${p} done total_flops=1000000 elapsed=1.000s"
EOF
chmod +x "${T}/perf" "${T}/load"
printf 'vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 143\n' >"${T}/intel"
printf 'vendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel\t\t: 1\n' >"${T}/amd"
printf 'vendor_id\t: SomethingElse\n' >"${T}/other"

# run CPUINFO TABLE [args...]: sets out and rc.
run() {
  local cpu="$1" table="$2"
  shift 2
  set +e
  out="$(PERF="${T}/perf" PERF_RAW_FLOPS_LOAD="${T}/load" PERF_RAW_FLOPS_CPUINFO="${T}/${cpu}" \
    FAKE_PERF_TABLE="${table}" "${SCRIPT}" --prefix "${T}/prefix" --seconds 1 --threads 1 "$@" 2>&1)"
  rc=$?
  set -e
}
has() { grep -qF -- "$1" <<<"${out}" || fail "$2: missing '$1' in: ${out}"; }

# Intel defaults: dp FLOPs on the 256b event (weight 4), sp on 256b single (weight 8); the 512b
# events are not in the table, so perf reports <not supported> and they are skipped.
I="${T}/intel.tab"
{
  echo "fp_arith_inst_retired.scalar_double 0 0"
  echo "fp_arith_inst_retired.scalar_single 0 0"
  echo "fp_arith_inst_retired.128b_packed_double 0 0"
  echo "fp_arith_inst_retired.128b_packed_single 0 0"
  echo "fp_arith_inst_retired.256b_packed_double 250000 0 50.00"
  echo "fp_arith_inst_retired.256b_packed_single 0 125000"
} >"${I}"
run intel "${I}"
[ "${rc}" -eq 0 ] || fail "intel pass exit ${rc}: ${out}"
has "event dp-512b_packed (fp_arith_inst_retired.512b_packed_double): not usable" "512b skipped"
has "skipped; perf: <not supported>;;fp_arith_inst_retired.512b_packed_double" "skip shows perf reason"
has "gemm extra cflags: none" "no extra cflags by default"
has "events: 6" "six usable events"
has "event dp-256b_packed   weight  4  count 2.5e+05  running 50.00%  FLOPs/GEMM 1.0000" "dp 256b line"
has "dp ratio 1.000 PASS" "dp ratio"
has "dp leak 0.000 PASS" "dp leak"
has "sp ratio 1.000 PASS" "sp ratio"
has "RESULT: PASS" "intel verdict"

# --gemm-cflags reaches the load install unchanged.
set +e
out="$(FAKE_INSTALL_ARGS="${T}/install.args" PERF="${T}/perf" PERF_RAW_FLOPS_LOAD="${T}/load" \
  PERF_RAW_FLOPS_CPUINFO="${T}/intel" FAKE_PERF_TABLE="${I}" "${SCRIPT}" --prefix "${T}/prefix" \
  --seconds 1 --threads 1 --precisions dp --gemm-cflags "-mprefer-vector-width=512 -mfoo" 2>&1)"
rc=$?
set -e
[ "${rc}" -eq 0 ] || fail "--gemm-cflags run exit ${rc}: ${out}"
has "gemm extra cflags: -mprefer-vector-width=512 -mfoo" "cflags echoed"
grep -qx -- "--prefix ${T}/prefix --cflags -mprefer-vector-width=512 -mfoo" "${T}/install.args" ||
  fail "install args: $(cat "${T}/install.args")"

# Precision leak: a dp run that also shows 5% sp FLOPs fails.
sed 's/^fp_arith_inst_retired.scalar_single 0 0/fp_arith_inst_retired.scalar_single 50000 0/' "${I}" >"${T}/leak.tab"
run intel "${T}/leak.tab" --precisions dp
[ "${rc}" -eq 1 ] || fail "leak exit ${rc}: ${out}"
has "leak  0.050  FAIL" "leak line"
has "RESULT: FAIL" "leak verdict"

# AMD default: one all-class FLOP event, weight 1, no leak line; half the FLOPs fails (the
# symptom of a missing MERGE pairing) with exit 1.
echo "fp_ret_sse_avx_ops.all 1000000 1000000" >"${T}/amd.tab"
run amd "${T}/amd.tab"
[ "${rc}" -eq 0 ] || fail "amd pass exit ${rc}: ${out}"
has "event all-flops" "amd event"
has "sp ratio 1.000 PASS" "amd sp ratio"
grep -q "leak" <<<"${out}" && fail "amd has no precision events, so no leak line: ${out}"
echo "fp_ret_sse_avx_ops.all 500000 500000" >"${T}/amd_half.tab"
run amd "${T}/amd_half.tab" --precisions dp
[ "${rc}" -eq 1 ] || fail "amd undercount exit ${rc}: ${out}"
has "ratio 0.500  FAIL" "amd undercount"

# Raw --event specs replace the defaults; <not counted> during the run is shown, the rest counts.
{
  echo "cpu/event=0x3,umask=0xff/ 1000000 1000000"
  echo "cpu/event=0x3,umask=0x01/ nc nc"
} >"${T}/raw.tab"
run other "${T}/raw.tab" --precisions sp --event "all-raw:1:cpu/event=0x3,umask=0xff/" \
  --event "sp-x:1:cpu/event=0x3,umask=0x01/"
[ "${rc}" -eq 0 ] || fail "raw spec exit ${rc}: ${out}"
has "event sp-x             <not counted>" "not counted line"
has "RESULT: PASS" "raw verdict"

# Every event reads 0 (virtualized PMU): FAIL with the hint.
echo "fp_ret_sse_avx_ops.all 0 0" >"${T}/zero.tab"
run amd "${T}/zero.tab" --precisions dp
[ "${rc}" -eq 1 ] || fail "zero exit ${rc}"
has "every counted event read 0" "zero hint"

# No usable events at all, and a GEMM that never prints its total, both FAIL.
: >"${T}/empty.tab"
run amd "${T}/empty.tab"
[ "${rc}" -eq 1 ] || fail "no events exit ${rc}"
has "RESULT: FAIL (no usable events" "no events"
set +e
out="$(FAKE_NO_TOTAL=1 PERF="${T}/perf" PERF_RAW_FLOPS_LOAD="${T}/load" \
  PERF_RAW_FLOPS_CPUINFO="${T}/amd" FAKE_PERF_TABLE="${T}/amd.tab" "${SCRIPT}" \
  --prefix "${T}/prefix" --seconds 1 --precisions dp 2>&1)"
rc=$?
set -e
[ "${rc}" -eq 1 ] || fail "missing total exit ${rc}: ${out}"
has "GEMM total missing" "missing total"
has "FAIL: dp run-failed" "missing total summary"

# --list prints the CPU and the encodings perf knows.
run amd "${T}/amd.tab" --list
[ "${rc}" -eq 0 ] || fail "--list exit ${rc}"
has "cpu AuthenticAMD family 25 model 1" "list cpu"
has "cpu/event=0x3,umask=0xff/" "list encoding"

# Bad input exits 2 before any perf run.
for args in "--precisions qp" "--tolerance 0" "--seconds 0" "--event dp-a:0:x" "--event xx-a:1:x" \
  "--event dp-a:x" "--bogus"; do
  # shellcheck disable=SC2086
  run amd "${T}/amd.tab" ${args}
  [ "${rc}" -eq 2 ] || fail "'${args}' exit ${rc}, want 2: ${out}"
done
run other "${T}/amd.tab"
[ "${rc}" -eq 2 ] || fail "unknown vendor without --event exit ${rc}, want 2"
has "no default events for vendor 'SomethingElse'" "unknown vendor"

echo "PASS test_perf_raw_flops_check"
