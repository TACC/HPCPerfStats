#!/usr/bin/env bash
# Shared podman-compose invocation for test workflows (source, do not execute).
# Usage: . "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
#        compose_test up -d db redis

_compose_test_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${HPCPERFSTATS_COMPOSE_DEVELOPMENT:-0}" == "1" ]]; then
  HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-dev
else
  HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-test
fi
export HPCPERFSTATS_COMPOSE_PROJECT
# shellcheck source=../scripts/lib/podman_runtime.sh
. "${_compose_test_repo_root}/scripts/lib/podman_runtime.sh"
# shellcheck source=podman_compose_teardown.sh
. "${_compose_test_repo_root}/tests/podman_compose_teardown.sh"

COMPOSE_TEST=(
  "${PODMAN_COMPOSE[@]}"
  -f docker-compose.yaml
  -f tests/docker-compose.test-overlay.yaml
)
COMPOSE_BIND_MOUNT_DIR=""
_COMPOSE_BIND_MOUNT_WORK_COPY=""

compose_repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

compose_ensure_settings_yaml() {
  local repo_root
  repo_root="$(compose_repo_root)"
  local settings="${repo_root}/docker-compose.settings.yaml"
  local example="${repo_root}/docker-compose.settings.yaml.example"
  if [[ ! -f "$settings" ]]; then
    if [[ ! -f "$example" ]]; then
      echo "compose_ensure_settings_yaml: missing ${example}" >&2
      return 1
    fi
    cp "$example" "$settings"
    echo "compose_ensure_settings_yaml: created ${settings} from example" >&2
  fi
}

compose_ensure_test_overlay_yaml() {
  local repo_root
  repo_root="$(compose_repo_root)"
  local overlay="${repo_root}/tests/docker-compose.test-overlay.yaml"
  local example="${repo_root}/tests/docker-compose.test-overlay.yaml.example"
  if [[ ! -f "$overlay" ]]; then
    if [[ ! -f "$example" ]]; then
      echo "compose_ensure_test_overlay_yaml: missing ${example}" >&2
      return 1
    fi
    cp "$example" "$overlay"
    echo "compose_ensure_test_overlay_yaml: created ${overlay} from example" >&2
  fi
}

# Keep optional bind-mount work copies on the shared /data storage contract.
compose_work_copy_base_dir() {
  if [[ -n "${COMPOSE_BIND_MOUNT_BASE_DIR:-}" ]]; then
    echo "${COMPOSE_BIND_MOUNT_BASE_DIR}"
    return
  fi
  echo "${HPCPERFSTATS_HOST_CACHE}/hpcperfstats-compose"
}

compose_ensure_work_copy_ini() {
  local repo_root="$1"
  local work_dir="$2"
  if [[ -f "${repo_root}/hpcperfstats.ini" ]]; then
    cp -f "${repo_root}/hpcperfstats.ini" "${work_dir}/hpcperfstats.ini"
  elif [[ ! -f "${work_dir}/hpcperfstats.ini" ]]; then
    cp "${repo_root}/hpcperfstats.ini.example" "${work_dir}/hpcperfstats.ini"
  fi
}

compose_rsync_repo_to_work_copy() {
  local repo_root="$1"
  local dest="$2"
  local minimal="${3:-0}"
  local excludes=(
    --exclude '.venv/'
    --exclude '.git/'
    --exclude 'docs/'
    --exclude 'artifacts/'
    --exclude 'test_runs/'
    --exclude '**/node_modules/'
    --exclude '**/__pycache__/'
    --exclude '.pytest_cache/'
    --exclude 'staticfiles/'
    --exclude 'hpcperfstats/site/hpcperfstats_site/static/frontend/'
  )
  if [[ "$minimal" == "1" ]]; then
    mkdir -p "${dest}/hpcperfstats" "${dest}/tests"
    rsync -a --timeout=120 "${excludes[@]}" \
      "${repo_root}/pyproject.toml" \
      "${repo_root}/conftest.py" \
      "${repo_root}/docker-compose.yaml" \
      "${repo_root}/docker-compose.settings.yaml.example" \
      "${repo_root}/Dockerfile" \
      "${dest}/"
    rsync -a --timeout=120 "${excludes[@]}" \
      "${repo_root}/hpcperfstats.ini.example" \
      "${dest}/"
    if [[ -f "${repo_root}/hpcperfstats.ini" ]]; then
      cp -f "${repo_root}/hpcperfstats.ini" "${dest}/hpcperfstats.ini"
    fi
    rsync -a --delete --timeout=120 "${excludes[@]}" \
      "${repo_root}/hpcperfstats/" "${dest}/hpcperfstats/"
    rsync -a --delete --timeout=120 "${excludes[@]}" \
      "${repo_root}/tests/" "${dest}/tests/"
    rsync -a --delete --timeout=120 "${excludes[@]}" \
      "${repo_root}/scripts/" "${dest}/scripts/"
    rsync -a --timeout=120 "${excludes[@]}" \
      "${repo_root}/services-conf/" "${dest}/services-conf/"
    compose_rsync_docs_contract_files "$repo_root" "$dest"
    return 0
  fi
  rsync -a --delete --timeout=120 "${excludes[@]}" \
    "${repo_root}/" "${dest}/"
  compose_rsync_docs_contract_files "$repo_root" "$dest"
}

