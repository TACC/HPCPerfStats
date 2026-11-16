#!/usr/bin/env bash
# Contract tests for scripts/run_warning_gate.sh that need no build:
#   - --parse-gc-log extracts "<object> <function>" from ld --print-gc-sections output
#     (daemon objects only, GCC clone suffixes stripped, non-.text sections ignored)
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
/usr/bin/ld: removing unused section '.text.str_eq_nocase.part.0' in file 'hpcperfstatsd-likwid_arch_map.o'
/usr/bin/ld: removing unused section '.text.ring_buffer_resend' in file '../src/hpcperfstatsd-stats_buffer.o'
/usr/bin/ld: removing unused section '.rodata.some_table' in file 'hpcperfstatsd-dict.o'
/usr/bin/ld: removing unused section '.text.ev_run' in file '/prefix/lib/libev.a(ev.o)'
/usr/bin/ld: removing unused section '.text.dict_remv' in file 'hpcperfstatsd-dict.o'
unrelated line
EOF
got="$("${GATE}" --parse-gc-log "${WORK}/gc.log")"
want="$(printf '%s\n' \
  'hpcperfstatsd-dict.o dict_remv' \
  'hpcperfstatsd-likwid_arch_map.o str_eq_nocase' \
  'hpcperfstatsd-stats_buffer.o ring_buffer_resend')"
test "${got}" = "${want}" || fail "parse-gc-log: got [${got}] want [${want}]"

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

# --- bad arguments ---
set +e
"${GATE}" --bogus >/dev/null 2>&1
rc=$?
"${GATE}" --parse-gc-log >/dev/null 2>&1
rc2=$?
set -e
test "${rc}" -eq 2 || fail "unknown option should exit 2, got ${rc}"
test "${rc2}" -eq 2 || fail "--parse-gc-log without FILE should exit 2, got ${rc2}"

echo "test_run_warning_gate: ok"
