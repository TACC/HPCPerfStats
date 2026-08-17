#!/usr/bin/env bash
# Rebuild the shared hpcperfstats image (Python/pipeline code) without rerunning npm.
#
# web and pipeline share the hpcperfstats image tag. This script stops pipeline and
# web, builds the hpcperfstats-pipeline-refresh Dockerfile target (preserved frontend
# tree from the live deployment), then recreates web and pipeline. db, redis,
# rabbitmq, and proxy are left running (podman-compose briefly stops proxy while recreating web).
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_pipeline.sh
#   ./scripts/rebuild_pipeline.sh --dry-run
#   ./scripts/rebuild_pipeline.sh --build-only
#   ./scripts/rebuild_pipeline.sh --no-start
#   ./scripts/rebuild_pipeline.sh --no-web
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"

PIPELINE_BUILD_TARGET="hpcperfstats-pipeline-refresh"
PRESERVE_FRONTEND_DIR="${REPO_ROOT}/.build/pipeline-rebuild-frontend"
FRONTEND_BACKUP_TAR=""
PIPELINE_STOP_TIMEOUT="${HPCPERFSTATS_PIPELINE_STOP_TIMEOUT:-300}"
WEB_STOP_TIMEOUT="${HPCPERFSTATS_WEB_STOP_TIMEOUT:-120}"
WEB_WAIT_TIMEOUT="${HPCPERFSTATS_WEB_WAIT_TIMEOUT:-600}"

DRY_RUN=0
BUILD_ONLY=0
NO_START=0
NO_WEB=0
SKIP_FRONTEND_VERIFY=0

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_pipeline.sh [options]

Rebuild web/pipeline image (Python only) without npm frontend-builder. Preserves
live STATIC_ROOT/frontend from the running web container, stops pipeline then web,
builds --target hpcperfstats-pipeline-refresh, and recreates web then pipeline.

IMPORTANT: This does NOT ship SPA/OpenAPI/Orval fixes. After frontend changes run:
  ./scripts/rebuild_frontend.sh
Otherwise the browser keeps the previous /machine/ bundle (stale job-list date filter,
hollow Bokeh Zod, etc.).

Options:
  --dry-run                 Print planned steps only
  --build-only              Preserve frontend + build image; do not stop/start
  --no-start                Stop + build; skip compose up
  --no-web                  Pipeline-only: no running web required; do not start web.
                            Seeds SPA from the existing image (or placeholders).
                            Temporary — run a proper rebuild before bringing web /
                            the full stack back into service.
  --skip-frontend-verify    Skip live SPA shell / fingerprint checks (dev only)
  --pipeline-stop-timeout S Timeout for compose stop pipeline (default 300)
  Env HPCPERFSTATS_WEB_STOP_TIMEOUT  Grace seconds for compose stop web (default 120)
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
      NO_WEB=1
      SKIP_FRONTEND_VERIFY=1
      shift
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