compose_rsync_docs_contract_files() {
  local repo_root="$1"
  local dest="$2"
  if [[ -f "${repo_root}/docs/monitor_variable_rename_map.yaml" ]]; then
    mkdir -p "${dest}/docs"
    rsync -a --timeout=120 \
      "${repo_root}/docs/monitor_variable_rename_map.yaml" \
      "${dest}/docs/"
  fi
}

compose_prepare_bind_mount() {
  compose_ensure_settings_yaml || return 1
  compose_ensure_test_overlay_yaml || return 1
  local repo_root
  repo_root="$(compose_repo_root)"
  local use_work_copy="${COMPOSE_BIND_MOUNT_WORK_COPY:-}"
  local minimal_copy="${COMPOSE_BIND_MOUNT_MINIMAL:-0}"
  if [[ "${COMPOSE_BIND_MOUNT_SKIP_BUILD:-0}" == "1" ]]; then
    minimal_copy=1
  fi
  if [[ -z "$use_work_copy" ]]; then
    case "$repo_root" in
      *CloudStorage*|*iCloud*)
        use_work_copy=1
        ;;
    esac
  fi
  if [[ "$use_work_copy" != "1" && "${COMPOSE_BIND_MOUNT_FORCE_WORK_COPY:-0}" == "1" ]]; then
    use_work_copy=1
  fi
  if [[ "$use_work_copy" == "1" ]]; then
    local work_base attempt
    work_base="$(compose_work_copy_base_dir)"
    mkdir -p "$work_base"
    _COMPOSE_BIND_MOUNT_WORK_COPY="${COMPOSE_BIND_MOUNT_WORKDIR:-${work_base}/stable}"
    mkdir -p "${_COMPOSE_BIND_MOUNT_WORK_COPY}"
    echo "Compose bind mount: rsync repo to ${_COMPOSE_BIND_MOUNT_WORK_COPY} (virtiofs-safe; minimal=${minimal_copy})" >&2
    for attempt in 1 2 3; do
      if ! compose_rsync_repo_to_work_copy "$repo_root" "${_COMPOSE_BIND_MOUNT_WORK_COPY}" "$minimal_copy"; then
        echo "compose_prepare_bind_mount: rsync command failed (attempt ${attempt}/3)" >&2
      fi
      if [[ -f "${_COMPOSE_BIND_MOUNT_WORK_COPY}/pyproject.toml" ]]; then
        compose_ensure_work_copy_ini "$repo_root" "${_COMPOSE_BIND_MOUNT_WORK_COPY}"
        if [[ ! -f "${_COMPOSE_BIND_MOUNT_WORK_COPY}/docker-compose.settings.yaml" ]]; then
          if [[ -f "${_COMPOSE_BIND_MOUNT_WORK_COPY}/docker-compose.settings.yaml.example" ]]; then
            cp "${_COMPOSE_BIND_MOUNT_WORK_COPY}/docker-compose.settings.yaml.example" \
              "${_COMPOSE_BIND_MOUNT_WORK_COPY}/docker-compose.settings.yaml"
          elif [[ -f "${repo_root}/docker-compose.settings.yaml" ]]; then
            cp -f "${repo_root}/docker-compose.settings.yaml" \
              "${_COMPOSE_BIND_MOUNT_WORK_COPY}/docker-compose.settings.yaml"
          fi
        fi
        if [[ ! -f "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests/docker-compose.test-overlay.yaml" ]]; then
          mkdir -p "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests"
          if [[ -f "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests/docker-compose.test-overlay.yaml.example" ]]; then
            cp "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests/docker-compose.test-overlay.yaml.example" \
              "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests/docker-compose.test-overlay.yaml"
          elif [[ -f "${repo_root}/tests/docker-compose.test-overlay.yaml" ]]; then
            cp -f "${repo_root}/tests/docker-compose.test-overlay.yaml" \
              "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests/docker-compose.test-overlay.yaml"
          elif [[ -f "${repo_root}/tests/docker-compose.test-overlay.yaml.example" ]]; then
            cp -f "${repo_root}/tests/docker-compose.test-overlay.yaml.example" \
              "${_COMPOSE_BIND_MOUNT_WORK_COPY}/tests/docker-compose.test-overlay.yaml"
          fi
        fi
        break
      fi
      echo "compose_prepare_bind_mount: rsync incomplete (attempt ${attempt}/3), retrying..." >&2
      sleep 2
    done
    if [[ ! -f "${_COMPOSE_BIND_MOUNT_WORK_COPY}/pyproject.toml" ]]; then
      echo "compose_prepare_bind_mount: work copy missing pyproject.toml after rsync" >&2
      return 1
    fi
    COMPOSE_BIND_MOUNT_DIR="${_COMPOSE_BIND_MOUNT_WORK_COPY}"
  else
    COMPOSE_BIND_MOUNT_DIR="${repo_root}"
  fi
  if [[ ! -f "${COMPOSE_BIND_MOUNT_DIR}/hpcperfstats.ini" ]]; then
    compose_ensure_work_copy_ini "$repo_root" "${COMPOSE_BIND_MOUNT_DIR}"
  fi
}

