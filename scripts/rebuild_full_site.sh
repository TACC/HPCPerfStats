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

DEFAULT_BUILD_SERVICES=(web pipeline redis proxy db rabbitmq)
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
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] $*"
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
  echo "Building musl GCC toolchain image ${GCC_MUSL_IMAGE} ..."
  run_cmd podman build -f "${GCC_ALPINE_DOCKERFILE}" -t "${GCC_MUSL_IMAGE}" .
}

build_default_stack_images() {
  echo "Building default stack images: ${DEFAULT_BUILD_SERVICES[*]} ..."
  run_cmd "${PODMAN_COMPOSE[@]}" build "${DEFAULT_BUILD_SERVICES[@]}"
}

build_db_pg18_image() {
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

main() {
  preflight
  build_musl_gcc_toolchain_image
  build_default_stack_images
  build_db_pg18_image

  if [[ "${BUILD_ONLY}" -eq 1 ]]; then
    echo "Skipping up -d (--build-only). Images built; containers unchanged."
    return 0
  fi

  if [[ "${NO_START}" -eq 1 ]]; then
    echo "Skipping up -d (--no-start). Run compose up when ready."
    return 0
  fi

  up_default_stack
  up_db_pg18

  if [[ "${DRY_RUN}" -eq 0 ]]; then
    apply_pipeline_memory_high || exit 1
  fi

  echo "Full-site rebuild complete. Status:"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    "${PODMAN_COMPOSE[@]}" ps 2>/dev/null || true
  fi
}

main "$@"
