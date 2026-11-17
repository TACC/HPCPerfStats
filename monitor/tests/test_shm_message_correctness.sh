#!/usr/bin/env bash
# Validate shm message correctness on the committed synthetic fixtures (knob-0 capture, a
# sentinel-on variant, cross-sample). The live-slug golden check is test_shm_message_golden.sh.
set -euo pipefail

MONITOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${MONITOR_ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
VALIDATOR="${MONITOR_ROOT}/scripts/validate_shm_messages.py"
SYNTHETIC_DIR="${MONITOR_ROOT}/tests/expected/synthetic_fixture"

if [[ ! -x "${VENV_PY}" ]] || ! "${VENV_PY}" -c "import sys" >/dev/null 2>&1; then
  VENV_PY="$(command -v python3)"
fi

run_validate() {
  local caps="$1"
  local manifest="$2"
  local fixture="$3"
  local label="$4"
  shift 4
  echo "=== validate_shm_messages (${label}) ==="
  "${VENV_PY}" "${VALIDATOR}" \
    --capabilities "${caps}" \
    --manifest "${manifest}" \
    --shm-dir "${fixture}" \
    --fixture-dir "${fixture}" \
    --no-live-spot-check \
    --no-freshness \
    "$@"
}

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# The committed fixture has no $sentinels banner (emit_value_sentinels 0): sentinel layers SKIP.
run_validate \
  "${SYNTHETIC_DIR}/monitor-build-capabilities.json" \
  "${SYNTHETIC_DIR}/expectations_synthetic_debug_tier1.json" \
  "${SYNTHETIC_DIR}" \
  "synthetic_fixture" | tee "${WORK}/knob0.txt"
grep -q '^SKIP sentinel layers' "${WORK}/knob0.txt"
if grep -q '^PASS sentinel' "${WORK}/knob0.txt"; then
  echo "FAIL: knob-0 capture reported a sentinel PASS" >&2
  exit 1
fi

# Sentinel-on variant: banner plus catalog tokens validate and are counted per code.
mkdir "${WORK}/sent1"
cp "${SYNTHETIC_DIR}"/fast "${WORK}/sent1/"
# shellcheck disable=SC2016 # literal $ in sed script
sed 's/^\$uptime 1000$/&\n$sentinels 1/' "${SYNTHETIC_DIR}/schema" >"${WORK}/sent1/schema"
sed 's/^host_net eth0 @full 10 11 12$/host_net eth0 @full 10 -3 -10/' "${SYNTHETIC_DIR}/full" \
  >"${WORK}/sent1/full"
run_validate \
  "${SYNTHETIC_DIR}/monitor-build-capabilities.json" \
  "${SYNTHETIC_DIR}/expectations_synthetic_debug_tier1.json" \
  "${WORK}/sent1" \
  "synthetic_fixture sent1" | tee "${WORK}/sent1.txt"
grep -q '^PASS sentinel banner v1' "${WORK}/sent1.txt"
grep -q '^NOTE sentinel full: NOT_BUILT=1 READ_FAILED=1' "${WORK}/sent1.txt"

# An out-of-catalog negative value must fail row validation.
sed 's/ -10$/ -99/' "${WORK}/sent1/full" >"${WORK}/sent1/full.bad" && mv "${WORK}/sent1/full.bad" "${WORK}/sent1/full"
if run_validate \
  "${SYNTHETIC_DIR}/monitor-build-capabilities.json" \
  "${SYNTHETIC_DIR}/expectations_synthetic_debug_tier1.json" \
  "${WORK}/sent1" \
  "synthetic_fixture -99" >"${WORK}/bad.txt" 2>&1; then
  echo "FAIL: -99 accepted" >&2
  exit 1
fi
grep -q 'neither unsigned nor a catalog sentinel' "${WORK}/bad.txt"

CROSS_SAMPLE_FIXTURE="${MONITOR_ROOT}/tests/expected/cross_sample_fixture"
echo "=== validate_shm_messages (cross_sample_fixture) ==="
"${VENV_PY}" "${VALIDATOR}" \
  --capabilities "${SYNTHETIC_DIR}/monitor-build-capabilities.json" \
  --manifest "${SYNTHETIC_DIR}/expectations_synthetic_debug_tier1.json" \
  --shm-dir "${SYNTHETIC_DIR}" \
  --fixture-dir "${SYNTHETIC_DIR}" \
  --cross-sample-fixture-dir "${CROSS_SAMPLE_FIXTURE}" \
  --consumer-parse-check \
  --no-live-spot-check \
  --no-freshness | tee "${WORK}/cross.txt"
grep -Eq '^(PASS|SKIP) consumer_parse' "${WORK}/cross.txt"
echo "test_shm_message_correctness.sh: OK (synthetic, sent1, cross_sample)"
