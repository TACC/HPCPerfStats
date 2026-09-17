#!/usr/bin/env bash
# Start and verify the isolated development project without tearing it down.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-dev
export HPCPERFSTATS_COMPOSE_DEVELOPMENT=1
export HPCPERFSTATS_HTTP_PORT=8080
export HPCPERFSTATS_HTTPS_PORT=8443
export HPCPERFSTATS_SYSLOG_PORT=1514
export HPCPERFSTATS_AMQP_PORT=5673
# shellcheck source=compose_test_cmd.sh
. tests/compose_test_cmd.sh
podman_runtime_require

compose_ensure_settings_yaml
compose_ensure_test_overlay_yaml
compose_test up -d db redis rabbitmq web pipeline proxy

for _ in $(seq 1 60); do
  unhealthy=0
  for service in db redis rabbitmq web pipeline proxy; do
    container_id="$(compose_service_container_id "$service")"
    if [[ -z "$container_id" ]]; then
      unhealthy=1
      break
    fi
    state="$(
      "${PODMAN[@]}" inspect \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$container_id"
    )"
    case "$state" in
      healthy|running) ;;
      *)
        unhealthy=1
        break
        ;;
    esac
  done
  if [[ "$unhealthy" -eq 0 ]]; then
    break
  fi
  sleep 2
done

if [[ "$unhealthy" -ne 0 ]]; then
  echo "Development stack did not become ready within 120 seconds." >&2
  compose_test ps >&2
  exit 1
fi

published_port() {
  local service="$1"
  local container_port="$2"
  local expected_host_port="$3"
  local container_id mapping
  container_id="$(compose_service_container_id "$service")"
  mapping="$("${PODMAN[@]}" port "$container_id" "$container_port")"
  case "$mapping" in
    *":${expected_host_port}") return 0 ;;
    *)
      echo "${service} ${container_port} is not published on ${expected_host_port}: ${mapping}" >&2
      return 1
      ;;
  esac
}

published_port proxy 80/tcp 8080
published_port proxy 443/tcp 8443
published_port pipeline 514/tcp 1514
published_port pipeline 514/udp 1514
published_port rabbitmq 5672/tcp 5673
echo "PODMAN_DEVELOPMENT_STACK_OK"
