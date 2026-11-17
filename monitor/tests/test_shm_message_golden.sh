#!/usr/bin/env bash
# Validate the build tree's slug-named shm goldens (tests/expected/shm_*_<slug>.txt).
# Exit 77 (SKIP) when no golden exists for the live capability slug: never fall back to another.
set -euo pipefail

MONITOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-${MONITOR_ROOT}/.build-static}"
REPO_ROOT="$(cd "${MONITOR_ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python"
VALIDATOR="${MONITOR_ROOT}/scripts/validate_shm_messages.py"
EXPECTED_DIR="${MONITOR_ROOT}/tests/expected"

if [[ ! -x "${VENV_PY}" ]] || ! "${VENV_PY}" -c "import sys" >/dev/null 2>&1; then
  VENV_PY="$(command -v python3)"
fi

# Optional: match build-tree slug to slug-named goldens under tests/expected/.
if [[ -d "${BUILD_DIR}" && -f "${BUILD_DIR}/monitor-build-capabilities.json" ]]; then
  CAPS_JSON="${BUILD_DIR}/monitor-build-capabilities.json"
elif [[ -d "${BUILD_DIR}" ]]; then
  echo "Emitting capabilities in ${BUILD_DIR}"
  make -C "${BUILD_DIR}" capabilities
  CAPS_JSON="${BUILD_DIR}/monitor-build-capabilities.json"
else
  echo "SKIP: no build dir ${BUILD_DIR} (exit 77)"
  exit 77
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

echo "=== validate_shm_messages (slug=${SLUG}) ==="
"${VENV_PY}" "${VALIDATOR}" \
  --capabilities "${CAPS_JSON}" \
  --manifest "${MANIFEST}" \
  --shm-dir "${FIXTURE_TMP}" \
  --fixture-dir "${FIXTURE_TMP}" \
  --no-live-spot-check \
  --no-freshness \
  --golden-dir "${EXPECTED_DIR}"
echo "test_shm_message_golden.sh: OK (slug ${SLUG})"
