#!/usr/bin/env bash
# Shared docker-compose invocation for test workflows (source, do not execute).
# Usage: . "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
#        compose_test up -d db redis

COMPOSE_TEST=(docker-compose -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml)
COMPOSE_BIND_MOUNT_DIR=""
_COMPOSE_BIND_MOUNT_WORK_COPY=""

compose_repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# virtiofs bind mounts from ProtonDrive/cloud-sync paths can deny listdir/open in
# the Linux VM (pip, bash, cp all hit EPERM). Rsync to a host work tree first,
# then bind-mount that tree (not the cloud-sync checkout). Default work copy:
# $HOME/.cache/hpcperfstats-compose (Colima shares $HOME into the VM). macOS /tmp
# is not bind-mountable unless Colima is started with --mount /tmp:w; use
# COMPOSE_BIND_MOUNT_USE_TMP=1 or COMPOSE_BIND_MOUNT_BASE_DIR=/tmp/hpcperfstats-compose
# only when /tmp is VM-visible.
compose_work_copy_base_dir() {
  if [[ -n "${COMPOSE_BIND_MOUNT_BASE_DIR:-}" ]]; then
    echo "${COMPOSE_BIND_MOUNT_BASE_DIR}"
    return
  fi
  if [[ "${COMPOSE_BIND_MOUNT_USE_TMP:-0}" == "1" ]]; then
    echo "/tmp/hpcperfstats-compose"
    return
  fi
  mkdir -p "${HOME}/.cache"
  echo "${HOME}/.cache/hpcperfstats-compose"
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
      "${repo_root}/docker-compose.app.yaml" \
      "${repo_root}/docker-compose.cpu-pinning.infra.yaml" \
      "${repo_root}/docker-compose.cpu-pinning.app.yaml" \
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
  local repo_root
  repo_root="$(compose_repo_root)"
  local use_work_copy="${COMPOSE_BIND_MOUNT_WORK_COPY:-}"
  local minimal_copy="${COMPOSE_BIND_MOUNT_MINIMAL:-0}"
  if [[ "${COMPOSE_BIND_MOUNT_SKIP_BUILD:-0}" == "1" ]]; then
    minimal_copy=1
  fi
  if [[ -z "$use_work_copy" ]]; then
    case "$repo_root" in
      *ProtonDrive*|*CloudStorage*|*iCloud*)
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

compose_test_project_args() {
  if [[ -n "${COMPOSE_BIND_MOUNT_DIR:-}" ]]; then
    printf '%s\n' --project-directory "${COMPOSE_BIND_MOUNT_DIR}"
  fi
}

compose_test() {
  local project_args=()
  if [[ -n "${COMPOSE_BIND_MOUNT_DIR:-}" ]]; then
    project_args=(--project-directory "${COMPOSE_BIND_MOUNT_DIR}")
  fi
  "${COMPOSE_TEST[@]}" "${project_args[@]}" "$@"
}

# Run tests/*_inner.sh inside the web container.
# Stream the script from the host via stdin: virtiofs bind mounts (e.g. ProtonDrive)
# can return EPERM when the container opens .sh paths on the mount for read/exec.
compose_run_inner_script() {
  local inner_rel="$1"
  shift
  local repo_root
  repo_root="$(compose_repo_root)"
  local inner_host="${repo_root}/${inner_rel}"
  local pip_helper="${repo_root}/tests/compose_inner_pip_install.sh"
  local docker_args=("$@")
  if [[ ! -f "$inner_host" ]]; then
    echo "compose_run_inner_script: missing ${inner_host}" >&2
    return 1
  fi
  if [[ ! -f "$pip_helper" ]]; then
    echo "compose_run_inner_script: missing ${pip_helper}" >&2
    return 1
  fi
  if [[ -n "${COMPOSE_BIND_MOUNT_DIR:-}" ]]; then
    # Overlay docker-compose.app.yaml ./hpcperfstats.ini (cloud virtiofs) after the tree mount.
    docker_args+=(
      -v "${COMPOSE_BIND_MOUNT_DIR}:/home/hpcperfstats:rw"
      -v "${COMPOSE_BIND_MOUNT_DIR}/hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro"
    )
  fi
  {
    cat "$pip_helper"
    echo
    tail -n +2 "$inner_host"
  } | compose_test run --rm -i "${docker_args[@]}" \
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
