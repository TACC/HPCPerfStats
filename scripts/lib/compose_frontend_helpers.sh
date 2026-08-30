#!/usr/bin/env bash
# Shared compose/podman helpers for frontend volume deploy and pipeline rebuild scripts.
# Source from scripts/rebuild_frontend.sh or scripts/rebuild_pipeline.sh (not executed directly).

: "${REPO_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
: "${CONTAINER_STATIC_ROOT:=/home/hpcperfstats/staticfiles}"
: "${CONTAINER_STATIC_ROOT_FRONTEND:=${CONTAINER_STATIC_ROOT}/frontend}"
: "${CONTAINER_STATIC_FRONTEND:=/home/hpcperfstats/hpcperfstats/site/hpcperfstats_site/static/frontend}"
: "${PROXY_STATIC_ROOT_FRONTEND:=/srv/static/frontend}"

# Next static export shells nginx serves (see services-conf/nginx-static-files.conf).
REQUIRED_SPA_SHELLS=(
  "machine/index.html"
  "pub/index.html"
)

compose_backend_is_podman() {
  if command -v podman-compose >/dev/null 2>&1; then
    return 0
  fi
  if docker compose version 2>/dev/null | grep -qi podman; then
    return 0
  fi
  return 1
}

compose_web_image_name() {
  cd "${REPO_ROOT}"
  local name
  name="$(docker compose config --images web 2>/dev/null | head -n 1 | tr -d '[:space:]')"
  if [[ -n "${name}" ]]; then
    echo "${name}"
    return
  fi
  echo "hpcperfstats"
}

# Full commit SHA for SPA bake override (context may include .git for in-image
# rev-parse). Prefer env when set, else host git for --build-arg / rebuild_frontend.
resolve_hpcperfstats_git_commit() {
  local from_env="${HPCPERFSTATS_GIT_COMMIT:-}"
  if [[ -n "${from_env}" && "${from_env}" != "unknown" ]]; then
    printf '%s\n' "${from_env}"
    return 0
  fi
  if command -v git >/dev/null 2>&1 && git -C "${REPO_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
    git -C "${REPO_ROOT}" rev-parse HEAD
    return 0
  fi
  printf '%s\n' "unknown"
}