warn_no_web_temporary() {
  cat <<'EOF' >&2
WARN: --no-web is a temporary pipeline-only rebuild (web is not required and will not be started).
Before bringing web / the full stack back into service, rebuild properly with web available:
  ./scripts/rebuild_pipeline.sh
EOF
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
  if ! docker compose config --services 2>/dev/null | grep -qx web; then
    echo "rebuild_pipeline.sh: compose stack has no web service" >&2
    exit 1
  fi
  if ! docker compose config --services 2>/dev/null | grep -qx pipeline; then
    echo "rebuild_pipeline.sh: compose stack has no pipeline service" >&2
    exit 1
  fi
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
    echo "rebuild_pipeline.sh: web is not running; cannot verify live frontend" >&2
    echo "Start web, or pass --no-web for a temporary pipeline-only rebuild." >&2
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

# Copy package static frontend from an existing image (no running web container).
try_extract_frontend_from_image() {
  local image_name="$1"
  local dest="$2"
  local cid=""
  local cli=()

  if podman_cli_available && podman image exists "${image_name}" >/dev/null 2>&1; then
    cli=(podman)
  elif command -v docker >/dev/null 2>&1 \
    && docker image inspect "${image_name}" >/dev/null 2>&1; then
    cli=(docker)
  else
    return 1
  fi

  cid="$("${cli[@]}" create "${image_name}")" || return 1
  if ! "${cli[@]}" cp "${cid}:${CONTAINER_STATIC_FRONTEND}/." "${dest}/"; then
    "${cli[@]}" rm -f "${cid}" >/dev/null 2>&1 || true
    return 1
  fi
  "${cli[@]}" rm -f "${cid}" >/dev/null 2>&1 || true
  [[ -f "${dest}/machine/index.html" && -f "${dest}/pub/index.html" ]]
}

write_placeholder_spa_shells() {
  local dest="$1"
  mkdir -p "${dest}/machine" "${dest}/pub"
  printf '%s\n' '<!doctype html><title>hpcperfstats --no-web placeholder</title>' \
    >"${dest}/machine/index.html"
  cp "${dest}/machine/index.html" "${dest}/pub/index.html"
}

preserve_frontend_without_web() {
  echo "Seeding ${PRESERVE_FRONTEND_DIR} without a running web container ..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would seed ${PRESERVE_FRONTEND_DIR} from image or placeholders"
    return 0
  fi

  mkdir -p "${PRESERVE_FRONTEND_DIR}"
  rm -rf "${PRESERVE_FRONTEND_DIR:?}/"*

  local image_name
  image_name="$(compose_web_image_name)"
  if try_extract_frontend_from_image "${image_name}" "${PRESERVE_FRONTEND_DIR}"; then
    echo "Seeded SPA shells from image ${image_name}"
    verify_spa_shells "${PRESERVE_FRONTEND_DIR}" "pipeline-rebuild preserve dir (--no-web from image)"
    return 0
  fi

  echo "WARN: could not extract SPA from image ${image_name}; writing placeholder shells" >&2
  write_placeholder_spa_shells "${PRESERVE_FRONTEND_DIR}"
  verify_spa_shells "${PRESERVE_FRONTEND_DIR}" "pipeline-rebuild preserve dir (--no-web placeholders)"
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
  local restore_dir
  restore_dir="$(mktemp -d /tmp/hps-pipeline-frontend-restore.XXXXXX)"
  tar -xf "${FRONTEND_BACKUP_TAR}" -C "${restore_dir}"
  copy_tree_into_container_from_dir \
    "${restore_dir}" \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "${restore_dir}/machine/index.html"
  rm -rf "${restore_dir}"
  post_fp="$(fingerprint_in_container web "${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html")"
  if [[ "${post_fp}" != "${LIVE_FRONTEND_FINGERPRINT}" ]]; then
    echo "rebuild_pipeline.sh: frontend restore failed (expected ${LIVE_FRONTEND_FINGERPRINT}, got ${post_fp})" >&2
    exit 1
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
  echo "Building web image target=${PIPELINE_BUILD_TARGET} (no npm frontend-builder) ..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] would build web image target=${PIPELINE_BUILD_TARGET}"
    return 0
  fi
  build_web_image_with_target "${PIPELINE_BUILD_TARGET}"
}

stop_pipeline_only() {
  echo "Stopping pipeline (grace ${PIPELINE_STOP_TIMEOUT}s; --no-web leaves web alone) ..."
  run_cmd docker compose stop -t "${PIPELINE_STOP_TIMEOUT}" pipeline
}

stop_app_containers() {
  echo "Stopping pipeline (grace ${PIPELINE_STOP_TIMEOUT}s) ..."
  run_cmd docker compose stop -t "${PIPELINE_STOP_TIMEOUT}" pipeline
  echo "Stopping web (grace ${WEB_STOP_TIMEOUT}s) ..."
  run_cmd docker compose stop -t "${WEB_STOP_TIMEOUT}" web
}

start_pipeline_only() {
  export HPCPERFSTATS_SCRIPT_DRY_RUN="${DRY_RUN}"
  echo "Recreating pipeline on refreshed image (--no-web; web not started) ..."
  compose_recreate_pipeline_after_image_refresh
  echo "Pipeline-only image refresh complete."
}

start_app_containers() {
  export HPCPERFSTATS_SCRIPT_DRY_RUN="${DRY_RUN}"
  echo "Recreating web on refreshed image ..."
  compose_recreate_web_after_image_refresh
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    wait_for_web_from_host
  fi
  restore_frontend_volume_if_drifted
  compose_restore_proxy_if_was_running
  if [[ "${SKIP_FRONTEND_VERIFY}" -eq 0 && "${DRY_RUN}" -eq 0 ]]; then
    verify_spa_shells_via_compose \
      "${CONTAINER_STATIC_ROOT_FRONTEND}" \
      "STATIC_ROOT/frontend (post-rebuild)"
    if docker compose exec -T proxy true >/dev/null 2>&1; then
      verify_proxy_frontend_matches_web || true
    fi
  fi
  echo "Recreating pipeline on refreshed image ..."
  compose_recreate_pipeline_after_image_refresh
  echo "Pipeline image refresh complete (SPA bundle unchanged)."
  echo "If you changed frontend/OpenAPI, also run: ./scripts/rebuild_frontend.sh"
}

cleanup() {
  if [[ -n "${FRONTEND_BACKUP_TAR:-}" && -f "${FRONTEND_BACKUP_TAR}" ]]; then
    rm -f "${FRONTEND_BACKUP_TAR}"
  fi
}

main() {
  preflight

  if [[ "${NO_WEB}" -eq 1 ]]; then
    warn_no_web_temporary
    preserve_frontend_without_web
  else
    verify_live_frontend_ready
    backup_live_frontend_volume
    preserve_frontend_for_build
  fi

  if [[ "${BUILD_ONLY}" -eq 0 ]]; then
    if [[ "${NO_WEB}" -eq 1 ]]; then
      stop_pipeline_only
    else
      stop_app_containers
    fi
  fi

  build_pipeline_image

  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 ]]; then
    echo "Skipping container start (--build-only or --no-start)."
    if [[ "${NO_WEB}" -eq 1 ]]; then
      warn_no_web_temporary
    fi
    cleanup
    return 0
  fi

  if [[ "${NO_WEB}" -eq 1 ]]; then
    start_pipeline_only
    cleanup
    warn_no_web_temporary
    echo "Pipeline-only rebuild complete. Confirm with:"
    echo "  docker compose logs -f pipeline | grep -E 'pending reconcile cap|startup maintenance idle|Number of host stats files'"
    return 0
  fi

  start_app_containers
  cleanup
  echo "Pipeline rebuild complete. Confirm ingest with:"
  echo "  docker compose logs -f pipeline | grep -E 'pending reconcile cap|startup maintenance idle|Number of host stats files'"
}

trap cleanup EXIT
main "$@"
