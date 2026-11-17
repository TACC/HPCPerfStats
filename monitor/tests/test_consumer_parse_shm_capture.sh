#!/bin/sh
# scripts/consumer_parse_check.py on a sent1 capture built from the synthetic fixture: sentinel
# rows parse as -n with no delta, no negative delta. SKIP (77) without the repo .venv.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${PY}"; then
  echo "SKIP: no repo .venv (${PY})"
  exit 77
fi
SYN="${ROOT}/tests/expected/synthetic_fixture"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# shellcheck disable=SC2016 # literal $ in sed script
sed 's/^\$uptime 1000$/&\n$build synthetic_debug_tier1\n$sentinels 1/' "${SYN}/schema" >"${WORK}/schema"
sed 's/^host_net eth0 @full 10 11 12$/host_net eth0 @full 10 -3 12/' "${SYN}/full" >"${WORK}/t0"
sed -e 's/^1234567890\.000000/1234567950.000000/' \
  -e 's/^host_net eth0 @full 10 11 12$/host_net eth0 @full 15 -10 14/' "${SYN}/full" >"${WORK}/t1"

rc=0
"${PY}" "${ROOT}/scripts/consumer_parse_check.py" --schema "${WORK}/schema" "${WORK}/t0" "${WORK}/t1" \
  >"${WORK}/out.txt" || rc=$?
cat "${WORK}/out.txt"
if test "${rc}" = 77; then
  exit 77
fi
test "${rc}" = 0
grep -q '^PASS consumer_parse python (.*, 2 sentinel)' "${WORK}/out.txt"

# Negative control: a sentinel on a row the consumer drops (type not in the schema) is a FAIL.
printf '%s\n' 'host_unknown dev0 @full -3' >>"${WORK}/t1"
if "${PY}" "${ROOT}/scripts/consumer_parse_check.py" --schema "${WORK}/schema" "${WORK}/t0" \
  "${WORK}/t1" >"${WORK}/bad.txt"; then
  echo "FAIL: dropped sentinel row not detected" >&2
  cat "${WORK}/bad.txt" >&2
  exit 1
fi
grep -q 'sentinel rows for 3 tokens' "${WORK}/bad.txt"
echo "test_consumer_parse_shm_capture passed"
