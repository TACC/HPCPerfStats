#!/usr/bin/env bash
# Rebuild only the proxy (nginx) image and recreate only the proxy container.
#
# The image is built while the old proxy keeps serving, then the proxy
# container is replaced with --no-deps so web, pipeline, db, valkey and
# rabbitmq are never stopped. Expect a few seconds of proxy downtime during
# the swap. Build on the host that runs the proxy: proxy.Dockerfile bakes
# -march=native.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_proxy.sh
#   ./scripts/rebuild_proxy.sh --build-only
#   ./scripts/rebuild_proxy.sh --dry-run
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"

PROXY_IMAGE="hpcperfstats-proxy"
PROXY_CONTAINER="${HPCPERFSTATS_COMPOSE_PROJECT}_proxy_1"
: "${HPCPERFSTATS_PROXY_START_TIMEOUT:=60}"

DRY_RUN=0
BUILD_ONLY=0

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_proxy.sh [options]

Build the proxy image (services-conf/proxy.Dockerfile) while the current
proxy keeps running, then recreate only the proxy container (--no-deps).
Other services are not touched.

Options:
  --build-only   Build the image; do not recreate the container
  --dry-run      Print planned steps only
  -h, --help     Show this help

Env:
  HPCPERFSTATS_PROXY_START_TIMEOUT  Seconds to wait for the new proxy (default 60)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only)
      BUILD_ONLY=1
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
      echo "rebuild_proxy.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

preflight() {
  podman_runtime_require
  cd "${REPO_ROOT}"
  if ! "${PODMAN_COMPOSE[@]}" config --services 2>/dev/null | grep -qx proxy; then
    echo "rebuild_proxy.sh: compose stack has no proxy service (is docker-compose.yaml present?)" >&2
    exit 1
  fi
}

build_proxy_image() {
  local cmd=("${PODMAN[@]}" build -f "${REPO_ROOT}/services-conf/proxy.Dockerfile" -t "${PROXY_IMAGE}" "${REPO_ROOT}")
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] ${cmd[*]}"
    return 0
  fi
  echo "Building ${PROXY_IMAGE} (current proxy keeps running) ..."
  "${cmd[@]}"
}

recreate_proxy() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] ${PODMAN_COMPOSE[*]} up --detach --no-deps --no-build --force-recreate proxy"
    return 0
  fi
  if ! compose_service_running web; then
    echo "rebuild_proxy.sh: WARNING: web is not running; nginx may not start until web is up." >&2
  fi
  compose_up_service_detached proxy --no-build --force-recreate
}

wait_for_proxy_running() {
  local waited=0
  until compose_service_running proxy; do
    if [[ "${waited}" -ge "${HPCPERFSTATS_PROXY_START_TIMEOUT}" ]]; then
      echo "rebuild_proxy.sh: proxy not running after ${HPCPERFSTATS_PROXY_START_TIMEOUT}s" >&2
      "${PODMAN[@]}" logs --tail 30 "${PROXY_CONTAINER}" >&2 || true
      echo "rebuild_proxy.sh: retry the start with: ${PODMAN_COMPOSE[*]} up --detach --no-deps --no-build proxy" >&2
      return 1
    fi
    sleep 2
    waited=$((waited + 2))
  done
}

verify_proxy() {
  local want have
  want="$("${PODMAN[@]}" image inspect --format '{{.Id}}' "${PROXY_IMAGE}")"
  have="$("${PODMAN[@]}" inspect --format '{{.Image}}' "${PROXY_CONTAINER}")"
  if [[ "${want}" != "${have}" ]]; then
    echo "rebuild_proxy.sh: ${PROXY_CONTAINER} runs image ${have}, expected new ${PROXY_IMAGE} ${want}" >&2
    return 1
  fi
  "${PODMAN[@]}" exec "${PROXY_CONTAINER}" nginx -t
  "${PODMAN[@]}" exec "${PROXY_CONTAINER}" nginx -v
  echo "Proxy recreated from ${PROXY_IMAGE} ${want:0:12}."
}

main() {
  preflight
  build_proxy_image
  if [[ "${BUILD_ONLY}" -eq 1 ]]; then
    echo "Image built (--build-only); proxy container not recreated."
    return 0
  fi
  recreate_proxy
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] done"
    return 0
  fi
  wait_for_proxy_running
  verify_proxy
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
