#!/usr/bin/env bash
# Contract tests for scripts/run_warning_gate.sh that need no build:
#   - --parse-gc-log extracts "<object> <function>" from GNU ld and lld --print-gc-sections
#     output (daemon objects only, clone suffixes stripped, non-.text sections ignored)
#   - --dead-from-sets reports a function only when every configuration that defines it
#     leaves it unreachable (ARM-only / DEBUG-only callers count as reached)
#   - a missing prerequisite prints the bold SKIPPED banner and exits 0
#   - HPC_WARN_GATE_REQUIRE=1 turns that skip into a failure
#   - unknown arguments exit 2
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="${SCRIPT_DIR}/../scripts/run_warning_gate.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/warn_gate_test_XXXXXX")"
trap 'rm -rf "${WORK}"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# --- --parse-gc-log ---
cat >"${WORK}/gc.log" <<'EOF'
/usr/bin/ld: removing unused section '.text.dict_remv' in file 'hpcperfstatsd-dict.o'
/usr/bin/ld: removing unused section '.text.str_eq_nocase.part.0' in file 'hpcperfstatsd-ib_family.o'
/usr/bin/ld: removing unused section '.text.ring_buffer_resend' in file '../src/hpcperfstatsd-stats_buffer.o'
/usr/bin/ld: removing unused section '.rodata.some_table' in file 'hpcperfstatsd-dict.o'
/usr/bin/ld: removing unused section '.text.ev_run' in file '/prefix/lib/libev.a(ev.o)'
/usr/bin/ld: removing unused section '.text.dict_remv' in file 'hpcperfstatsd-dict.o'
removing unused section hpcperfstatsd-perf_util.o:(.text.perf_util_open_group)
removing unused section ../src/hpcperfstatsd-arm_imc.o:(.text.arm_imc_probe.constprop.0)
removing unused section /sysroot/lib64/crt1.o:(.text.unused_crt)
removing unused section hpcperfstatsd-arm_imc.o:(.rodata.table)
unrelated line
EOF
got="$("${GATE}" --parse-gc-log "${WORK}/gc.log")"
want="$(printf '%s\n' \
  'hpcperfstatsd-arm_imc.o arm_imc_probe' \
  'hpcperfstatsd-dict.o dict_remv' \
  'hpcperfstatsd-ib_family.o str_eq_nocase' \
  'hpcperfstatsd-perf_util.o perf_util_open_group' \
  'hpcperfstatsd-stats_buffer.o ring_buffer_resend')"
test "${got}" = "${want}" || fail "parse-gc-log (GNU ld + lld): got [${got}] want [${want}]"

# --- --dead-from-sets: dead only when unreachable in every configuration defining it ---
mkdir -p "${WORK}/sets"
# shared_arm_only: x86 never calls it, ARM does -> reached, not dead
# debug_only:      only the DEBUG build calls it -> reached
# x86_only_dead:   defined and unreachable in the x86 builds, absent on ARM -> dead
# dead_everywhere: unreachable wherever defined -> dead
printf '%s\n' dead_everywhere debug_only shared_arm_only used_everywhere x86_only_dead >"${WORK}/sets/gc.defined"
printf '%s\n' dead_everywhere debug_only shared_arm_only x86_only_dead >"${WORK}/sets/gc.unreachable"
printf '%s\n' dead_everywhere debug_only shared_arm_only used_everywhere x86_only_dead >"${WORK}/sets/gc-debug.defined"
printf '%s\n' dead_everywhere shared_arm_only x86_only_dead >"${WORK}/sets/gc-debug.unreachable"
printf '%s\n' dead_everywhere shared_arm_only used_everywhere >"${WORK}/sets/gc-arm.defined"
printf '%s\n' dead_everywhere >"${WORK}/sets/gc-arm.unreachable"
got="$("${GATE}" --dead-from-sets "${WORK}/sets")"
want="$(printf '%s\n' dead_everywhere x86_only_dead)"
test "${got}" = "${want}" || fail "dead-from-sets: got [${got}] want [${want}]"

