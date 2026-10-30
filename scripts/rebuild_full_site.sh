#!/usr/bin/env bash
# Full-stack image rebuild: podman build web, proxy, db_pg18; compose down + up -d
# --no-build. The db_pg18 image is built, but its container (profile pg18-migrate) is NOT started.
# CPython bake uses --enable-optimizations only (no custom Clang host profiles).
# Pipeline cgroup memory.high is commented out (do not throttle below memory.max).
# Never use podman-compose up --build here.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_full_site.sh
#   ./scripts/rebuild_full_site.sh --dry-run
#   ./scripts/rebuild_full_site.sh --build-only
#   ./scripts/rebuild_full_site.sh --no-start
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "rebuild_full_site.sh: run ./scripts/rebuild_full_site.sh — do not source (.) this file; exit would close your shell" >&2
  return 2 2>/dev/null || exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/podman_runtime.sh
source "${SCRIPT_DIR}/lib/podman_runtime.sh"
# shellcheck source=lib/compose_pipeline_memory_high.sh
source "${SCRIPT_DIR}/lib/compose_pipeline_memory_high.sh"
# shellcheck source=lib/compose_frontend_helpers.sh
source "${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"
# shellcheck source=lib/pipeline_supervisor_shutdown.sh
source "${SCRIPT_DIR}/lib/pipeline_supervisor_shutdown.sh"

DRY_RUN=0
BUILD_ONLY=0
NO_START=0

PG18_SERVICE=db_pg18

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_full_site.sh [options]

Build default-stack images and hpcperfstats-db (db_pg18 image), then tear down
the compose project (graceful stop, preserve volumes) and start the default stack.
The db_pg18 container (profile pg18-migrate) is built but NOT started:

  podman-compose down --remove-orphans
  podman-compose up -d --no-build

Down uses each service stop_grace_period from compose (do not pass down -t; a global
timeout overrides longer db/rabbitmq/pg18 grace and risks SIGKILL mid-shutdown).

Images bake ThinLTO + CPython --enable-optimizations (upstream mini profile path).
There is no custom Clang host-profile bake path and no legacy profile-phase flags.

Options:
  --dry-run        Print planned steps only
  --build-only     Build images only; do not up -d
  --no-start       Build images, skip up -d
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile-phase | --pgo-*)
      echo "rebuild_full_site.sh: custom Clang host profiling was removed; unsupported option: $1" >&2
      echo "rebuild_full_site.sh: images always bake with CPython --enable-optimizations only" >&2
      usage >&2
      exit 2
      ;;
    --full-*)
      if [[ "${1#--full-}" == pgo ]]; then
        echo "rebuild_full_site.sh: custom Clang host profiling was removed; unsupported option: $1" >&2
        echo "rebuild_full_site.sh: images always bake with CPython --enable-optimizations only" >&2
        usage >&2
        exit 2
      fi
      echo "rebuild_full_site.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --build-only)
      BUILD_ONLY=1
      shift
      ;;
    --no-start)
      NO_START=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "rebuild_full_site.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run_cmd() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] (not executed) $*"
    return 0
  fi
  "$@"
}

preflight() {
  podman_runtime_require
  if [[ ! -f "${REPO_ROOT}/docker-compose.defaults.yaml" ]]; then
    echo "rebuild_full_site.sh: docker-compose.defaults.yaml not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  if [[ ! -f "${REPO_ROOT}/docker-compose.yaml" ]]; then
    echo "rebuild_full_site.sh: missing docker-compose.yaml (cp docker-compose.yaml.example docker-compose.yaml)" >&2
    exit 1
  fi
  cd "${REPO_ROOT}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return 0
  fi
  if ! "${PODMAN_COMPOSE[@]}" config >/dev/null 2>&1; then
    echo "rebuild_full_site.sh: compose parse failed (often invalid docker-compose.yaml)." >&2
    echo "rebuild_full_site.sh: paste whole optional blocks from docker-compose.yaml.example — do not uncomment lines inside a block partially." >&2
    "${PODMAN_COMPOSE[@]}" config 2>&1 | tail -25 >&2 || true
    exit 1
  fi
}

