#!/usr/bin/env bash
# Build the React/Next.js SPA and refresh nginx static assets without restarting pipeline.
#
# Pipeline and web share the hpcperfstats image, but ingest keeps running because this
# script never rebuilds that image or restarts the pipeline service. Fresh bundles are
# copied into the running web container's STATIC_ROOT frontend tree (shared
# staticfiles_data volume) that nginx (proxy) serves as /static/ and /machine/.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_frontend.sh
#   ./scripts/rebuild_frontend.sh --skip-npm-ci
#   ./scripts/rebuild_frontend.sh --docker-build
#   ./scripts/rebuild_frontend.sh --no-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"

FRONTEND_DIR="${REPO_ROOT}/hpcperfstats/site/frontend"
STATIC_FRONTEND="${REPO_ROOT}/hpcperfstats/site/hpcperfstats_site/static/frontend"
NODE_IMAGE="node:26.5.0-alpine3.23"

SKIP_NPM_CI=0
DEPLOY=1
DOCKER_BUILD=0

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_frontend.sh [options]

Build the SPA (npm run build:prod) and, when the compose web service is running, copy
artifacts into web STATIC_ROOT/frontend (nginx volume) so new hashes are served
immediately. The pipeline service is never stopped or restarted.

Options:
  --skip-npm-ci    Skip "npm ci" before build (use when node_modules is current)
  --docker-build   Build with a Node container instead of host npm
  --no-deploy      Build only; do not copy into web STATIC_ROOT/frontend
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-npm-ci)
      SKIP_NPM_CI=1
      shift
      ;;
    --docker-build)
      DOCKER_BUILD=1
      shift
      ;;
    --no-deploy)
      DEPLOY=0
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "rebuild_frontend.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  echo "rebuild_frontend.sh: frontend not found at ${FRONTEND_DIR}" >&2
  exit 1
fi

build_on_host() {
  local git_commit
  if ! command -v npm >/dev/null 2>&1; then
    echo "rebuild_frontend.sh: npm not found on PATH; retry with --docker-build" >&2
    exit 1
  fi
  git_commit="$(resolve_hpcperfstats_git_commit)"
  export HPCPERFSTATS_GIT_COMMIT="${git_commit}"
  cd "${FRONTEND_DIR}"
  if [[ "${SKIP_NPM_CI}" -eq 0 ]]; then
    npm ci
  fi
  NEXT_TELEMETRY_DISABLED=1 HPCPERFSTATS_GIT_COMMIT="${git_commit}" npm run build:prod
}

build_in_docker() {
  local npm_ci_cmd="npm ci"
  local git_commit
  if [[ "${SKIP_NPM_CI}" -ne 0 ]]; then
    npm_ci_cmd="true"
  fi
  git_commit="$(resolve_hpcperfstats_git_commit)"
  export HPCPERFSTATS_GIT_COMMIT="${git_commit}"
  docker run --rm \
    -v "${REPO_ROOT}":/home/hpcperfstats \
    -w /home/hpcperfstats/hpcperfstats/site/frontend \
    -e NEXT_TELEMETRY_DISABLED=1 \
    -e "HPCPERFSTATS_GIT_COMMIT=${git_commit}" \
    "${NODE_IMAGE}" \
    sh -lc "${npm_ci_cmd} && npm run build:prod"
}

verify_build_output() {
  verify_spa_shells "${STATIC_FRONTEND}" "host build"
  verify_job_list_date_filter_in_spa_build "${STATIC_FRONTEND}" "host build"
  print_deploy_fingerprint "host build" "${STATIC_FRONTEND}/machine/index.html"
  echo "Built frontend static export: ${STATIC_FRONTEND}"
}

copy_tree_via_staged_tar() {
  copy_tree_via_staged_tar_from_dir \
    "${STATIC_FRONTEND}" \
    "$1" \
    "${STATIC_FRONTEND}/machine/index.html"
}

copy_tree_via_compose_cp() {
  copy_tree_via_compose_cp_from_dir "$1" "$2"
}

deploy_frontend_via_staged_volume() {
  echo "rebuild_frontend.sh: podman-compose — staged tar direct to nginx volume (staticfiles_data)"
  copy_tree_via_staged_tar "${CONTAINER_STATIC_ROOT_FRONTEND}"
  echo "rebuild_frontend.sh: syncing image source tree for future image rebuilds ..."
  copy_tree_via_staged_tar "${CONTAINER_STATIC_FRONTEND}"
}

