#!/bin/sh
# Smoke: validate_stampede3_profile.sh requires --profile and rejects Vista names.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WRAP="${ROOT}/scripts/validate_stampede3_profile.sh"
test -x "${WRAP}" || { echo "missing executable ${WRAP}" >&2; exit 1; }

if "${WRAP}" >/tmp/vsp_out.$$ 2>/tmp/vsp_err.$$; then
  echo "expected failure without --profile" >&2
  exit 1
fi
grep -q -- '--profile' /tmp/vsp_err.$$ || {
  echo "expected --profile hint in stderr" >&2
  cat /tmp/vsp_err.$$ >&2
  exit 1
}

if "${WRAP}" --profile gg >/tmp/vsp_out.$$ 2>/tmp/vsp_err.$$; then
  echo "expected failure for Vista profile gg" >&2
  exit 1
fi
grep -qi vista /tmp/vsp_err.$$ || {
  echo "expected Vista rejection message" >&2
  cat /tmp/vsp_err.$$ >&2
  exit 1
}

rm -f /tmp/vsp_out.$$ /tmp/vsp_err.$$
echo "test_validate_stampede3_profile.sh passed"
