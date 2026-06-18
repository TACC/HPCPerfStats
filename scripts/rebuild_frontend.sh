#!/usr/bin/env bash
# Build the React/Next.js SPA and refresh nginx static assets without restarting pipeline.
#
# Pipeline and web share the hpcperfstats image, but ingest keeps running because this
# script never rebuilds that image or restarts the pipeline service. Fresh bundles are
# copied into the running web container and collectstatic updates the shared
# staticfiles_data volume that nginx (proxy) serves as /static/ and /machine/.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_frontend.sh
#   ./scripts/rebuild_frontend.sh --skip-npm-ci
#   ./scripts/rebuild_frontend.sh --docker-build
#   ./scripts/rebuild_frontend.sh --no-deploy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/hpcperfstats/site/frontend"
STATIC_FRONTEND="${REPO_ROOT}/hpcperfstats/site/hpcperfstats_site/static/frontend"
CONTAINER_STATIC_FRONTEND="/home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend"
NODE_IMAGE="node:26.3.0-alpine3.23"

# Next static export shells nginx serves (see services-conf/nginx-static-files.conf).
REQUIRED_SPA_SHELLS=(
  "machine/index.html"
  "pub/index.html"
)

verify_spa_shells() {
  local base_dir="$1"
  local label="${2:-${base_dir}}"
  local missing=()
  local rel

  for rel in "${REQUIRED_SPA_SHELLS[@]}"; do
    if [[ ! -f "${base_dir}/${rel}" ]]; then
      missing+=("${base_dir}/${rel}")
    fi
  done

  if ((${#missing[@]} > 0)); then
    echo "rebuild_frontend.sh: build did not produce required SPA shell(s) under ${label}:" >&2
    for path in "${missing[@]}"; do
      echo "  missing: ${path}" >&2
    done
    return 1
  fi

  echo "Verified SPA shells under ${label}: ${REQUIRED_SPA_SHELLS[*]}"
}

SKIP_NPM_CI=0
DEPLOY=1
DOCKER_BUILD=0

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_frontend.sh [options]

Build the SPA (npm run build) and, when the compose web service is running, copy
artifacts into that container and run collectstatic so nginx serves new hashes
immediately. The pipeline service is never stopped or restarted.

Options:
  --skip-npm-ci    Skip "npm ci" before build (use when node_modules is current)
  --docker-build   Build with a Node container instead of host npm
  --no-deploy      Build only; do not copy into web or run collectstatic
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
  if ! command -v npm >/dev/null 2>&1; then
    echo "rebuild_frontend.sh: npm not found on PATH; retry with --docker-build" >&2
    exit 1
  fi
  cd "${FRONTEND_DIR}"
  if [[ "${SKIP_NPM_CI}" -eq 0 ]]; then
    npm ci
  fi
  NEXT_TELEMETRY_DISABLED=1 npm run build
}

build_in_docker() {
  local npm_ci_cmd="npm ci"
  if [[ "${SKIP_NPM_CI}" -ne 0 ]]; then
    npm_ci_cmd="true"
  fi
  docker run --rm \
    -v "${REPO_ROOT}:/home/hpcperfstats" \
    -w /home/hpcperfstats/hpcperfstats/site/frontend \
    -e NEXT_TELEMETRY_DISABLED=1 \
    "${NODE_IMAGE}" \
    sh -lc "${npm_ci_cmd} && npm run build"
}

verify_build_output() {
  verify_spa_shells "${STATIC_FRONTEND}"
  echo "Built frontend static export: ${STATIC_FRONTEND}"
}

web_service_running() {
  cd "${REPO_ROOT}"
  docker compose exec -T web true >/dev/null 2>&1
}

web_container_id() {
  cd "${REPO_ROOT}"
  docker compose ps -q web 2>/dev/null | head -n 1
}

compose_cp_supported() {
  cd "${REPO_ROOT}"
  docker compose cp --help >/dev/null 2>&1
}

podman_cli_available() {
  command -v podman >/dev/null 2>&1
}

file_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
  else
    shasum -a 256 "${path}" | awk '{print $1}'
  fi
}

reset_container_frontend_source_dir() {
  local cid="$1"
  local reset_cmd="rm -rf '${CONTAINER_STATIC_FRONTEND}' && mkdir -p '${CONTAINER_STATIC_FRONTEND}'"
  if [[ -n "${cid}" ]] && podman_cli_available; then
    podman exec "${cid}" bash -lc "${reset_cmd}"
  else
    docker compose exec -T web bash -lc "${reset_cmd}"
  fi
}

copy_frontend_via_compose_cp() {
  docker compose cp "${STATIC_FRONTEND}/." "web:${CONTAINER_STATIC_FRONTEND}/"
}

copy_frontend_via_podman_cp() {
  local cid="$1"
  podman cp "${STATIC_FRONTEND}/." "${cid}:${CONTAINER_STATIC_FRONTEND}/"
}

copy_frontend_via_podman_exec_tar() {
  local cid="$1"
  tar -C "${STATIC_FRONTEND}" -cf - . | podman exec -i "${cid}" tar -xf - -C "${CONTAINER_STATIC_FRONTEND}"
}

copy_frontend_into_web() {
  cd "${REPO_ROOT}"
  local cid
  cid="$(web_container_id)"

  if compose_cp_supported; then
    echo "rebuild_frontend.sh: copying via docker compose cp" >&2
    copy_frontend_via_compose_cp
    return
  fi

  if ! podman_cli_available; then
    echo "rebuild_frontend.sh: compose cp unavailable and podman not on PATH; cannot deploy into web" >&2
    exit 1
  fi

  if [[ -z "${cid}" ]]; then
    echo "rebuild_frontend.sh: web container id not found (is the web service running?)" >&2
    exit 1
  fi

  echo "rebuild_frontend.sh: compose cp unavailable; resetting container source dir" >&2
  reset_container_frontend_source_dir "${cid}"

  if copy_frontend_via_podman_cp "${cid}"; then
    echo "rebuild_frontend.sh: copied frontend tree via podman cp" >&2
    return
  fi

  echo "rebuild_frontend.sh: podman cp failed; trying podman exec -i tar pipe" >&2
  reset_container_frontend_source_dir "${cid}"
  copy_frontend_via_podman_exec_tar "${cid}"
}

verify_container_frontend_matches_host() {
  local host_probe="${STATIC_FRONTEND}/machine/index.html"
  local container_probe="${CONTAINER_STATIC_FRONTEND}/machine/index.html"
  local host_sha container_sha

  host_sha="$(file_sha256 "${host_probe}")"
  container_sha="$(
    docker compose exec -T web bash -lc \
      "if [[ ! -f '${container_probe}' ]]; then exit 2; fi; sha256sum '${container_probe}' | awk '{print \$1}'"
  )"

  if [[ -z "${container_sha}" ]]; then
    echo "rebuild_frontend.sh: container frontend probe missing after copy: ${container_probe}" >&2
    return 1
  fi

  if [[ "${host_sha}" != "${container_sha}" ]]; then
    echo "rebuild_frontend.sh: container frontend does not match host after copy" >&2
    echo "  host:      ${host_sha}  (${host_probe})" >&2
    echo "  container: ${container_sha}  (${container_probe})" >&2
    return 1
  fi

  echo "Verified container frontend matches host (${container_probe})"
}

