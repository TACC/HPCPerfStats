#!/usr/bin/env bash
# Rebuild the shared hpcperfstats image (Python/pipeline; no npm), then recreate
# web + pipeline. Leaves db, redis, rabbitmq alone. Does NOT rebuild the proxy
# image — proxy is taken down, then restored via ``up -d web proxy pipeline``.
#
# Flow (minimize downtime — build while old containers keep running):
#   1. Preserve live STATIC_ROOT/frontend into the build context (web still up)
#   2. Build --target hpcperfstats-pipeline-refresh (stack still up on old image)
#   3. Stop pipeline + web; down proxy
#   4. Remove web + pipeline (+ proxy) containers
#   5. docker compose up -d web proxy pipeline  (new image)
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_pipeline.sh
#   ./scripts/rebuild_pipeline.sh --dry-run
#   ./scripts/rebuild_pipeline.sh --build-only
#   ./scripts/rebuild_pipeline.sh --no-start
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"

PIPELINE_BUILD_TARGET="hpcperfstats-pipeline-refresh"
PRESERVE_FRONTEND_DIR="${REPO_ROOT}/.build/pipeline-rebuild-frontend"
FRONTEND_BACKUP_TAR=""
FRONTEND_RESTORE_DIR=""
_PIPELINE_REBUILD_CLEANUP_DONE=0
PIPELINE_STOP_TIMEOUT="${HPCPERFSTATS_PIPELINE_STOP_TIMEOUT:-30}"
WEB_STOP_TIMEOUT="${HPCPERFSTATS_WEB_STOP_TIMEOUT:-30}"
PROXY_STOP_TIMEOUT="${HPCPERFSTATS_PROXY_STOP_TIMEOUT:-30}"
WEB_WAIT_TIMEOUT="${HPCPERFSTATS_WEB_WAIT_TIMEOUT:-600}"

DRY_RUN=0
BUILD_ONLY=0
NO_START=0
SKIP_FRONTEND_VERIFY=0

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_pipeline.sh [options]

Rebuild the shared hpcperfstats image (Python only, no npm) while the stack
stays up, then cut over: down proxy / web / pipeline and

  docker compose up -d web proxy pipeline

Leaves db / db_pg18 / redis / rabbitmq running. Does not rebuild the proxy image.

IMPORTANT: This does NOT ship SPA/OpenAPI/Orval fixes. After frontend changes run:
  ./scripts/rebuild_frontend.sh

Options:
  --dry-run                 Print planned steps only
  --build-only              Preserve frontend + build image; do not stop/start
  --no-start                Build + stop; skip compose up
  --skip-frontend-verify    Skip live SPA shell / fingerprint checks (dev only)
  --pipeline-stop-timeout S Timeout for compose stop pipeline (default 30)
  Env HPCPERFSTATS_WEB_STOP_TIMEOUT    Grace for stop web (default 30)
  Env HPCPERFSTATS_PROXY_STOP_TIMEOUT  Grace for proxy down (default 30)
  -h, --help                Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --build-only)
      BUILD_ONLY=1
      shift
      ;;
    --no-start)
      NO_START=1
      shift
      ;;
    --no-web)
      echo "rebuild_pipeline.sh: --no-web removed; web must be cycled with the shared image" >&2
      exit 2
      ;;
    --skip-frontend-verify)
      SKIP_FRONTEND_VERIFY=1
      shift
      ;;
    --pipeline-stop-timeout)
      shift
      PIPELINE_STOP_TIMEOUT="${1:?--pipeline-stop-timeout requires seconds}"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "rebuild_pipeline.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run_cmd() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] $*"
    return 0
  fi
  "$@"
}

preflight() {
  if [[ ! -f "${REPO_ROOT}/docker-compose.yaml" ]]; then
    echo "rebuild_pipeline.sh: docker-compose.yaml not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  if [[ ! -f "${REPO_ROOT}/Dockerfile" ]]; then
    echo "rebuild_pipeline.sh: Dockerfile not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  cd "${REPO_ROOT}"
  local svc
  for svc in web pipeline; do
    if ! docker compose config --services 2>/dev/null | grep -qx "${svc}"; then
      echo "rebuild_pipeline.sh: compose stack has no ${svc} service" >&2
      exit 1
    fi
  done
}

capture_live_frontend_fingerprint() {
  LIVE_FRONTEND_FINGERPRINT="$(
    fingerprint_in_container web "${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html"
  )"
  echo "Live STATIC_ROOT fingerprint (pre-rebuild): ${LIVE_FRONTEND_FINGERPRINT}"
}

