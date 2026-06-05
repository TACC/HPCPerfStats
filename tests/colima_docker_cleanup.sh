#!/usr/bin/env bash
# Prune Colima Docker after compose-backed test workflows (containers, images, build cache, volumes, networks).
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-}"
export DOCKER_HOST="${DOCKER_HOST:-unix://${HOME}/.colima/default/docker.sock}"

if [[ "${COLIMA_DOCKER_CLEANUP_SKIP:-}" == "1" ]]; then
  echo "Colima Docker cleanup skipped (COLIMA_DOCKER_CLEANUP_SKIP=1)."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "colima_docker_cleanup: docker CLI not found; skipping." >&2
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo "colima_docker_cleanup: docker unreachable (is Colima running?); skipping." >&2
  exit 0
fi

echo "Colima Docker cleanup: pruning stopped containers, unused images, build cache, volumes, and networks..."

_prune() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "  $label: done"
  else
    echo "  $label: skipped or failed (non-fatal)" >&2
  fi
}

_prune "containers" docker container prune -f
_prune "images" docker image prune -a -f
_prune "build cache" docker builder prune -a -f
_prune "volumes" docker volume prune -f
_prune "networks" docker network prune -f

echo "Colima Docker cleanup finished."