deploy_to_compose() {
  cd "${REPO_ROOT}"
  if ! web_service_running; then
    echo "web service is not running; skipping deploy (host tree is updated)." >&2
    echo "Start web later and re-run without --no-deploy, or run collectstatic manually." >&2
    return 0
  fi

  echo "Copying built assets into web:${CONTAINER_STATIC_FRONTEND} ..."
  copy_frontend_into_web
  verify_container_frontend_matches_host

  echo "Running collectstatic in web (updates staticfiles_data for nginx) ..."
  docker compose exec -T web bash -lc '
set -euo pipefail
export STATIC_ROOT="${STATIC_ROOT:-/home/hpcperfstats/staticfiles}"
rm -rf "${STATIC_ROOT}/frontend"
/usr/local/bin/python3 hpcperfstats/site/manage.py collectstatic --noinput
frontend_root="${STATIC_ROOT}/frontend"
required=(machine/index.html pub/index.html)
missing=()
for rel in "${required[@]}"; do
  if [[ ! -f "${frontend_root}/${rel}" ]]; then
    missing+=("${frontend_root}/${rel}")
  fi
done
if ((${#missing[@]} > 0)); then
  echo "ERROR: collectstatic did not produce required SPA shell(s):" >&2
  for path in "${missing[@]}"; do
    echo "  missing: ${path}" >&2
  done
  exit 1
fi
echo "Verified SPA shells in STATIC_ROOT: ${required[*]}"
'

  echo "Frontend deploy complete. nginx serves updated /static/ and /machine/ assets."
  echo "Pipeline was not restarted. Hard-refresh the browser to load new bundle hashes."
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