verify_live_frontend_ready() {
  if [[ "${SKIP_FRONTEND_VERIFY}" -eq 1 ]]; then
    echo "WARN: skipping live frontend verification (--skip-frontend-verify)" >&2
    return 0
  fi
  if ! web_service_running; then
    echo "rebuild_pipeline.sh: web is not running; cannot preserve live frontend" >&2
    echo "Start web first, or pass --skip-frontend-verify." >&2
    exit 1
  fi
  verify_spa_shells_via_compose \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "STATIC_ROOT/frontend (pre-rebuild)"
  capture_live_frontend_fingerprint
}

backup_live_frontend_volume() {
  FRONTEND_BACKUP_TAR="$(mktemp /tmp/hps-pipeline-frontend-backup.XXXXXX.tar)"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would backup ${CONTAINER_STATIC_ROOT_FRONTEND} to ${FRONTEND_BACKUP_TAR}"
    return 0
  fi
  echo "Backing up live frontend volume to ${FRONTEND_BACKUP_TAR} ..."
  docker compose exec -T web bash -lc \
    "tar -C '${CONTAINER_STATIC_ROOT_FRONTEND}' -cf /tmp/hps-pipeline-frontend-backup.tar ."
  local container_ref
  container_ref="$(web_container_ref)"
  if compose_cp_supported; then
    docker compose cp "web:/tmp/hps-pipeline-frontend-backup.tar" "${FRONTEND_BACKUP_TAR}"
  elif podman_cli_available; then
    podman cp "${container_ref}":/tmp/hps-pipeline-frontend-backup.tar "${FRONTEND_BACKUP_TAR}"
  else
    echo "rebuild_pipeline.sh: cannot backup frontend volume from web container" >&2
    exit 1
  fi
  docker compose exec -T web bash -lc "rm -f /tmp/hps-pipeline-frontend-backup.tar" || true
}

preserve_frontend_for_build() {
  echo "Extracting live frontend into ${PRESERVE_FRONTEND_DIR} for Docker build ..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would populate ${PRESERVE_FRONTEND_DIR} from web:${CONTAINER_STATIC_ROOT_FRONTEND}"
    return 0
  fi
  extract_container_dir_to_host \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "${PRESERVE_FRONTEND_DIR}"
  verify_spa_shells "${PRESERVE_FRONTEND_DIR}" "pipeline-rebuild preserve dir"
}

