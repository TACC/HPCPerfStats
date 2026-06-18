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
FRONTEND_DIR="${REPO_ROOT}/hpcperfstats/site/frontend"
STATIC_FRONTEND="${REPO_ROOT}/hpcperfstats/site/hpcperfstats_site/static/frontend"
CONTAINER_STATIC_FRONTEND="/home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend"
CONTAINER_STATIC_ROOT="${CONTAINER_STATIC_ROOT:-/home/hpcperfstats/staticfiles}"
CONTAINER_STATIC_ROOT_FRONTEND="${CONTAINER_STATIC_ROOT}/frontend"
PROXY_STATIC_ROOT_FRONTEND="/srv/static/frontend"
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

verify_spa_shells_via_compose() {
  local container_dir="$1"
  local label="$2"
  local rel missing=()
  for rel in "${REQUIRED_SPA_SHELLS[@]}"; do
    if ! docker compose exec -T web bash -lc "[[ -f '${container_dir}/${rel}' ]]"; then
      missing+=("${container_dir}/${rel}")
    fi
  done
  if ((${#missing[@]} > 0)); then
    echo "rebuild_frontend.sh: required SPA shell(s) missing under ${label}:" >&2
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
  print_deploy_fingerprint "host build" "${STATIC_FRONTEND}/machine/index.html"
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

compose_backend_is_podman() {
  if command -v podman-compose >/dev/null 2>&1; then
    return 0
  fi
  if docker compose version 2>/dev/null | grep -qi podman; then
    return 0
  fi
  return 1
}

compose_cp_supported() {
  if compose_backend_is_podman; then
    return 1
  fi
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

frontend_build_fingerprint() {
  local index_html="$1"
  if [[ ! -f "${index_html}" ]]; then
    echo "unknown"
    return
  fi
  local fingerprint
  fingerprint="$(grep -oE 'page-[0-9a-f]+\.js' "${index_html}" | head -n 1 || true)"
  if [[ -n "${fingerprint}" ]]; then
    echo "${fingerprint}"
    return
  fi
  grep -oE '<!--[^>]{8,}-->' "${index_html}" | head -n 1 | tr -d '<!->' || echo "unknown"
}

print_deploy_fingerprint() {
  local label="$1"
  local index_html="$2"
  local fingerprint
  fingerprint="$(frontend_build_fingerprint "${index_html}")"
  echo "Deploy fingerprint (${label}): ${fingerprint}"
}

print_container_deploy_fingerprint() {
  local label="$1"
  local service="$2"
  local container_path="$3"
  local fingerprint
  fingerprint="$(
    docker compose exec -T "${service}" sh -lc \
      "grep -oE 'page-[0-9a-f]+\\.js' '${container_path}' 2>/dev/null | head -n 1 || echo unknown"
  )"
  echo "Deploy fingerprint (${label}): ${fingerprint}"
}

count_files_under() {
  local root="$1"
  find "${root}" -type f 2>/dev/null | wc -l | tr -d ' '
}

count_files_in_web_container() {
  local container_dir="$1"
  docker compose exec -T web bash -lc "find '${container_dir}' -type f 2>/dev/null | wc -l | tr -d ' '"
}

count_files_in_proxy_container() {
  local container_dir="$1"
  docker compose exec -T proxy sh -lc "find '${container_dir}' -type f 2>/dev/null | wc -l | tr -d ' '"
}

reset_container_dir_via_compose() {
  local target_dir="$1"
  docker compose exec -T web bash -lc "rm -rf '${target_dir}' && mkdir -p '${target_dir}'"
}

copy_host_tar_into_web() {
  local host_tar="$1"
  local container_tar="$2"
  local cid="$3"

  cd "${REPO_ROOT}"
  if docker compose cp "${host_tar}" "web:${container_tar}" 2>/dev/null; then
    echo "rebuild_frontend.sh: staged deploy tar → web via compose cp" >&2
    return 0
  fi

  if [[ -n "${cid}" ]] && podman_cli_available; then
    podman cp "${host_tar}" "${cid}:${container_tar}"
    echo "rebuild_frontend.sh: staged deploy tar → web via podman cp" >&2
    return 0
  fi

  echo "rebuild_frontend.sh: failed to copy deploy tar into web container" >&2
  return 1
}

copy_tree_via_staged_tar() {
  local dest="$1"
  local cid host_tar container_tar

  cid="$(web_container_id)"
  if [[ -z "${cid}" ]]; then
    echo "rebuild_frontend.sh: web container id not found (is the web service running?)" >&2
    exit 1
  fi

  host_tar="$(mktemp /tmp/hps-frontend-deploy.XXXXXX.tar)"
  container_tar="/tmp/hps-frontend-deploy.${$}.${RANDOM}.tar"

  tar -C "${STATIC_FRONTEND}" -cf "${host_tar}" .
  if ! copy_host_tar_into_web "${host_tar}" "${container_tar}" "${cid}"; then
    rm -f "${host_tar}"
    exit 1
  fi
  rm -f "${host_tar}"

  # Extract inside web via compose exec so writes land on the staticfiles_data volume.
  docker compose exec -T web bash -lc \
    "rm -rf '${dest}' && mkdir -p '${dest}' && tar -xf '${container_tar}' -C '${dest}' && rm -f '${container_tar}'"
}

copy_tree_via_compose_cp() {
  local dest="$1"
  reset_container_dir_via_compose "${dest}"
  docker compose cp "${STATIC_FRONTEND}/." "web:${dest}/"
}

copy_tree_into_container() {
  local dest="$1"

  if compose_cp_supported; then
    echo "rebuild_frontend.sh: copying via docker compose cp → ${dest}" >&2
    copy_tree_via_compose_cp "${dest}"
    return
  fi

  if ! podman_cli_available; then
    echo "rebuild_frontend.sh: podman-compose detected but podman not on PATH; cannot deploy into web" >&2
    exit 1
  fi

  # podman-compose: stdin tar and direct tree cp are unreliable for named volumes; stage a
  # tar on /tmp in the container, then extract with compose exec onto the volume mount.
  echo "rebuild_frontend.sh: copying via staged tar + compose exec extract → ${dest}" >&2
  copy_tree_via_staged_tar "${dest}"
}

copy_frontend_into_web() {
  cd "${REPO_ROOT}"

  # nginx (proxy) reads STATIC_ROOT/frontend from the shared staticfiles_data volume.
  copy_tree_into_container "${CONTAINER_STATIC_ROOT_FRONTEND}"

  # Keep image source tree in sync for future collectstatic / container rebuilds.
  copy_tree_into_container "${CONTAINER_STATIC_FRONTEND}"
}

sha256_in_web_container() {
  local container_path="$1"
  docker compose exec -T web bash -lc \
    "if [[ ! -f '${container_path}' ]]; then exit 2; fi; sha256sum '${container_path}' | awk '{print \$1}'"
}

sha256_in_proxy_container() {
  local container_path="$1"
  docker compose exec -T proxy sh -lc \
    "if [[ ! -f '${container_path}' ]]; then exit 2; fi; sha256sum '${container_path}' | awk '{print \$1}'"
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

verify_proxy_frontend_matches_web() {
  local web_probe="${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html"
  local proxy_probe="${PROXY_STATIC_ROOT_FRONTEND}/machine/index.html"
  local web_sha proxy_sha

  web_sha="$(sha256_in_web_container "${web_probe}")"
  proxy_sha="$(sha256_in_proxy_container "${proxy_probe}")"

  if [[ -z "${proxy_sha}" ]]; then
    echo "rebuild_frontend.sh: proxy nginx volume missing ${proxy_probe}" >&2
    return 1
  fi

  if [[ "${web_sha}" != "${proxy_sha}" ]]; then
    echo "rebuild_frontend.sh: proxy nginx volume does not match web STATIC_ROOT" >&2
    echo "  web:   ${web_sha}  (${web_probe})" >&2
    echo "  proxy: ${proxy_sha}  (${proxy_probe})" >&2
    return 1
  fi

  echo "Verified proxy nginx volume matches web (${proxy_probe})"
}

verify_proxy_file_count_matches_web() {
  local web_count proxy_count

  web_count="$(count_files_in_web_container "${CONTAINER_STATIC_ROOT_FRONTEND}")"
  proxy_count="$(count_files_in_proxy_container "${PROXY_STATIC_ROOT_FRONTEND}")"

  if [[ "${web_count}" != "${proxy_count}" ]]; then
    echo "rebuild_frontend.sh: proxy file count mismatch (web ${web_count}, proxy ${proxy_count})" >&2
    return 1
  fi

  echo "Verified proxy nginx volume file count matches web (${web_count} files)"
}

verify_container_frontend_matches_host() {
  verify_container_path_matches_host "${CONTAINER_STATIC_ROOT_FRONTEND}" "STATIC_ROOT/frontend (nginx volume)"
}

deploy_to_compose() {
  cd "${REPO_ROOT}"
  if ! web_service_running; then
    echo "web service is not running; skipping deploy (host tree is updated)." >&2
    echo "Start web later and re-run without --no-deploy, or run collectstatic manually." >&2
    return 0
  fi

  echo "Copying built assets into web:${CONTAINER_STATIC_ROOT_FRONTEND} (nginx staticfiles volume) ..."
  copy_frontend_into_web
  verify_container_frontend_matches_host
  verify_container_file_count_matches_host "${CONTAINER_STATIC_ROOT_FRONTEND}" "STATIC_ROOT/frontend (nginx volume)"
  verify_spa_shells_via_compose "${CONTAINER_STATIC_ROOT_FRONTEND}" "STATIC_ROOT/frontend"
  verify_proxy_frontend_matches_web
  verify_proxy_file_count_matches_web
  print_container_deploy_fingerprint "web STATIC_ROOT" web "${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html"
  print_container_deploy_fingerprint "proxy nginx volume" proxy "${PROXY_STATIC_ROOT_FRONTEND}/machine/index.html"
  print_deploy_fingerprint "host build (expected)" "${STATIC_FRONTEND}/machine/index.html"

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
