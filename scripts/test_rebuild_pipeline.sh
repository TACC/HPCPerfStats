#!/usr/bin/env bash
# Static regression for scripts/rebuild_pipeline.sh (no compose required).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_SCRIPT="${SCRIPT_DIR}/rebuild_pipeline.sh"
HELPERS="${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"
FRONTEND_SCRIPT="${SCRIPT_DIR}/rebuild_frontend.sh"

if [[ ! -f "${PIPELINE_SCRIPT}" ]]; then
  echo "missing ${PIPELINE_SCRIPT}" >&2
  exit 1
fi

if [[ ! -f "${HELPERS}" ]]; then
  echo "missing ${HELPERS}" >&2
  exit 1
fi

if ! bash -n "${PIPELINE_SCRIPT}"; then
  echo "bash -n failed for rebuild_pipeline.sh" >&2
  exit 1
fi

if ! bash -n "${HELPERS}"; then
  echo "bash -n failed for compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! grep -q 'compose_frontend_helpers.sh' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must source compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! grep -q 'hpcperfstats-pipeline-refresh' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must use hpcperfstats-pipeline-refresh build target" >&2
  exit 1
fi

if ! grep -q 'docker compose build web --target' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must build web with explicit --target" >&2
  exit 1
fi

if ! grep -q 'docker compose stop -t.* pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must stop pipeline with grace timeout" >&2
  exit 1
fi

if ! grep -q 'docker compose stop web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must stop web" >&2
  exit 1
fi

pipeline_line="$(grep -n 'docker compose stop.*pipeline' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
web_line="$(grep -n 'docker compose stop web' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
if [[ -z "${pipeline_line}" || -z "${web_line}" || "${pipeline_line}" -ge "${web_line}" ]]; then
  echo "rebuild_pipeline.sh must stop pipeline before web (pipeline line ${pipeline_line:-?}, web line ${web_line:-?})" >&2
  exit 1
fi

if ! grep -q 'docker compose up -d web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must start web before pipeline" >&2
  exit 1
fi

web_up_line="$(grep -n 'docker compose up -d web' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
pipe_up_line="$(grep -n 'docker compose up -d pipeline' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
if [[ -z "${web_up_line}" || -z "${pipe_up_line}" || "${web_up_line}" -ge "${pipe_up_line}" ]]; then
  echo "rebuild_pipeline.sh must start web before pipeline" >&2
  exit 1
fi

if grep -Eiq '\bnpm ci\b|\bnpm run build\b' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not invoke npm ci or npm run build" >&2
  exit 1
fi

if grep -q 'docker compose build.*proxy' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not rebuild proxy service" >&2
  exit 1
fi

if ! grep -q 'compose_frontend_helpers.sh' "${FRONTEND_SCRIPT}"; then
  echo "rebuild_frontend.sh must source compose_frontend_helpers.sh" >&2
  exit 1
fi

echo "test_rebuild_pipeline.sh: all checks passed"
