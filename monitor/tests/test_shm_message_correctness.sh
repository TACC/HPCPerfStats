#!/usr/bin/env bash
# Validate shm message correctness: synthetic fixture always; live slug goldens when present.
set -euo pipefail

MONITOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-${MONITOR_ROOT}/.build-static}"
REPO_ROOT="$(cd "${MONITOR_ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
VALIDATOR="${MONITOR_ROOT}/scripts/validate_shm_messages.py"
SYNTHETIC_DIR="${MONITOR_ROOT}/tests/expected/synthetic_fixture"
EXPECTED_DIR="${MONITOR_ROOT}/tests/expected"

if [[ ! -x "${VENV_PY}" ]]; then
  VENV_PY="$(command -v python3)"
fi

run_validate() {
  local caps="$1"
  local manifest="$2"
  local fixture="$3"
  local label="$4"
  local extra="${5:-}"
  echo "=== validate_shm_messages (${label}) ==="
  "${VENV_PY}" "${VALIDATOR}" \
    --capabilities "${caps}" \
    --manifest "${manifest}" \
    --shm-dir "${fixture}" \
    --fixture-dir "${fixture}" \
    --no-live-spot-check \
    --no-freshness \
    ${extra}
}

# Always exercise Python validator on committed synthetic fixture.
run_validate \
  "${SYNTHETIC_DIR}/monitor-build-capabilities.json" \
  "${SYNTHETIC_DIR}/expectations_synthetic_debug_tier1.json" \
  "${SYNTHETIC_DIR}" \
  "synthetic_fixture"

# Optional: match build-tree slug to slug-named goldens under tests/expected/.
if [[ -d "${BUILD_DIR}" && -f "${BUILD_DIR}/monitor-build-capabilities.json" ]]; then
  CAPS_JSON="${BUILD_DIR}/monitor-build-capabilities.json"
elif [[ -d "${BUILD_DIR}" ]]; then
  echo "Emitting capabilities in ${BUILD_DIR}"
  make -C "${BUILD_DIR}" capabilities
  CAPS_JSON="${BUILD_DIR}/monitor-build-capabilities.json"
else
  echo "No build dir ${BUILD_DIR}; skipping live-slug golden check"
  exit 0
fi

SLUG="$("${VENV_PY}" -c "import json; print(json.load(open('${CAPS_JSON}'))['capability_slug'])")"
GOLDEN_SCHEMA="${EXPECTED_DIR}/shm_schema_${SLUG}.txt"
GOLDEN_FAST="${EXPECTED_DIR}/shm_fast_${SLUG}.txt"
GOLDEN_FULL="${EXPECTED_DIR}/shm_full_${SLUG}.txt"

if [[ ! -f "${GOLDEN_SCHEMA}" && ! -f "${GOLDEN_FAST}" && ! -f "${GOLDEN_FULL}" ]]; then
  echo "SKIP: no golden fixtures for capability_slug=${SLUG} (exit 77)"
  exit 77
fi

MANIFEST="${BUILD_DIR}/expectations_${SLUG}.json"
if [[ ! -f "${MANIFEST}" ]]; then
  SHM_DIR="${HPCPERFSTATS_DEBUG_SHM_DIR:-/dev/shm/hpcperfstatsd-debug}"
  "${VENV_PY}" "${MONITOR_ROOT}/scripts/build_message_expectations.py" \
    --capabilities "${CAPS_JSON}" \
    --shm-dir "${SHM_DIR}" \
    --out "${MANIFEST}" || true
fi

if [[ ! -f "${MANIFEST}" ]]; then
  echo "SKIP: could not build expectations for slug=${SLUG} (exit 77)"
  exit 77
fi

FIXTURE_TMP="$(mktemp -d)"
trap 'rm -rf "${FIXTURE_TMP}"' EXIT
[[ -f "${GOLDEN_SCHEMA}" ]] && cp "${GOLDEN_SCHEMA}" "${FIXTURE_TMP}/schema"
[[ -f "${GOLDEN_FAST}" ]] && cp "${GOLDEN_FAST}" "${FIXTURE_TMP}/fast"
[[ -f "${GOLDEN_FULL}" ]] && cp "${GOLDEN_FULL}" "${FIXTURE_TMP}/full"

run_validate "${CAPS_JSON}" "${MANIFEST}" "${FIXTURE_TMP}" "slug=${SLUG}" \
  "--golden-dir ${EXPECTED_DIR}"
echo "test_shm_message_correctness.sh: OK (synthetic + slug ${SLUG})"
