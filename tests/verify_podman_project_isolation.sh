#!/usr/bin/env bash
# Prove test-project teardown preserves an unrelated positive-control volume.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=compose_test_cmd.sh
. tests/compose_test_cmd.sh
podman_runtime_require

control_volume="hpcperfstats-positive-control-$(cat /proc/sys/kernel/random/uuid)"
development_compose=(
  podman-compose
  --project-name hpcperfstats-dev
  -f docker-compose.yaml
  -f tests/docker-compose.test-overlay.yaml
)
development_compose_cmd() {
  HPCPERFSTATS_NETWORK_NAME=hpcperfstats-dev_net "${development_compose[@]}" "$@"
}
development_ids_before="$(development_compose_cmd ps -q | sort)"
if [[ -z "$development_ids_before" ]]; then
  echo "Development project must be running before isolation verification." >&2
  exit 1
fi

created=0
cleanup_control() {
  if [[ "$created" -eq 1 ]]; then
    "${PODMAN[@]}" volume rm "$control_volume" >/dev/null
  fi
}
trap cleanup_control EXIT

if "${PODMAN[@]}" volume exists "$control_volume"; then
  echo "Unexpected positive-control collision: ${control_volume}" >&2
  exit 1
fi
"${PODMAN[@]}" volume create "$control_volume" >/dev/null
created=1

podman_compose_teardown "${COMPOSE_TEST[@]}"

if ! "${PODMAN[@]}" volume exists "$control_volume"; then
  echo "Project teardown removed unrelated volume: ${control_volume}" >&2
  exit 1
fi
development_ids_after="$(development_compose_cmd ps -q | sort)"
if [[ "$development_ids_after" != "$development_ids_before" ]]; then
  echo "Test-project teardown changed the development project." >&2
  exit 1
fi
while IFS= read -r container_id; do
  if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Status}}' "$container_id")" != "running" ]]; then
    echo "Development container stopped during test teardown: ${container_id}" >&2
    exit 1
  fi
done <<<"$development_ids_after"
echo "PODMAN_PROJECT_ISOLATION_OK"