run_stack_up_and_verify() {
  PHASE=up
  compose_up_project_stack
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    verify_default_stack_running || return 1
  fi
  UP_COMPLETED=1
}

compose_build_web_image() {
  local git_commit
  LAST_STEP="podman build web"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  git_commit="${HPCPERFSTATS_GIT_COMMIT:-unknown}"
  if [[ "${git_commit}" == "unknown" ]] \
    && command -v git >/dev/null 2>&1 \
    && git -C "${REPO_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
    git_commit="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  fi
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/pipeline_web.Dockerfile" \
    -t hpcperfstats \
    --target hpcperfstats-full \
    --build-arg "HPCPERFSTATS_GIT_COMMIT=${git_commit}" \
    "${REPO_ROOT}"
}

compose_build_proxy_image() {
  LAST_STEP="podman build proxy"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/proxy.Dockerfile" \
    -t hpcperfstats-proxy \
    "${REPO_ROOT}"
}

compose_build_db_pg18() {
  LAST_STEP="podman build ${PG18_SERVICE}"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/db.Dockerfile" \
    -t hpcperfstats-db \
    "${REPO_ROOT}/services-conf"
}

compose_build_all_images() {
  compose_build_web_image || return 1
  compose_build_proxy_image || return 1
  compose_build_db_pg18 || return 1
  compose_ensure_local_stack_image_tags
}

compose_down_project() {
  LAST_STEP="pipeline supervisor shutdown"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  pipeline_supervisor_shutdown
  LAST_STEP="podman-compose down --remove-orphans"
  echo "rebuild_full_site.sh: ${LAST_STEP} (volumes preserved; no -v; stop_grace_period from compose) ..."
  run_cmd "${PODMAN_COMPOSE[@]}" down --remove-orphans
}

compose_up_project_stack() {
  compose_down_project
  LAST_STEP="podman-compose up -d --no-build (default stack; ${PG18_SERVICE} not started)"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN_COMPOSE[@]}" up -d --no-build
}

# Legacy names kept for callers/tests; default-profile up never starts db_pg18.
up_default_stack() {
  compose_up_project_stack
}

up_db_pg18() {
  :
}

compose_service_container_name() {
  printf '%s_%s_1' "${HPCPERFSTATS_COMPOSE_PROJECT}" "$1"
}

wait_for_compose_service_running() {
  local svc="$1"
  local timeout="${2:-${HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT:-300}}"
  local name elapsed=0
  name="$(compose_service_container_name "${svc}")"
  while [[ "${elapsed}" -lt "${timeout}" ]]; do
    if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${name}" 2>/dev/null)" == "true" ]]; then
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "rebuild_full_site.sh: timed out waiting for ${name} to run (${timeout}s)" >&2
  "${PODMAN[@]}" logs --tail 50 "${name}" 2>&1 || true
  return 1
}

verify_default_stack_running() {
  local svc
  # Hub PG15 (db) stays live; db_pg18 (profile pg18-migrate) is intentionally not started.
  local -a required=(web pipeline redis proxy db rabbitmq)
  for svc in "${required[@]}"; do
    if ! compose_service_container_exists "${svc}"; then
      echo "rebuild_full_site.sh: required service container missing: ${svc}" >&2
      return 1
    fi
    wait_for_compose_service_running "${svc}" || return 1
  done
}

PHASE=preflight
UP_COMPLETED=0
LAST_STEP=