copy_frontend_into_web() {
  cd "${REPO_ROOT}"

  if compose_backend_is_podman; then
    deploy_frontend_via_staged_volume
    return
  fi

  # nginx (proxy) reads STATIC_ROOT/frontend from the shared staticfiles_data volume.
  copy_tree_into_container_from_dir \
    "${STATIC_FRONTEND}" \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "${STATIC_FRONTEND}/machine/index.html"

  # Keep image source tree in sync for future collectstatic / container rebuilds.
  copy_tree_into_container_from_dir \
    "${STATIC_FRONTEND}" \
    "${CONTAINER_STATIC_FRONTEND}" \
    "${STATIC_FRONTEND}/machine/index.html"
}

verify_container_path_matches_host() {
  local container_dir="$1"
  local label="$2"
  local host_probe="${STATIC_FRONTEND}/machine/index.html"
  local container_probe="${container_dir}/machine/index.html"
  local host_sha container_sha

  host_sha="$(file_sha256 "${host_probe}")"
  container_sha="$(sha256_in_web_container "${container_probe}")"

  if [[ -z "${container_sha}" ]]; then
    echo "rebuild_frontend.sh: ${label} probe missing after copy: ${container_probe}" >&2
    return 1
  fi

  if [[ "${host_sha}" != "${container_sha}" ]]; then
    echo "rebuild_frontend.sh: ${label} does not match host after copy" >&2
    echo "  host:      ${host_sha}  (${host_probe})" >&2
    echo "  container: ${container_sha}  (${container_probe})" >&2
    return 1
  fi

  echo "Verified ${label} matches host (${container_probe})"
}

verify_container_file_count_matches_host() {
  local container_dir="$1"
  local label="$2"
  local host_count container_count

  host_count="$(count_files_under "${STATIC_FRONTEND}")"
  container_count="$(count_files_in_web_container "${container_dir}")"

  if [[ "${host_count}" != "${container_count}" ]]; then
    echo "rebuild_frontend.sh: ${label} file count mismatch (host ${host_count}, web ${container_count})" >&2
    return 1
  fi

  echo "Verified ${label} file count matches host (${host_count} files)"
}

verify_container_frontend_matches_host() {
  verify_container_path_matches_host \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "STATIC_ROOT/frontend (nginx volume)"
}

deploy_to_compose() {
  cd "${REPO_ROOT}"
  if ! web_service_running; then
    echo "web service is not running; skipping deploy (host tree is updated)." >&2
    echo "Start web later and re-run without --no-deploy, or run collectstatic manually." >&2
    return 0
  fi

  trap 'echo "rebuild_frontend.sh: deploy step failed." >&2; compose_backend_is_podman && print_podman_deploy_fallback; exit 1' ERR

  echo "Copying built assets into web:${CONTAINER_STATIC_ROOT_FRONTEND} (nginx staticfiles volume) ..."
  copy_frontend_into_web
  verify_container_frontend_matches_host
  verify_container_file_count_matches_host \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "STATIC_ROOT/frontend (nginx volume)"
  verify_spa_shells_via_compose \
    "${CONTAINER_STATIC_ROOT_FRONTEND}" \
    "STATIC_ROOT/frontend"
  verify_proxy_frontend_matches_web
  verify_proxy_file_count_matches_web
  print_container_deploy_fingerprint \
    "web STATIC_ROOT" \
    web \
    "${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html"
  print_container_deploy_fingerprint \
    "proxy nginx volume" \
    "proxy" \
    "${PROXY_STATIC_ROOT_FRONTEND}/machine/index.html"
  print_deploy_fingerprint "host build (expected)" "${STATIC_FRONTEND}/machine/index.html"

  trap - ERR

  echo "Frontend deploy complete. Use the proxy service (ports 80/443), not web:8000 — Gunicorn does not serve /machine/."
  echo "Hard-refresh the browser and confirm the deploy fingerprint above matches page-*.js in DevTools Network."
}

main() {
  if [[ "${DOCKER_BUILD}" -eq 1 ]]; then
    build_in_docker
  else
    build_on_host
  fi
  verify_build_output

  if [[ "${DEPLOY}" -eq 1 ]]; then
    deploy_to_compose
  else
    echo "Skipping deploy (--no-deploy)."
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