# podman-compose does not forward `compose build --target`; use podman/docker build directly.
build_web_image_with_target() {
  local target="$1"
  local image_name dockerfile git_commit

  cd "${REPO_ROOT}"
  image_name="$(compose_web_image_name)"
  dockerfile="${REPO_ROOT}/Dockerfile"
  if [[ ! -f "${dockerfile}" ]]; then
    echo "build_web_image_with_target: Dockerfile not found at ${dockerfile}" >&2
    return 1
  fi

  git_commit="$(resolve_hpcperfstats_git_commit)"
  export HPCPERFSTATS_GIT_COMMIT="${git_commit}"

  if compose_backend_is_podman; then
    local build_cli=(podman build)
    if ! podman_cli_available; then
      if command -v docker >/dev/null 2>&1; then
        build_cli=(docker build)
      else
        echo "build_web_image_with_target: podman-compose detected but neither podman nor docker on PATH" >&2
        return 1
      fi
    fi
    echo "Building ${image_name} target=${target} via ${build_cli[*]} (podman-compose lacks compose build --target) ..."
    "${build_cli[@]}" \
      --target "${target}" \
      --build-arg "HPCPERFSTATS_GIT_COMMIT=${git_commit}" \
      -f "${dockerfile}" \
      -t "${image_name}" \
      "${REPO_ROOT}"
    return 0
  fi

  docker compose build web --target "${target}" \
    --build-arg "HPCPERFSTATS_GIT_COMMIT=${git_commit}"
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

web_service_running() {
  cd "${REPO_ROOT}"
  docker compose exec -T web true >/dev/null 2>&1
}

web_container_ref() {
  cd "${REPO_ROOT}"
  local id name
  id="$(docker compose ps -q web 2>/dev/null | head -n 1 | tr -d '[:space:]')"
  if [[ -n "${id}" ]]; then
    echo "${id}"
    return
  fi
  name="$(docker compose ps --format '{{.Name}}' web 2>/dev/null | head -n 1 | tr -d '[:space:]')"
  if [[ -n "${name}" ]]; then
    echo "${name}"
    return
  fi
  echo "hpcperfstats_web_1"
}

web_container_id() {
  web_container_ref
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

fingerprint_in_container() {
  local service="$1"
  local container_path="$2"
  docker compose exec -T "${service}" sh -lc \
    "grep -oE 'page-[0-9a-f]+\\.js' '${container_path}' 2>/dev/null | head -n 1 || echo unknown"
}

print_container_deploy_fingerprint() {
  local label="$1"
  local service="$2"
  local container_path="$3"
  local fingerprint
  fingerprint="$(fingerprint_in_container "${service}" "${container_path}")"
  echo "Deploy fingerprint (${label}): ${fingerprint}"
}

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
    echo "required SPA shell(s) missing under ${label}:" >&2
    for path in "${missing[@]}"; do
      echo "  missing: ${path}" >&2
    done
    return 1
  fi

  echo "Verified SPA shells under ${label}: ${REQUIRED_SPA_SHELLS[*]}"
}

# Fail closed if the built SPA still has the pre-2026-07 job-list date allowlist
# (include_filter_options/host/jid only — drops calendar end_time__date → full job table).
verify_job_list_date_filter_in_spa_build() {
  local base_dir="$1"
  local label="${2:-${base_dir}}"
  local chunks_dir="${base_dir}/_next/static/chunks"
  local hit=""

  if [[ ! -d "${chunks_dir}" ]]; then
    echo "verify_job_list_date_filter_in_spa_build: no chunks under ${label} (${chunks_dir})" >&2
    return 1
  fi

  # Fixed allowlist embeds these keys adjacent in the minified Set initializer.
  hit="$(
    grep -R -l -F 'include_filter_options","host","jid","end_time__date' "${chunks_dir}" 2>/dev/null | head -n 1 || true
  )"
  if [[ -z "${hit}" ]]; then
    echo "verify_job_list_date_filter_in_spa_build: ${label} is missing end_time__date job-list allowlist." >&2
    echo "  Stale SPA serves unfiltered /api/jobs/ for /machine/date/… (hundreds of thousands of rows)." >&2
    echo "  Rebuild with scripts/rebuild_frontend.sh — rebuild_pipeline.sh does NOT refresh the SPA." >&2
    return 1
  fi
  echo "Verified job-list date filter allowlist in SPA build (${label}): $(basename "${hit}")"
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
    echo "required SPA shell(s) missing under ${label}:" >&2
    for path in "${missing[@]}"; do
      echo "  missing: ${path}" >&2
    done
    return 1
  fi
  echo "Verified SPA shells under ${label}: ${REQUIRED_SPA_SHELLS[*]}"
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
  local container_ref="$3"

  cd "${REPO_ROOT}"
  if compose_backend_is_podman; then
    if ! podman_cli_available; then
      echo "podman-compose detected but podman not on PATH" >&2
      return 1
    fi
    podman cp "${host_tar}" "${container_ref}:${container_tar}"
    echo "staged deploy tar → web via podman cp (${container_ref})"
    return 0
  fi

  if docker compose cp "${host_tar}" "web:${container_tar}"; then
    echo "staged deploy tar → web via compose cp"
    return 0
  fi

  echo "failed to copy deploy tar into web container" >&2
  return 1
}

verify_staged_tar_in_web() {
  local container_tar="$1"
  docker compose exec -T web bash -lc "test -s '${container_tar}'"
}