# --- skip banner when the prefix is missing ---
set +e
out="$(PREFIX="${WORK}/no-such-prefix" HPC_WARN_GATE_LOG="${WORK}/skip.log" "${GATE}" --commit 2>&1)"
rc=$?
set -e
test "${rc}" -eq 0 || fail "missing prefix should skip with rc 0, got ${rc}"
case "${out}" in
  *"MONITOR WARNING GATE SKIPPED -- C CODE WAS NOT CHECKED"*) ;;
  *) fail "skip banner missing: ${out}" ;;
esac
# The first failing prerequisite names the reason (non-Linux / no gcc / no autoreconf on
# some build hosts); on a full dev host it is the missing prefix.
case "${out}" in
  *"##  reason: "*) ;;
  *) fail "skip reason missing: ${out}" ;;
esac
if test "$(uname -s)" = "Linux" && command -v gcc >/dev/null 2>&1 \
  && command -v autoreconf >/dev/null 2>&1; then
  case "${out}" in
    *"missing under PREFIX=${WORK}/no-such-prefix"*) ;;
    *) fail "expected missing-prefix reason: ${out}" ;;
  esac
fi

# --- HPC_WARN_GATE_REQUIRE=1 fails instead ---
set +e
PREFIX="${WORK}/no-such-prefix" HPC_WARN_GATE_LOG="${WORK}/req.log" HPC_WARN_GATE_REQUIRE=1 \
  "${GATE}" --commit >/dev/null 2>&1
rc=$?
set -e
test "${rc}" -eq 1 || fail "HPC_WARN_GATE_REQUIRE=1 should exit 1, got ${rc}"

# --- --push without the aarch64 sysroot: aarch64-only banner; REQUIRE=1 fails in preflight ---
# Only on hosts whose x86 prerequisites pass (otherwise the whole-gate skip comes first) and
# that have clang + ld.lld, which the gate checks before the sysroot (LS6 c313-031: no clang).
DEFAULT_PREFIX="${SCRIPT_DIR}/../../../.build/prefix-static"
if test "$(uname -s)" = "Linux" && command -v gcc >/dev/null 2>&1 \
  && command -v autoreconf >/dev/null 2>&1 && test -f "${DEFAULT_PREFIX}/lib/libev.a" \
  && command -v clang >/dev/null 2>&1 && command -v ld.lld >/dev/null 2>&1; then
  set +e
  out="$(ARM_SYSROOT="${WORK}/no-such-sysroot" HPC_WARN_GATE_LOG="${WORK}/arm.log" \
    HPC_WARN_GATE_REQUIRE=1 "${GATE}" --push 2>&1)"
  rc=$?
  set -e
  test "${rc}" -eq 1 || fail "missing aarch64 sysroot with REQUIRE=1 should exit 1, got ${rc}"
  case "${out}" in
    *"AARCH64 + UNREACHABLE-FUNCTION CHECKS NOT RUN"*"aarch64 sysroot missing"*) ;;
    *) fail "aarch64 skip banner missing: ${out}" ;;
  esac
fi

# --- bad arguments ---
set +e
"${GATE}" --bogus >/dev/null 2>&1
rc=$?
"${GATE}" --parse-gc-log >/dev/null 2>&1
rc2=$?
"${GATE}" --dead-from-sets "${WORK}/no-such-dir" >/dev/null 2>&1
rc3=$?
set -e
test "${rc}" -eq 2 || fail "unknown option should exit 2, got ${rc}"
test "${rc2}" -eq 2 || fail "--parse-gc-log without FILE should exit 2, got ${rc2}"
test "${rc3}" -eq 2 || fail "--dead-from-sets without a DIR should exit 2, got ${rc3}"

echo "test_run_warning_gate: ok"
