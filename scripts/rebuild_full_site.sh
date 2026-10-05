#!/usr/bin/env bash
# Full-stack image rebuild: build musl GCC toolchain image, then compose-build all
# default services + db_pg18, then recreate without --build on up. Sets pipeline
# cgroup memory.high after pipeline is up. Never use podman-compose up --build here.
#
# Not for SPA-only releases — use rebuild_frontend.sh / rebuild_pipeline.sh when
# you need live frontend preservation (see rebuild_pipeline.sh).
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_full_site.sh
#   ./scripts/rebuild_full_site.sh --dry-run
#   ./scripts/rebuild_full_site.sh --build-only
#   ./scripts/rebuild_full_site.sh --no-start
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/podman_runtime.sh
source "${SCRIPT_DIR}/lib/podman_runtime.sh"
# shellcheck source=lib/compose_pipeline_memory_high.sh
source "${SCRIPT_DIR}/lib/compose_pipeline_memory_high.sh"

DRY_RUN=0
BUILD_ONLY=0
NO_START=0

GCC_MUSL_IMAGE=hpcperfstats-gcc-musl:16.2
GCC_ALPINE_DOCKERFILE=services-conf/gcc-alpine.Dockerfile

# Only services with compose `build:` (web → hpcperfstats; proxy → hpcperfstats-proxy).
# Build one service per `podman-compose build` — parallel multi-service build can fail
# after hpcperfstats tags while proxy still compiles (podman-compose runs builds concurrently).
COMPOSE_IMAGE_BUILD_SERVICES=(web proxy)
PG18_PROFILE=pg18-migrate
PG18_SERVICE=db_pg18

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_full_site.sh [options]

Build musl GCC (podman build), then default-stack images and hpcperfstats-db
(db_pg18 profile), then recreate the stack with:

  podman-compose up -d --force-recreate
  podman-compose --profile pg18-migrate up -d --force-recreate db_pg18

All image builds happen before any up step (no --build on up). Applies pipeline
memory.high after pipeline starts.

Full-stack downtime including db, redis, and rabbitmq. PG18 dual-run host
prereqs: docs/OPERATOR_PG18_MIGRATION.md.

For SPA or pipeline-only cutover use rebuild_frontend.sh / rebuild_pipeline.sh.

Options:
  --dry-run       Print planned steps only
  --build-only    Build images only; do not up -d or set memory.high
  --no-start      Build images, skip up -d and memory.high
  -h, --help      Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
  if [[ ! -f "${REPO_ROOT}/docker-compose.yaml" ]]; then
    echo "rebuild_full_site.sh: docker-compose.yaml not found under ${REPO_ROOT}" >&2
    exit 1
  fi
  cd "${REPO_ROOT}"
}

build_musl_gcc_toolchain_image() {
  LAST_STEP="podman build ${GCC_MUSL_IMAGE}"
  echo "Building musl GCC toolchain image ${GCC_MUSL_IMAGE} ..."
  run_cmd podman build -f "${GCC_ALPINE_DOCKERFILE}" -t "${GCC_MUSL_IMAGE}" services-conf
}

build_default_stack_images() {
  local svc
  echo "Building compose images (serial): ${COMPOSE_IMAGE_BUILD_SERVICES[*]} ..."
  for svc in "${COMPOSE_IMAGE_BUILD_SERVICES[@]}"; do
    LAST_STEP="podman-compose build ${svc}"
    echo "rebuild_full_site.sh: ${LAST_STEP} ..."
    run_cmd "${PODMAN_COMPOSE[@]}" build "${svc}"
  done
}

build_db_pg18_image() {
  LAST_STEP="podman-compose --profile ${PG18_PROFILE} build ${PG18_SERVICE}"
  echo "Building ${PG18_SERVICE} (profile ${PG18_PROFILE}) ..."
  run_cmd "${PODMAN_COMPOSE[@]}" --profile "${PG18_PROFILE}" build "${PG18_SERVICE}"
}

up_default_stack() {
  echo "Recreating default stack: up -d --force-recreate (no --build) ..."
  run_cmd "${PODMAN_COMPOSE[@]}" up -d --force-recreate
}

up_db_pg18() {
  echo "Recreating ${PG18_SERVICE}: --profile ${PG18_PROFILE} up -d --force-recreate (no --build) ..."
  run_cmd "${PODMAN_COMPOSE[@]}" --profile "${PG18_PROFILE}" up -d --force-recreate "${PG18_SERVICE}"
}

verify_default_stack_running() {
  local svc name
  for svc in web pipeline redis proxy db rabbitmq; do
    name="${HPCPERFSTATS_COMPOSE_PROJECT}_${svc}_1"
    if [[ "$("${PODMAN[@]}" inspect --format '{{.State.Running}}' "${name}" 2>/dev/null)" != "true" ]]; then
      echo "rebuild_full_site.sh: after up, ${name} is not running" >&2
      return 1
    fi
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
      echo "rebuild_full_site.sh: retry in isolation: podman-compose -p ${HPCPERFSTATS_COMPOSE_PROJECT} build proxy" >&2
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

main() {
  trap on_exit EXIT
  preflight
  PHASE=build
  echo "rebuild_full_site.sh: project=${HPCPERFSTATS_COMPOSE_PROJECT} DRY_RUN=${DRY_RUN} BUILD_ONLY=${BUILD_ONLY} NO_START=${NO_START}"
  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
    echo "NOTE: up -d --force-recreate runs only when all three flags above are 0." >&2
  fi
  build_musl_gcc_toolchain_image
  build_default_stack_images
  build_db_pg18_image

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

  PHASE=up
  echo "=== rebuild_full_site.sh: starting podman-compose up (force-recreate, no --build) ==="
  up_default_stack
  up_db_pg18
  verify_default_stack_running || exit 1
  UP_COMPLETED=1

  PHASE=memory_high
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    apply_pipeline_memory_high || exit 1
  fi

  PHASE=summary
  echo "Full-site rebuild complete (build + up + memory.high)."
  print_stack_summary
}

main "$@"
