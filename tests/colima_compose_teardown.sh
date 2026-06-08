#!/usr/bin/env bash
# Shared compose teardown + Colima Docker prune for tests/run_*_workflow.sh scripts.
# Source from workflow scripts: . "$(dirname "$0")/colima_compose_teardown.sh"

_colima_teardown_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

colima_export_docker_env() {
  export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-}"
  export DOCKER_HOST="${DOCKER_HOST:-unix://${HOME}/.colima/default/docker.sock}"
}

# Usage: colima_compose_teardown docker-compose [extra compose flags...]
#        colima_compose_teardown docker-compose -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml
colima_compose_teardown() {
  colima_export_docker_env
  if [[ $# -lt 1 ]]; then
    echo "colima_compose_teardown: missing compose command" >&2
    return 1
  fi
  local project_args=()
  if [[ -n "${COMPOSE_BIND_MOUNT_DIR:-}" ]]; then
    project_args=(--project-directory "${COMPOSE_BIND_MOUNT_DIR}")
  fi
  echo "Tearing down compose services and volumes..."
  "$@" "${project_args[@]}" down -v --remove-orphans || true
  bash "${_colima_teardown_script_dir}/colima_docker_cleanup.sh"
}
