#!/usr/bin/env bash
# Replace web then pipeline in the background and exit. No log attach, no watch.
#
# This does NOT rebuild the image. For a Python/code image rebuild use:
#   ./scripts/rebuild_pipeline.sh
#
# Use when containers are missing / named ``*_tmp*``, or you need a fire-and-forget
# recreate of existing ``localhost/hpcperfstats:latest`` without watching logs.
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

Recreate compose web then pipeline from the CURRENT image (no Podman build).
Uses --detach --no-deps; prints compose ps and exits (does not attach logs).

For an image rebuild: ./scripts/rebuild_pipeline.sh

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
  podman_runtime_require
  if [[ ! -f "${REPO_ROOT}/docker-compose.yaml" ]]; then
    echo "recreate_web_pipeline.sh: docker-compose.yaml not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  cd "${REPO_ROOT}"
  if [[ "${PIPELINE_ONLY}" -eq 0 ]] \
    && ! "${PODMAN_COMPOSE[@]}" config --services 2>/dev/null | grep -qx web; then
    echo "recreate_web_pipeline.sh: compose stack has no web service" >&2
    exit 1
  fi
  if ! "${PODMAN_COMPOSE[@]}" config --services 2>/dev/null | grep -qx pipeline; then
    echo "recreate_web_pipeline.sh: compose stack has no pipeline service" >&2
    exit 1
  fi
}

main() {
  preflight
  export HPCPERFSTATS_SCRIPT_DRY_RUN="${DRY_RUN}"

  echo "NOTE: this script does NOT rebuild the image (no podman build)."
  echo "      It only replaces web/pipeline containers from localhost/hpcperfstats:latest."
  echo "      Image rebuild: ./scripts/rebuild_pipeline.sh"

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
  "${PODMAN_COMPOSE[@]}" ps 2>/dev/null | grep -E 'web|pipeline' || true
  echo "Optional status sample (non-blocking): podman-compose --project-name ${HPCPERFSTATS_COMPOSE_PROJECT} logs pipeline 2>&1 | grep -Ei 'error|warn|critical|traceback' | tail -40"
}

main "$@"
