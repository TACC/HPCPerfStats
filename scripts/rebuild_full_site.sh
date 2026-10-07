#!/usr/bin/env bash
# Full-stack image rebuild: compose-build default services + db_pg18, then recreate
# without --build on up. Optional live-soak PGO via --profile-phase. Sets pipeline
# cgroup memory.high after pipeline is up. Never use podman-compose up --build here.
#
# Usage (from the git checkout that contains docker-compose.yaml):
#   ./scripts/rebuild_full_site.sh
#   ./scripts/rebuild_full_site.sh --profile-phase
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
# shellcheck source=pgo_lib.sh
source "${SCRIPT_DIR}/pgo_lib.sh"

DRY_RUN=0
BUILD_ONLY=0
NO_START=0
PROFILE_PHASE=0

export PGOROOT="${PGOROOT:-${HPCPERFSTATS_PGO_ROOT:-/root/.hpcperfstats_pgo}}"

COMPOSE_IMAGE_BUILD_SERVICES=(web proxy)
PG18_PROFILE=pg18-migrate
PG18_SERVICE=db_pg18
PGO_PHASE=skip

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_full_site.sh [options]

Build default-stack images and hpcperfstats-db (db_pg18 profile), then recreate
the stack with:

  podman-compose up -d --force-recreate
  podman-compose --profile pg18-migrate up -d --force-recreate db_pg18

PGO (optional): PGOROOT defaults to /root/.hpcperfstats_pgo. --profile-phase
wipes PGOROOT and builds with PGO_PHASE=generate for soak; if a complete profile
set already exists (all namespaces ready), you must confirm interactively or set
HPC_PGO_PROFILE_PHASE_FORCE_WIPE=1. After soak, the first default run performs
one merge + PGO_PHASE=use rebuild (db_pg18, proxy, web), then skip until the next
--profile-phase.

Options:
  --profile-phase  PGO generate build + up for live soak (fresh PGOROOT wipe)
  --dry-run        Print planned steps only
  --build-only     Build images only; do not up -d or set memory.high
  --no-start       Build images, skip up -d and memory.high
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile-phase)
      PROFILE_PHASE=1
      shift
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

resolve_pgo_phase() {
  if [[ "${PROFILE_PHASE}" -eq 1 ]]; then
    PGO_PHASE=generate
    echo "PGO: profile-phase → PGO_PHASE=generate" >&2
    return 0
  fi
  local crumb
  crumb="$(pgo_use_breadcrumb_path)"
  if [[ -f "${crumb}" ]]; then
    PGO_PHASE=skip
    echo "PGO: skip — use breadcrumb present (${crumb})" >&2
    return 0
  fi
  if profiles_ready "${REPO_ROOT}"; then
    PGO_PHASE=use
    echo "PGO: profiles ready (soak/generate satisfied for all namespaces) → one-time PGO_PHASE=use rebuild" >&2
    return 0
  fi
  PGO_PHASE=skip
  echo "PGO: skip — profiles not ready; run --profile-phase for generate, then soak stack until every namespace has profiles" >&2
}

prepare_profile_phase() {
  mkdir -p "$(pgo_root_dir)/breadcrumbs"
  date -u +"%Y-%m-%dT%H:%M:%SZ" >"${PGOROOT}/breadcrumbs/profile_phase_started"
}

run_pgo_use_path() {
  echo "=== PGO one-time use rebuild (merge → build db_pg18/proxy/web → up) ===" >&2
  profiles_ready "${REPO_ROOT}" \
    || pgo_die "PGO use rebuild refused: profiles_ready false (soak not confirmed for every namespace)"
  local failed=0
  if ! run_cmd "${SCRIPT_DIR}/pgo_merge_namespaces.sh"; then
    failed=1
  fi
  if [[ "${failed}" -eq 0 ]]; then
    if ! compose_build_all_with_pgo; then
      failed=1
    fi
  fi
  if [[ "${failed}" -ne 0 ]]; then
    date -u +"%Y-%m-%dT%H:%M:%SZ" >"$(pgo_use_failed_path)"
    echo "PGO: use rebuild FAILED — breadcrumb NOT written" >&2
    pgo_die "use path failed (see above)"
  fi
  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
    echo "PGO: use build finished; skipping up due to flags" >&2
  else
    PHASE=up
    up_default_stack
    up_db_pg18
    verify_default_stack_running || pgo_die "stack verify failed after PGO use up"
    UP_COMPLETED=1
  fi
  date -u +"%Y-%m-%dT%H:%M:%SZ" >"$(pgo_use_breadcrumb_path)"
  echo "PGO: use rebuild complete; wrote pgo_use_full_rebuild.done" >&2
}