copy_tree_via_staged_tar_from_dir() {
  local host_src_dir="$1"
  local dest="$2"
  local expected_index_html="${3:-}"
  local container_ref host_tar container_tar

  container_ref="$(web_container_ref)"
  host_tar="$(mktemp /tmp/hps-frontend-deploy.XXXXXX.tar)"
  container_tar="/tmp/hps-frontend-deploy.${$}.${RANDOM}.tar"

  echo "creating deploy tar from ${host_src_dir} ..."
  tar -C "${host_src_dir}" -cf "${host_tar}" .
  if ! copy_host_tar_into_web "${host_tar}" "${container_tar}" "${container_ref}"; then
    rm -f "${host_tar}"
    return 1
  fi
  rm -f "${host_tar}"

  echo "verifying staged tar in web container ..."
  if ! verify_staged_tar_in_web "${container_tar}"; then
    echo "staged tar missing or empty in web: ${container_tar}" >&2
    return 1
  fi

  echo "extracting staged tar into web:${dest} ..."
  docker compose exec -T web bash -lc \
    "rm -rf '${dest}' && mkdir -p '${dest}' && tar -xf '${container_tar}' -C '${dest}' && rm -f '${container_tar}'"

  if [[ -n "${expected_index_html}" && -f "${expected_index_html}" ]]; then
    verify_container_extract_fingerprint "${dest}" "${dest}" "${expected_index_html}"
  fi
}

verify_container_extract_fingerprint() {
  local dest="$1"
  local label="$2"
  local expected_index_html="$3"
  local host_fp container_fp probe="${dest}/machine/index.html"

  host_fp="$(frontend_build_fingerprint "${expected_index_html}")"
  container_fp="$(
    docker compose exec -T web bash -lc \
      "grep -oE 'page-[0-9a-f]+\\.js' '${probe}' 2>/dev/null | head -n 1 || echo unknown"
  )"

  if [[ "${host_fp}" != "${container_fp}" ]]; then
    echo "${label} fingerprint mismatch after extract" >&2
    echo "  expected: ${host_fp}  (${expected_index_html})" >&2
    echo "  container: ${container_fp}  (${probe})" >&2
    return 1
  fi

  echo "Verified extract fingerprint for ${label} (${container_fp})"
}

copy_tree_via_compose_cp_from_dir() {
  local host_src_dir="$1"
  local dest="$2"
  reset_container_dir_via_compose "${dest}"
  docker compose cp "${host_src_dir}/." "web:${dest}/"
}

copy_tree_into_container_from_dir() {
  local host_src_dir="$1"
  local dest="$2"
  local expected_index_html="${3:-}"

  if compose_cp_supported; then
    echo "copying via docker compose cp → ${dest}" >&2
    copy_tree_via_compose_cp_from_dir "${host_src_dir}" "${dest}"
    return
  fi

  if ! podman_cli_available; then
    echo "podman-compose detected but podman not on PATH; cannot deploy into web" >&2
    return 1
  fi

  echo "copying via staged tar + compose exec extract → ${dest}"
  copy_tree_via_staged_tar_from_dir "${host_src_dir}" "${dest}" "${expected_index_html}"
}

