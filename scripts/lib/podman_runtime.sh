#!/usr/bin/env bash
# Canonical rootless Podman runtime contract for local workflow scripts.

if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "podman_runtime.sh requires /bin/bash" >&2
  return 2 2>/dev/null || exit 2
fi

: "${USER:?USER must identify the rootless Podman account}"
: "${HPCPERFSTATS_COMPOSE_PROJECT:=hpcperfstats-test}"
: "${HPCPERFSTATS_HOST_ROOT:=/data/user/${USER}}"
: "${HPCPERFSTATS_PODMAN_ROOT:=/data/podman/${USER}}"
: "${HPCPERFSTATS_HOST_CACHE:=${HPCPERFSTATS_HOST_ROOT}/cache}"
: "${HPCPERFSTATS_HOST_TMP:=${HPCPERFSTATS_HOST_ROOT}/tmp}"
: "${HPCPERFSTATS_HOST_TOOLS:=${HPCPERFSTATS_HOST_ROOT}/tools}"

case "$HPCPERFSTATS_COMPOSE_PROJECT" in
  hpcperfstats-test)
    HPCPERFSTATS_NETWORK_NAME=hpcperfstats-test_net
    ;;
  hpcperfstats-dev)
    HPCPERFSTATS_NETWORK_NAME=hpcperfstats-dev_net
    ;;
  *)
    echo "Unsupported local Compose project: ${HPCPERFSTATS_COMPOSE_PROJECT}" >&2
    return 64 2>/dev/null || exit 64
    ;;
esac

export HPCPERFSTATS_COMPOSE_PROJECT
export HPCPERFSTATS_NETWORK_NAME
export HPCPERFSTATS_HOST_ROOT HPCPERFSTATS_PODMAN_ROOT
export HPCPERFSTATS_HOST_CACHE HPCPERFSTATS_HOST_TMP HPCPERFSTATS_HOST_TOOLS
export XDG_CACHE_HOME="${HPCPERFSTATS_PODMAN_ROOT}/cache"
export TMPDIR="${HPCPERFSTATS_PODMAN_ROOT}/tmp"
export PIP_CACHE_DIR="${HPCPERFSTATS_HOST_CACHE}/pip"
export npm_config_cache="${HPCPERFSTATS_HOST_CACHE}/npm"
export npm_config_prefix="${HPCPERFSTATS_HOST_TOOLS}/npm"
export PLAYWRIGHT_BROWSERS_PATH="${HPCPERFSTATS_HOST_CACHE}/ms-playwright"
export NEXT_TELEMETRY_DISABLED=1

PODMAN=(podman)
PODMAN_COMPOSE=(
  podman-compose
  --project-name "${HPCPERFSTATS_COMPOSE_PROJECT}"
)

podman_runtime_require() {
  local directory command_name
  local storage_conf="${CONTAINERS_STORAGE_CONF:-${HOME}/.config/containers/storage.conf}"
  local runtime_info rootless graph_root volume_path image_tmp imagestore
  local expected_graph_root="${HPCPERFSTATS_PODMAN_ROOT}/storage"
  local expected_volume_path="${HPCPERFSTATS_PODMAN_ROOT}/volumes"
  local expected_image_tmp="${HPCPERFSTATS_PODMAN_ROOT}/tmp"
  local expected_imagestore="${HPCPERFSTATS_PODMAN_ROOT}/images"
  if [[ "${_PODMAN_RUNTIME_VERIFIED:-0}" == "1" ]]; then
    return 0
  fi
  for command_name in "${PODMAN[0]}" "${PODMAN_COMPOSE[0]}"; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      echo "Required rootless runtime command not found: ${command_name}" >&2
      return 69
    fi
  done
  for directory in \
    "$HPCPERFSTATS_HOST_CACHE" \
    "$HPCPERFSTATS_HOST_TMP" \
    "$HPCPERFSTATS_HOST_TOOLS" \
    "${HPCPERFSTATS_PODMAN_ROOT}/storage" \
    "${HPCPERFSTATS_PODMAN_ROOT}/images" \
    "${HPCPERFSTATS_PODMAN_ROOT}/volumes" \
    "${HPCPERFSTATS_PODMAN_ROOT}/cache" \
    "${HPCPERFSTATS_PODMAN_ROOT}/tmp"; do
    if [[ ! -d "$directory" || ! -w "$directory" ]]; then
      echo "Required writable /data runtime directory is unavailable: ${directory}" >&2
      return 73
    fi
  done
  runtime_info="$(
    "${PODMAN[@]}" info \
      --format '{{.Host.Security.Rootless}}|{{.Store.GraphRoot}}|{{.Store.VolumePath}}|{{.Store.ImageCopyTmpDir}}'
  )" || return
  IFS='|' read -r rootless graph_root volume_path image_tmp <<<"$runtime_info"
  if [[ "$rootless" != "true" ]]; then
    echo "Podman must run rootlessly for project ${HPCPERFSTATS_COMPOSE_PROJECT}" >&2
    return 77
  fi
  if [[ "$graph_root" != "$expected_graph_root" ||
        "$volume_path" != "$expected_volume_path" ||
        "$image_tmp" != "$expected_image_tmp" ]]; then
    echo "Effective Podman storage mismatch: graphRoot=${graph_root}, volumePath=${volume_path}, imageCopyTmpDir=${image_tmp}" >&2
    return 78
  fi
  if [[ ! -r "$storage_conf" ]]; then
    echo "Podman storage configuration is not readable: ${storage_conf}" >&2
    return 78
  fi
  imagestore="$(
    awk '
      /^[[:space:]]*\[storage\][[:space:]]*$/ { in_storage = 1; next }
      /^[[:space:]]*\[/ { in_storage = 0 }
      in_storage && /^[[:space:]]*imagestore[[:space:]]*=/ {
        value = $0
        sub(/^[^=]*=[[:space:]]*/, "", value)
        sub(/[[:space:]]*#.*/, "", value)
        gsub(/^[[:space:]"]+|[[:space:]"]+$/, "", value)
        print value
        exit
      }
    ' "$storage_conf"
  )"
  if [[ "$imagestore" != "$expected_imagestore" ]]; then
    echo "Effective Podman storage mismatch: imagestore=${imagestore:-unset} expected=${expected_imagestore}" >&2
    return 78
  fi
  _PODMAN_RUNTIME_VERIFIED=1
}

podman_container_health() {
  local container_id="$1"
  "${PODMAN[@]}" inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' \
    "$container_id"
}
