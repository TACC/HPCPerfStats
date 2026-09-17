#!/usr/bin/env bash
# Remove only resources owned by the selected Podman Compose project.

_podman_teardown_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! declare -F podman_runtime_require >/dev/null 2>&1; then
  # shellcheck source=../scripts/lib/podman_runtime.sh
  . "${_podman_teardown_repo_root}/scripts/lib/podman_runtime.sh"
fi

# Usage: podman_compose_teardown "${COMPOSE_TEST[@]}"
podman_compose_teardown() {
  podman_runtime_require || return
  if [[ $# -lt 1 ]]; then
    echo "podman_compose_teardown: missing project-scoped compose command" >&2
    return 2
  fi
  local repo_root project_dir
  repo_root="$_podman_teardown_repo_root"
  project_dir="${COMPOSE_BIND_MOUNT_DIR:-$repo_root}"
  (cd "$project_dir" && "$@" down -v --remove-orphans) || return

  local volume_name
  while IFS= read -r volume_name; do
    case "$volume_name" in
      "${HPCPERFSTATS_COMPOSE_PROJECT}_"*)
        "${PODMAN[@]}" volume rm "$volume_name"
        ;;
    esac
  done < <("${PODMAN[@]}" volume ls --format '{{.Name}}')
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  cd "$_podman_teardown_repo_root"
  overlay="tests/docker-compose.test-overlay.yaml.example"
  if [[ -f "tests/docker-compose.test-overlay.yaml" ]]; then
    overlay="tests/docker-compose.test-overlay.yaml"
  fi
  podman_compose_teardown \
    "${PODMAN_COMPOSE[@]}" \
    -f docker-compose.yaml \
    -f "$overlay"
fi