compose_cleanup_bind_mount() {
  if [[ -n "${_COMPOSE_BIND_MOUNT_WORK_COPY}" && -d "${_COMPOSE_BIND_MOUNT_WORK_COPY}" ]]; then
    local repo_root
    repo_root="$(compose_repo_root)"
    if [[ -d "${_COMPOSE_BIND_MOUNT_WORK_COPY}/test_runs" ]]; then
      mkdir -p "${repo_root}/test_runs"
      rsync -a --timeout=120 "${_COMPOSE_BIND_MOUNT_WORK_COPY}/test_runs/" "${repo_root}/test_runs/"
    fi
    if [[ "${COMPOSE_BIND_MOUNT_KEEP_WORKDIR:-1}" != "1" ]]; then
      rm -rf "${_COMPOSE_BIND_MOUNT_WORK_COPY}"
      _COMPOSE_BIND_MOUNT_WORK_COPY=""
    fi
  fi
  COMPOSE_BIND_MOUNT_DIR=""
}

compose_test() {
  podman_runtime_require
  compose_ensure_settings_yaml || return 1
  compose_ensure_test_overlay_yaml || return 1
  local repo_root
  repo_root="$(compose_repo_root)"
  local project_dir="${COMPOSE_BIND_MOUNT_DIR:-$repo_root}"
  (cd "$project_dir" && "${COMPOSE_TEST[@]}" "$@")
}

compose_service_container_id() {
  local service="$1"
  "${PODMAN[@]}" inspect --format '{{.Id}}' \
    "${HPCPERFSTATS_COMPOSE_PROJECT}_${service}_1" 2>/dev/null
}

compose_container_health() {
  "${PODMAN[@]}" inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' \
    "$1"
}

# Run tests/*_inner.sh inside the web container.
# Stream the script from the host via stdin: virtiofs bind mounts on cloud-sync paths
# can return EPERM when the container opens .sh paths on the mount for read/exec.
compose_run_inner_script() {
  local inner_rel="$1"
  shift
  local repo_root
  repo_root="$(compose_repo_root)"
  local inner_host="${repo_root}/${inner_rel}"
  local pip_helper="${repo_root}/tests/compose_inner_pip_install.sh"
  local docker_args=()
  local env_spec
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "-e" && $# -ge 2 ]]; then
      env_spec="$2"
      if [[ "$env_spec" != *=* ]]; then
        if [[ -z "${!env_spec+x}" ]]; then
          shift 2
          continue
        fi
        env_spec="${env_spec}=${!env_spec-}"
      fi
      docker_args+=("-e" "$env_spec")
      shift 2
      continue
    fi
    docker_args+=("$1")
    shift
  done
  if [[ ! -f "$inner_host" ]]; then
    echo "compose_run_inner_script: missing ${inner_host}" >&2
    return 1
  fi
  if [[ ! -f "$pip_helper" ]]; then
    echo "compose_run_inner_script: missing ${pip_helper}" >&2
    return 1
  fi
  if [[ -n "${COMPOSE_BIND_MOUNT_DIR:-}" ]]; then
    # Overlay ./hpcperfstats.ini (cloud virtiofs) after the tree mount.
    docker_args+=(
      -v "${COMPOSE_BIND_MOUNT_DIR}:/home/hpcperfstats:rw"
      -v "${COMPOSE_BIND_MOUNT_DIR}/hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro"
    )
  fi
  {
    cat "$pip_helper"
    echo
    tail -n +2 "$inner_host"
  } | compose_test run --rm -T "${docker_args[@]}" \
    "${compose_run_inner_script_bind_mount_env[@]}" \
    --entrypoint bash \
    web -s
}

# Set by compose_prepare_bind_mount; callers must invoke it before compose_run_inner_script.
compose_run_inner_script_bind_mount_env=()

compose_run_inner_script_prepare_env() {
  local repo_root
  repo_root="$(compose_repo_root)"
  compose_run_inner_script_bind_mount_env=()
  if [[ "${COMPOSE_BIND_MOUNT_DIR:-$repo_root}" == "$repo_root" ]]; then
    compose_run_inner_script_bind_mount_env=(-e DOCKER_PYTEST_BIND_MOUNT=1)
  fi
}

# Volume args for compose_test run invocations that mount the repo into web.
compose_web_repo_bind_mount_args() {
  if [[ -z "${COMPOSE_BIND_MOUNT_DIR:-}" ]]; then
    echo "compose_web_repo_bind_mount_args: call compose_prepare_bind_mount first" >&2
    return 1
  fi
  compose_web_repo_bind_mount_args=(
    -v "${COMPOSE_BIND_MOUNT_DIR}:/home/hpcperfstats:rw"
  )
  if [[ -f "${COMPOSE_BIND_MOUNT_DIR}/hpcperfstats.ini" ]]; then
    compose_web_repo_bind_mount_args+=(
      -v "${COMPOSE_BIND_MOUNT_DIR}/hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro"
    )
  fi
}