restore_frontend_volume_if_drifted() {
  if [[ "${SKIP_FRONTEND_VERIFY}" -eq 1 ]]; then
    return 0
  fi
  if [[ -z "${FRONTEND_BACKUP_TAR:-}" || ! -f "${FRONTEND_BACKUP_TAR}" ]]; then
    return 0
  fi
  local post_fp
  post_fp="$(fingerprint_in_container web "${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html")"
  if [[ "${post_fp}" == "${LIVE_FRONTEND_FINGERPRINT}" ]]; then
    echo "Post-start frontend fingerprint unchanged (${post_fp})"
    return 0
  fi
  echo "WARN: collectstatic changed frontend fingerprint (${LIVE_FRONTEND_FINGERPRINT} -> ${post_fp}); restoring backup volume ..."
  FRONTEND_RESTORE_DIR="$(mktemp -d /tmp/hps-pipeline-frontend-restore.XXXXXX)"
  tar -xf "${FRONTEND_BACKUP_TAR}" -C "${FRONTEND_RESTORE_DIR}"
  copy_tree_into_container_from_dir \
    "${FRONTEND_RESTORE_DIR}" \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "${FRONTEND_RESTORE_DIR}/machine/index.html"
  rm -rf "${FRONTEND_RESTORE_DIR}"
  FRONTEND_RESTORE_DIR=""
  post_fp="$(fingerprint_in_container web "${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html")"
  if [[ "${post_fp}" != "${LIVE_FRONTEND_FINGERPRINT}" ]]; then
    echo "rebuild_pipeline.sh: frontend restore failed (expected ${LIVE_FRONTEND_FINGERPRINT}, got ${post_fp})" >&2
    return 1
  fi
  echo "Restored live frontend volume fingerprint: ${post_fp}"
}

wait_for_web_from_host() {
  local port="${HPCPERFSTATS_WEB_PORT:-8000}"
  local url="http://127.0.0.1:${port}/"
  local waited=0
  echo "Waiting for web on host ${url} (timeout ${WEB_WAIT_TIMEOUT}s) ..."
  while (( waited < WEB_WAIT_TIMEOUT )); do
    if command -v curl >/dev/null 2>&1; then
      if curl -s -o /dev/null -w '%{http_code}' "${url}" 2>/dev/null | grep -qE '^[23]'; then
        echo "web responded on host (${url})"
        return 0
      fi
    elif command -v nc >/dev/null 2>&1; then
      if nc -z 127.0.0.1 "${port}" 2>/dev/null; then
        echo "web port open on host (${port})"
        return 0
      fi
    fi
    sleep 5
    waited=$((waited + 5))
  done
  echo "rebuild_pipeline.sh: timed out waiting for web on host port ${port}" >&2
  return 1
}

build_pipeline_image() {
  echo "Building image target=${PIPELINE_BUILD_TARGET} (no npm; no proxy rebuild) ..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would build image target=${PIPELINE_BUILD_TARGET}"
    return 0
  fi
  build_web_image_with_target "${PIPELINE_BUILD_TARGET}"
}

# Stop/rm pipeline + web; always down proxy (no proxy image rebuild).
# Never touch db / redis / rabbitmq.
stop_and_remove_web_pipeline() {
  echo "Leaving db / redis / rabbitmq running. Not rebuilding proxy image."
  echo "Stopping pipeline (grace ${PIPELINE_STOP_TIMEOUT}s) ..."
  run_cmd docker compose stop -t "${PIPELINE_STOP_TIMEOUT}" pipeline || true
  echo "Stopping web (grace ${WEB_STOP_TIMEOUT}s) ..."
  run_cmd docker compose stop -t "${WEB_STOP_TIMEOUT}" web || true

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would down proxy, then rm web+pipeline+proxy"
    return 0
  fi

  echo "Taking proxy down ..."
  docker compose stop -t "${PROXY_STOP_TIMEOUT}" proxy >/dev/null 2>&1 || true
  compose_podman_rm_service_containers proxy

  echo "Removing web + pipeline containers ..."
  compose_podman_rm_service_containers web pipeline
}

# Proxy is already down. One detached up for web, proxy, and pipeline.
start_web_proxy_pipeline() {
  echo "Starting web, proxy, and pipeline: docker compose up -d web proxy pipeline"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] docker compose up -d web proxy pipeline"
    return 0
  fi
  cd "${REPO_ROOT}"
  docker compose up -d web proxy pipeline
}

cleanup() {
  if [[ "${_PIPELINE_REBUILD_CLEANUP_DONE}" -eq 1 ]]; then
    return 0
  fi
  _PIPELINE_REBUILD_CLEANUP_DONE=1
  cleanup_pipeline_rebuild_scratch \
    "${PRESERVE_FRONTEND_DIR:-}" \
    "${FRONTEND_BACKUP_TAR:-}" \
    "${FRONTEND_RESTORE_DIR:-}"
}

main() {
  preflight

  # Keep running containers up through the (long) image build.
  verify_live_frontend_ready
  backup_live_frontend_volume
  preserve_frontend_for_build
  build_pipeline_image

  if [[ "${BUILD_ONLY}" -eq 1 ]]; then
    echo "Skipping stop/start (--build-only). New image is tagged; containers still on old image."
    return 0
  fi

  stop_and_remove_web_pipeline

  if [[ "${NO_START}" -eq 1 ]]; then
    echo "Skipping container start (--no-start). Stack is down; up when ready:"
    echo "  docker compose up -d web proxy pipeline"
    return 0
  fi

  start_web_proxy_pipeline

  if [[ "${DRY_RUN}" -eq 0 ]]; then
    wait_for_web_from_host || \
      echo "WARN: web host wait failed; containers may still be starting" >&2
    restore_frontend_volume_if_drifted || \
      echo "WARN: frontend restore skipped/failed" >&2
    if [[ "${SKIP_FRONTEND_VERIFY}" -eq 0 ]]; then
      verify_spa_shells_via_compose \
        "${CONTAINER_STATIC_ROOT_FRONTEND}" \
        "STATIC_ROOT/frontend (post-rebuild)" || true
    fi
  fi

  echo "Rebuild complete (built while up → cut over to new image). Status:"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    docker compose ps web pipeline proxy 2>/dev/null || docker compose ps 2>/dev/null || true
  fi
  echo "If you changed frontend/OpenAPI, also run: ./scripts/rebuild_frontend.sh"
}

trap cleanup EXIT
main "$@"
