#!/usr/bin/env bash
# Static regression for scripts/recreate_web_pipeline.sh (no compose required).
set -euo pipefail

: "${USER:?USER must identify the static-test account}"
: "${TMPDIR:=/data/user/${USER}/tmp}"
[[ "${TMPDIR}" == /data/* ]] || {
  echo "static recreate tests require TMPDIR under /data" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECREATE_SCRIPT="${SCRIPT_DIR}/recreate_web_pipeline.sh"
HELPERS="${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"
PIPELINE_TEST="${SCRIPT_DIR}/test_rebuild_pipeline.sh"

if [[ ! -f "${RECREATE_SCRIPT}" ]]; then
  echo "missing ${RECREATE_SCRIPT}" >&2
  exit 1
fi

if ! bash -n "${RECREATE_SCRIPT}"; then
  echo "bash -n failed for recreate_web_pipeline.sh" >&2
  exit 1
fi

if ! grep -q 'compose_frontend_helpers.sh' "${RECREATE_SCRIPT}"; then
  echo "recreate_web_pipeline.sh must source compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! grep -q 'compose_recreate_web_after_image_refresh' "${RECREATE_SCRIPT}"; then
  echo "recreate_web_pipeline.sh must recreate web via helper" >&2
  exit 1
fi

if ! grep -q 'compose_recreate_pipeline_after_image_refresh' "${RECREATE_SCRIPT}"; then
  echo "recreate_web_pipeline.sh must recreate pipeline via helper" >&2
  exit 1
fi

if grep -Eiq 'logs -f|compose logs -f' "${RECREATE_SCRIPT}"; then
  echo "recreate_web_pipeline.sh must not attach follow logs" >&2
  exit 1
fi

if grep -Eq 'compose up[^-]* (web|pipeline)|compose up (web|pipeline)' "${RECREATE_SCRIPT}"; then
  echo "recreate_web_pipeline.sh must not call bare compose up (use recreate helpers)" >&2
  exit 1
fi

if ! grep -q 'compose_up_service_detached' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define compose_up_service_detached" >&2
  exit 1
fi

if ! grep -q -- '--detach --no-deps' "${HELPERS}"; then
  echo "compose_up_service_detached must use --detach --no-deps" >&2
  exit 1
fi

if ! grep -q '${HPCPERFSTATS_COMPOSE_PROJECT}_${service}_1' "${HELPERS}"; then
  echo "service container names must derive from the selected compose project" >&2
  exit 1
fi

if ! grep -q '${HPCPERFSTATS_COMPOSE_PROJECT}_${service}_tmp' "${HELPERS}"; then
  echo "temporary container prefixes must derive from the selected compose project" >&2
  exit 1
fi

if grep -qE 'docker compose up -d (web|pipeline)($|[[:space:]])' "${HELPERS}"; then
  echo "helpers must not use bare 'up -d SERVICE' without --no-deps (use compose_up_service_detached)" >&2
  exit 1
fi

if grep -Eq '"\$\{PODMAN_COMPOSE\[@\]\}" ps[[:space:]]+(web|pipeline|proxy)([[:space:]]|$)' \
  "${RECREATE_SCRIPT}" "${HELPERS}"; then
  echo "podman-compose ps must not receive positional service arguments" >&2
  exit 1
fi

if grep -Eq 'logs[[:space:]].*--tail|logs[[:space:]]+--tail' "${RECREATE_SCRIPT}"; then
  echo "status guidance must filter full logs before tailing matches" >&2
  exit 1
fi

if ! grep -Fq "logs pipeline 2>&1 | grep -Ei 'error|warn|critical|traceback' | tail -40" \
  "${RECREATE_SCRIPT}"; then
  echo "status guidance must show full pipeline logs filtered before tail" >&2
  exit 1
fi

# Keep rebuild_pipeline static suite green too.
bash "${PIPELINE_TEST}"

echo "test_recreate_web_pipeline.sh: all checks passed"
