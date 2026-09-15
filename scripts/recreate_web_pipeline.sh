#!/usr/bin/env bash
# Replace web then pipeline in the background and exit. No log attach, no watch.
#
# Use after an image rebuild left containers missing / named ``*_tmp*``, or when
# you need a fire-and-forget recreate without a foreground compose up or
# following container logs.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/recreate_web_pipeline.sh
#   ./scripts/recreate_web_pipeline.sh --pipeline-only
#   ./scripts/recreate_web_pipeline.sh --dry-run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"

DRY_RUN=0
PIPELINE_ONLY=0

usage() {
  cat <<'EOF'
Usage: scripts/recreate_web_pipeline.sh [options]

Recreate compose ``web`` then ``pipeline`` detached (--detach --no-deps).
Prints ``compose ps`` and exits — does NOT attach logs or wait on supervisord.

Options:
  --pipeline-only   Skip web; recreate pipeline only
  --dry-run         Print planned steps only
  -h, --help        Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pipeline-only)
      PIPELINE_ONLY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "recreate_web_pipeline.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

preflight() {
  if [[ ! -f "${REPO_ROOT}/docker-compose.yaml" ]]; then
    echo "recreate_web_pipeline.sh: docker-compose.yaml not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  cd "${REPO_ROOT}"
  if [[ "${PIPELINE_ONLY}" -eq 0 ]] \
    && ! docker compose config --services 2>/dev/null | grep -qx web; then
    echo "recreate_web_pipeline.sh: compose stack has no web service" >&2
    exit 1
  fi
  if ! docker compose config --services 2>/dev/null | grep -qx pipeline; then
    echo "recreate_web_pipeline.sh: compose stack has no pipeline service" >&2
    exit 1
  fi
}

main() {
  preflight
  export HPCPERFSTATS_SCRIPT_DRY_RUN="${DRY_RUN}"

  if [[ "${PIPELINE_ONLY}" -eq 0 ]]; then
    echo "Recreating web (detached) ..."
    compose_recreate_web_after_image_refresh
    compose_restore_proxy_if_was_running
  else
    echo "Skipping web (--pipeline-only)"
  fi

  echo "Recreating pipeline (detached) ..."
  compose_recreate_pipeline_after_image_refresh

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] done"
    return 0
  fi

  echo "Detached recreate finished. Container status:"
  docker compose ps web pipeline 2>/dev/null || docker compose ps | grep -E 'web|pipeline' || true
  echo "Optional status sample (non-blocking): docker compose logs --tail=40 pipeline"
}

main "$@"
