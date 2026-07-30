#!/usr/bin/env bash
# Regression: stub lshw must not invoke the real binary and must emit JSON.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUB="${ROOT}/scripts/stubs/lshw"
TMPDIR="${TMPDIR:-/tmp}"
FAKE_BIN="${TMPDIR}/hps-lshw-stub-test-$$"
mkdir -p "${FAKE_BIN}"
cleanup() { rm -rf "${FAKE_BIN}"; }
trap cleanup EXIT

cat >"${FAKE_BIN}/lshw" <<'EOF'
#!/bin/sh
echo "REAL_LSHW_RAN" >&2
exit 99
EOF
chmod +x "${FAKE_BIN}/lshw"

test -x "${STUB}" || { echo "missing stub ${STUB}" >&2; exit 1; }

out="$(PATH="${FAKE_BIN}:/usr/bin:/bin" "${STUB}" -json 2>"${FAKE_BIN}/err")"
err="$(cat "${FAKE_BIN}/err")"

test "${out}" = '{}' || { echo "stub stdout want '{}'; got '${out}'" >&2; exit 1; }
test -z "${err}" || { echo "stub wrote stderr: ${err}" >&2; exit 1; }
# Ensure PATH did not pick real/fake lshw via exec of basename alone from stub
# (stub is an absolute path invocation above).
grep -q REAL_LSHW_RAN "${FAKE_BIN}/err" && { echo "real lshw ran" >&2; exit 1; }

# which/PATH ordering: stub dir first must win for bare `lshw`
out2="$(PATH="$(dirname "${STUB}"):${FAKE_BIN}:/usr/bin" command -v lshw)"
test "${out2}" = "$(dirname "${STUB}")/lshw" || {
  echo "PATH order failed: ${out2}" >&2
  exit 1
}
out3="$(PATH="$(dirname "${STUB}"):${FAKE_BIN}:/usr/bin" lshw -json 2>"${FAKE_BIN}/err2")"
test "${out3}" = '{}' || { echo "PATH lshw want '{}'; got '${out3}'" >&2; exit 1; }
! grep -q REAL_LSHW_RAN "${FAKE_BIN}/err2" || { echo "PATH hit real lshw" >&2; exit 1; }

# libdcgm execs absolute paths with a minimal env (strace: ~6 vars, argv[0]=absolute).
# Stub must still emit {} with no PATH and no inherited env.
out4="$(env -i "${STUB}" -json 2>"${FAKE_BIN}/err4")"
err4="$(cat "${FAKE_BIN}/err4")"
test "${out4}" = '{}' || { echo "env -i stub stdout want '{}'; got '${out4}'" >&2; exit 1; }
test -z "${err4}" || { echo "env -i stub wrote stderr: ${err4}" >&2; exit 1; }

echo "test_lshw_stub.sh passed"