compose_build_service() {
  local svc="$1"
  case "${svc}" in
    web)
      compose_build_web_image
      return $?
      ;;
    proxy)
      compose_build_proxy_image
      return $?
      ;;
  esac
  LAST_STEP="podman-compose build ${svc} (PGO_PHASE=${PGO_PHASE})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd env PGO_PHASE="${PGO_PHASE}" PGO_ROOT="${PGOROOT}" PGOROOT="${PGOROOT}" \
    "${PODMAN_COMPOSE[@]}" build \
    --build-arg "PGO_PHASE=${PGO_PHASE}" \
    --build-arg "PGO_ROOT=/root/.hpcperfstats_pgo" \
    "${svc}"
}

compose_build_web_image() {
  local pgo_ctx git_commit
  LAST_STEP="podman build web (PGO_PHASE=${PGO_PHASE} PGOROOT=${PGOROOT})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  git_commit="${HPCPERFSTATS_GIT_COMMIT:-unknown}"
  if [[ "${git_commit}" == "unknown" ]] \
    && command -v git >/dev/null 2>&1 \
    && git -C "${REPO_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
    git_commit="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  fi
  mapfile -t pgo_ctx < <(pgo_podman_build_context_args)
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/Dockerfile" \
    -t hpcperfstats \
    --target hpcperfstats-full \
    --build-arg "HPCPERFSTATS_GIT_COMMIT=${git_commit}" \
    --build-arg "PGO_PHASE=${PGO_PHASE}" \
    --build-arg "PGO_ROOT=/root/.hpcperfstats_pgo" \
    "${pgo_ctx[@]}" \
    "${REPO_ROOT}"
}

compose_build_proxy_image() {
  local pgo_ctx
  LAST_STEP="podman build proxy (PGO_PHASE=${PGO_PHASE} PGOROOT=${PGOROOT})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  mapfile -t pgo_ctx < <(pgo_podman_build_context_args)
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/proxy.Dockerfile" \
    -t hpcperfstats-proxy \
    --build-arg "PGO_PHASE=${PGO_PHASE}" \
    --build-arg "PGO_ROOT=/root/.hpcperfstats_pgo" \
    "${pgo_ctx[@]}" \
    "${REPO_ROOT}"
}

compose_build_db_pg18() {
  local pgo_ctx
  LAST_STEP="podman build ${PG18_SERVICE} (PGO_PHASE=${PGO_PHASE} PGOROOT=${PGOROOT})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  mapfile -t pgo_ctx < <(pgo_podman_build_context_args)
  run_cmd "${PODMAN[@]}" build \
    -f "${REPO_ROOT}/services-conf/db.Dockerfile" \
    -t hpcperfstats-db \
    --build-arg "PGO_PHASE=${PGO_PHASE}" \
    --build-arg "PGO_ROOT=/root/.hpcperfstats_pgo" \
    "${pgo_ctx[@]}" \
    "${REPO_ROOT}/services-conf"
}

compose_build_all_with_pgo() {
  local svc
  for svc in "${COMPOSE_IMAGE_BUILD_SERVICES[@]}"; do
    compose_build_service "${svc}" || return 1
  done
  compose_build_db_pg18 || return 1
}

build_default_stack_images() {
  compose_build_all_with_pgo
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
  pgo_print_summary "${PGO_PHASE}"
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
  # Always create PGOROOT on the host (even --dry-run). Compose pgo_profiles binds and
  # Dockerfile RUN --mount=bind require the path before build/up — not gated on run_cmd.
  if [[ "${PROFILE_PHASE}" -eq 1 ]]; then
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      if profiles_ready "${REPO_ROOT}"; then
        echo "[dry-run] --profile-phase would prompt before wiping complete PGOROOT at $(pgo_root_dir)" >&2
      else
        echo "[dry-run] --profile-phase would wipe PGOROOT at $(pgo_root_dir) (incomplete or empty profile set)" >&2
      fi
    else
      pgo_confirm_wipe_pgroot_for_profile_phase "${REPO_ROOT}"
    fi
  fi
  "${SCRIPT_DIR}/pgo_ensure_layout.sh"
  resolve_pgo_phase
  pgo_die_if_partial_profile_collection "${REPO_ROOT}" "${PROFILE_PHASE}"

  if [[ "${PGO_PHASE}" == use && "${PROFILE_PHASE}" -eq 0 ]]; then
    run_pgo_use_path
    PHASE=memory_high
    if [[ "${DRY_RUN}" -eq 0 && "${BUILD_ONLY}" -eq 0 && "${NO_START}" -eq 0 ]]; then
      apply_pipeline_memory_high || exit 1
    fi
    PHASE=summary
    echo "Full-site PGO use rebuild complete."
    print_stack_summary
    return 0
  fi

  if [[ "${PROFILE_PHASE}" -eq 1 ]]; then
    prepare_profile_phase
  fi

  PHASE=build
  echo "rebuild_full_site.sh: project=${HPCPERFSTATS_COMPOSE_PROJECT} DRY_RUN=${DRY_RUN} BUILD_ONLY=${BUILD_ONLY} NO_START=${NO_START} PGO_PHASE=${PGO_PHASE}"
  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
    echo "NOTE: up -d --force-recreate runs only when all three flags above are 0." >&2
  fi
  build_default_stack_images

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
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    verify_default_stack_running || exit 1
  fi
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