extract_container_dir_to_host() {
  local container_src="$1"
  local host_dest="$2"
  local container_ref

  mkdir -p "${host_dest}"
  rm -rf "${host_dest:?}/"*

  cd "${REPO_ROOT}"
  container_ref="$(web_container_ref)"

  if compose_cp_supported; then
    docker compose cp "web:${container_src}/." "${host_dest}/"
    return 0
  fi

  if ! podman_cli_available; then
    echo "cannot extract from web container: podman not on PATH" >&2
    return 1
  fi

  local host_tar container_tar
  host_tar="$(mktemp /tmp/hps-frontend-export.XXXXXX.tar)"
  container_tar="/tmp/hps-frontend-export.${$}.${RANDOM}.tar"

  docker compose exec -T web bash -lc \
    "tar -C '${container_src}' -cf '${container_tar}' ."
  podman cp "${container_ref}:${container_tar}" "${host_tar}"
  tar -xf "${host_tar}" -C "${host_dest}"
  docker compose exec -T web bash -lc "rm -f '${container_tar}'" || true
  rm -f "${host_tar}"
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

verify_proxy_frontend_matches_web() {
  local web_probe="${CONTAINER_STATIC_ROOT_FRONTEND}/machine/index.html"
  local proxy_probe="${PROXY_STATIC_ROOT_FRONTEND}/machine/index.html"
  local web_sha proxy_sha

  web_sha="$(sha256_in_web_container "${web_probe}")"
  proxy_sha="$(sha256_in_proxy_container "${proxy_probe}")"

  if [[ -z "${proxy_sha}" ]]; then
    echo "proxy nginx volume missing ${proxy_probe}" >&2
    return 1
  fi

  if [[ "${web_sha}" != "${proxy_sha}" ]]; then
    echo "proxy nginx volume does not match web STATIC_ROOT" >&2
    echo "  web:   ${web_sha}  (${web_probe})" >&2
    echo " proxy: ${proxy_sha}  (${proxy_probe})" >&2
    return 1
  fi

  echo "Verified proxy nginx volume matches web (${proxy_probe})"
}

verify_proxy_file_count_matches_web() {
  local web_count proxy_count

  web_count="$(count_files_in_web_container "${CONTAINER_STATIC_ROOT_FRONTEND}")"
  proxy_count="$(count_files_in_proxy_container "${PROXY_STATIC_ROOT_FRONTEND}")"

  if [[ "${web_count}" != "${proxy_count}" ]]; then
    echo "proxy file count mismatch (web ${web_count}, proxy ${proxy_count})" >&2
    return 1
  fi

  echo "Verified proxy nginx volume file count matches web (${web_count} files)"
}

print_podman_deploy_fallback() {
  local static_root_frontend="${1:-${CONTAINER_STATIC_ROOT_FRONTEND}}"
  cat <<EOF >&2
manual podman fallback (from git checkout):
  tar -cf /tmp/hps-frontend.tar -C hpcperfstats/site/hpcperfstats_site/static/frontend .
  podman cp /tmp/hps-frontend.tar hpcperfstats_web_1:/tmp/hps-frontend.tar
  docker compose exec web bash -lc 'rm -rf ${static_root_frontend} && mkdir -p ${static_root_frontend} && tar -xf /tmp/hps-frontend.tar -C ${static_root_frontend} && rm -f /tmp/hps-frontend.tar'
EOF
}

wait_for_web_http() {
  local url="${1:-http://web:8000/}"
  local timeout_s="${2:-600}"
  local sleep_s="${3:-5}"
  local waited=0

  echo "Waiting for web at ${url} (timeout ${timeout_s}s) ..."
  while (( waited < timeout_s )); do
    if docker compose exec -T web sh -lc \
      "curl -s -o /dev/null -w '%{http_code}' '${url}'" 2>/dev/null | grep -qE '^[23]'; then
      echo "web responded at ${url}"
      return 0
    fi
    sleep "${sleep_s}"
    waited=$((waited + sleep_s))
  done
  echo "timed out waiting for web at ${url}" >&2
  return 1
}

# Set to 1 when compose_recreate_web_after_image_refresh stopped a running proxy (podman path).
COMPOSE_PROXY_WAS_RUNNING="${COMPOSE_PROXY_WAS_RUNNING:-0}"

compose_service_running() {
  local service="$1"
  cd "${REPO_ROOT}"
  docker compose ps --status running --services "${service}" 2>/dev/null | grep -qx "${service}"
}

# podman-compose has no ``rm`` subcommand (Docker Compose v2 only). Remove stale
# project containers by name/id via podman/docker CLI so ``up -d`` can reuse
# fixed names like ``hpcperfstats_web_1``.
compose_podman_rm_service_containers() {
  local service cid name
  local -a rm_cli
  cd "${REPO_ROOT}"
  if podman_cli_available; then
    rm_cli=(podman rm -f)
  else
    rm_cli=(docker rm -f)
  fi
  for service in "$@"; do
    cid="$(docker compose ps -q "${service}" 2>/dev/null | head -n 1 | tr -d '[:space:]')"
    if [[ -n "${cid}" ]]; then
      "${rm_cli[@]}" "${cid}" 2>/dev/null || true
      continue
    fi
    name="$(docker compose ps --format '{{.Name}}' "${service}" 2>/dev/null | head -n 1 | tr -d '[:space:]')"
    if [[ -z "${name}" ]]; then
      name="hpcperfstats_${service}_1"
    fi
    "${rm_cli[@]}" "${name}" 2>/dev/null || true
  done
}

compose_recreate_web_after_image_refresh() {
  cd "${REPO_ROOT}"
  if [[ "${HPCPERFSTATS_SCRIPT_DRY_RUN:-0}" -eq 1 ]]; then
    if compose_backend_is_podman; then
      echo "[dry-run] podman: stop proxy; podman rm -f pipeline+web containers; compose up -d web"
    else
      echo "[dry-run] docker compose up -d --force-recreate --no-deps web"
    fi
    return 0
  fi

  if compose_backend_is_podman; then
    COMPOSE_PROXY_WAS_RUNNING=0
    if compose_service_running proxy; then
      COMPOSE_PROXY_WAS_RUNNING=1
      echo "Stopping proxy (podman: release web container dependency) ..."
      docker compose stop proxy || true
    fi
    echo "Removing stopped pipeline and web containers (podman) ..."
    compose_podman_rm_service_containers pipeline web
    echo "Starting web with refreshed image ..."
    docker compose up -d web
    return 0
  fi

  echo "Recreating web with refreshed image ..."
  docker compose up -d --force-recreate --no-deps web
}

compose_recreate_pipeline_after_image_refresh() {
  cd "${REPO_ROOT}"
  if [[ "${HPCPERFSTATS_SCRIPT_DRY_RUN:-0}" -eq 1 ]]; then
    if compose_backend_is_podman; then
      echo "[dry-run] podman: podman rm -f pipeline container; compose up -d pipeline"
    else
      echo "[dry-run] docker compose up -d --force-recreate --no-deps pipeline"
    fi
    return 0
  fi

  if compose_backend_is_podman; then
    compose_podman_rm_service_containers pipeline
    docker compose up -d pipeline
    return 0
  fi

  echo "Recreating pipeline with refreshed image ..."
  docker compose up -d --force-recreate --no-deps pipeline
}

# Remove host scratch created for hpcperfstats-pipeline-refresh (preserve dir,
# backup tar, restore dir). rmdir .build only when empty so monitor/other
# sibling trees under .build/ are left intact.
cleanup_pipeline_rebuild_scratch() {
  local preserve_dir="${1:-}"
  local backup_tar="${2:-}"
  local restore_dir="${3:-}"
  local build_root=""
  local restore_base=""

  if [[ -n "${backup_tar}" && -f "${backup_tar}" ]]; then
    rm -f "${backup_tar}"
  fi

  if [[ -n "${restore_dir}" && -d "${restore_dir}" ]]; then
    restore_base="$(basename "${restore_dir}")"
    if [[ "${restore_base}" == hps-pipeline-frontend-restore.* ]]; then
      rm -rf "${restore_dir}"
    fi
  fi

  if [[ -z "${preserve_dir}" ]]; then
    return 0
  fi

  case "${preserve_dir}" in
    */.build/pipeline-rebuild-frontend)
      if [[ -e "${preserve_dir}" ]]; then
        echo "Removing pipeline rebuild scratch ${preserve_dir}"
        rm -rf "${preserve_dir}"
      fi
      build_root="$(dirname "${preserve_dir}")"
      if [[ "$(basename "${build_root}")" == ".build" ]]; then
        rmdir "${build_root}" 2>/dev/null || true
      fi
      ;;
    *)
      echo "cleanup_pipeline_rebuild_scratch: refusing unexpected preserve dir: ${preserve_dir}" >&2
      ;;
  esac
}

compose_restore_proxy_if_was_running() {
  if [[ "${COMPOSE_PROXY_WAS_RUNNING:-0}" -ne 1 ]]; then
    return 0
  fi
  cd "${REPO_ROOT}"
  if [[ "${HPCPERFSTATS_SCRIPT_DRY_RUN:-0}" -eq 1 ]]; then
    echo "[dry-run] would start proxy (was running before web recreate)"
    return 0
  fi
  echo "Starting proxy ..."
  docker compose up -d proxy
}
