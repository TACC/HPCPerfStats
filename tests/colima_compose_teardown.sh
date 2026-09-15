#!/usr/bin/env bash
# Shared compose teardown + Colima Docker prune for tests/run_*_workflow.sh scripts.
# Source from workflow scripts: . "$(dirname "$0")/colima_compose_teardown.sh"
#
# Local Docker/Colima compose workflows are OFF unless explicitly enabled:
#   export HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1

_colima_teardown_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

hpcperfstats_local_docker_enabled() {
  [[ "${HPCPERFSTATS_ENABLE_LOCAL_DOCKER:-0}" == "1" ]]
}

# Exit non-zero when local Docker/Colima compose workflows are disabled.
# Call from workflow entrypoints (colima_export_docker_env / compose_test).
hpcperfstats_require_local_docker() {
  if hpcperfstats_local_docker_enabled; then
    return 0
  fi
  cat >&2 <<'EOF'
Local Docker/Colima compose workflows are disabled on this machine
(HPCPERFSTATS_ENABLE_LOCAL_DOCKER is not 1). Use host .venv pytest for unit
tests. Re-enable with: export HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1
EOF
  exit 78
}

colima_export_docker_env() {
  hpcperfstats_require_local_docker
  export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-}"
  export DOCKER_HOST="${DOCKER_HOST:-unix://${HOME}/.colima/default/docker.sock}"
}

# Usage: colima_compose_teardown docker-compose [extra compose flags...]
#        colima_compose_teardown docker-compose -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml
colima_compose_teardown() {
  if ! hpcperfstats_local_docker_enabled; then
    echo "colima_compose_teardown: skipped (local Docker disabled)." >&2
    return 0
  fi
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