on_exit() {
  local ec=$?
  if [[ "${UP_COMPLETED}" -eq 0 && "${BUILD_ONLY}" -eq 0 && "${NO_START}" -eq 0 && "${DRY_RUN}" -eq 0 ]]; then
    echo "rebuild_full_site.sh: exited (code ${ec}) before podman-compose up finished (phase=${PHASE} last_step=${LAST_STEP:-unknown})." >&2
    if [[ "${PHASE}" == build ]]; then
      echo "rebuild_full_site.sh: scroll above for the failing build step (often proxy or db_pg18 after hpcperfstats:latest tags)." >&2
      echo "rebuild_full_site.sh: retry in isolation: podman build -f ${REPO_ROOT}/services-conf/proxy.Dockerfile -t hpcperfstats-proxy ${REPO_ROOT}" >&2
    fi
    echo "rebuild_full_site.sh: collectstatic in Dockerfile output alone is NOT a successful full rebuild." >&2
  fi
}

print_stack_summary() {
  local net_id svc name
  echo ""
  echo "=== Stack summary: project=${HPCPERFSTATS_COMPOSE_PROJECT} network=${HPCPERFSTATS_NETWORK_NAME} ==="
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] skipped stack summary"
    return 0
  fi
  net_id="$("${PODMAN[@]}" network inspect "${HPCPERFSTATS_NETWORK_NAME}" --format '{{.Id}}' 2>/dev/null || echo 'MISSING')"
  echo "Network ${HPCPERFSTATS_NETWORK_NAME} id=${net_id}"
  echo ""
  echo "--- podman-compose ps -a ---"
  "${PODMAN_COMPOSE[@]}" ps -a 2>/dev/null || true
  echo ""
  echo "--- Containers (name / container ID / image ID / status) ---"
  "${PODMAN[@]}" ps -a \
    --filter "label=io.podman.compose.project=${HPCPERFSTATS_COMPOSE_PROJECT}" \
    --format 'table {{.Names}}\t{{.ID}}\t{{.ImageID}}\t{{.Status}}' 2>/dev/null || true
  echo ""
  echo "--- Per-service inspect (container + image) ---"
  for svc in web pipeline redis proxy db rabbitmq "${PG18_SERVICE}"; do
    name="${HPCPERFSTATS_COMPOSE_PROJECT}_${svc}_1"
    if "${PODMAN[@]}" container exists "${name}" 2>/dev/null; then
      "${PODMAN[@]}" inspect "${name}" --format "${svc}: container={{.Id}} image={{.Image}}" 2>/dev/null || true
    else
      echo "${svc}: (no container ${name})"
    fi
  done
}

run_compose_up_memory_high_and_summary() {
  echo "=== rebuild_full_site.sh: starting podman-compose down + up (no --build) ==="
  run_stack_up_and_verify || exit 1

  # memory.high left at the cgroup default (max). Do not call apply_pipeline_memory_high.
  # PHASE=memory_high
  # if [[ "${DRY_RUN}" -eq 0 ]]; then
  #   apply_pipeline_memory_high || exit 1
  # fi

  PHASE=summary
  print_stack_summary
}

main() {
  trap on_exit EXIT
  preflight

  PHASE=build
  echo "rebuild_full_site.sh: project=${HPCPERFSTATS_COMPOSE_PROJECT} DRY_RUN=${DRY_RUN} BUILD_ONLY=${BUILD_ONLY} NO_START=${NO_START}"
  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
    echo "NOTE: compose down + up runs only when all three flags above are 0." >&2
  fi
  compose_build_all_images

  if [[ "${BUILD_ONLY}" -eq 1 ]]; then
    echo "rebuild_full_site.sh: FINISHED BUILD ONLY — did not run podman-compose up (use without --build-only to recreate containers)." >&2
    UP_COMPLETED=1
    return 0
  fi

  if [[ "${NO_START}" -eq 1 ]]; then
    echo "rebuild_full_site.sh: FINISHED WITHOUT UP — did not run podman-compose up (use without --no-start to recreate containers)." >&2
    UP_COMPLETED=1
    return 0
  fi

  run_compose_up_memory_high_and_summary
  echo "Full-site rebuild complete (build + up)."
}

main "$@"
