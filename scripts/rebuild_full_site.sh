#!/usr/bin/env bash
# Full-stack image rebuild: podman build web, proxy, db_pg18; compose down + up -d
# --no-build. Optional live-soak PGO via --profile-phase. Sets pipeline cgroup
# memory.high after pipeline is up. Never use podman-compose up --build here.
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
PGO_USE=0
PGO_SKIP=0

export PGOROOT="${PGOROOT:-${HPCPERFSTATS_PGO_ROOT:-/root/.hpcperfstats_pgo}}"

PODMAN_BUILD_STACK_IMAGES=(web proxy)
PG18_PROFILE=pg18-migrate
PG18_SERVICE=db_pg18
PGO_PHASE=stdlib

usage() {
  cat <<'EOF'
Usage: scripts/rebuild_full_site.sh [options]

Build default-stack images and hpcperfstats-db (db_pg18 profile), then tear down
the compose project (graceful stop, preserve volumes) and start:

  podman-compose down -t <timeout> --remove-orphans
  podman-compose up -d --no-build
  podman-compose --profile pg18-migrate up -d --no-build db_pg18

Default down stop timeout: HPCPERFSTATS_COMPOSE_DOWN_TIMEOUT=30 (seconds).

PGO (optional): default rebuild is always PGO_PHASE=stdlib — no PGOROOT access, no Clang
PGOROOT PGO (CPython --enable-optimizations + plain make install). PGOROOT defaults to
/root/.hpcperfstats_pgo only when a PGO flag is used.

  --profile-phase  Wipe PGOROOT (confirm if complete set) and build PGO_PHASE=generate for live soak
  --pgo-use        After soak: merge profiles + one-time PGO_PHASE=use rebuild (--no-cache on images)
  --pgo-skip       Rebuild with PGO_PHASE=skip (merged profdata under PGOROOT; podman cache allowed)

Only one of --profile-phase, --pgo-use, --pgo-skip may be passed.

Options:
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
    --pgo-use)
      PGO_USE=1
      shift
      ;;
    --pgo-skip)
      PGO_SKIP=1
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

pgo_validate_exclusive_cli_flags() {
  local n=0
  [[ "${PROFILE_PHASE}" -eq 1 ]] && n=$((n + 1))
  [[ "${PGO_USE}" -eq 1 ]] && n=$((n + 1))
  [[ "${PGO_SKIP}" -eq 1 ]] && n=$((n + 1))
  if [[ "${n}" -gt 1 ]]; then
    echo "rebuild_full_site.sh: only one of --profile-phase, --pgo-use, --pgo-skip allowed" >&2
    usage >&2
    exit 2
  fi
}

pgo_validate_exclusive_cli_flags

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
    echo "PGO: --profile-phase → PGO_PHASE=generate" >&2
    return 0
  fi
  if [[ "${PGO_USE}" -eq 1 ]]; then
    PGO_PHASE=use
    echo "PGO: --pgo-use → PGO_PHASE=use (merge + profile-guided rebuild)" >&2
    return 0
  fi
  if [[ "${PGO_SKIP}" -eq 1 ]]; then
    PGO_PHASE=skip
    echo "PGO: --pgo-skip → PGO_PHASE=skip (merged profdata; build cache allowed)" >&2
    return 0
  fi
  PGO_PHASE=stdlib
  echo "PGO: default stdlib — no PGOROOT; no Clang PGO (optional: --profile-phase, --pgo-use, --pgo-skip)" >&2
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
    run_stack_up_and_verify || pgo_die "stack verify failed after PGO use up"
  fi
  echo "PGO: use rebuild complete" >&2
}

run_stack_up_and_verify() {
  PHASE=up
  up_default_stack
  up_db_pg18
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    verify_default_stack_running || return 1
  fi
  UP_COMPLETED=1
}

compose_build_web_image() {
  local pgo_ctx git_commit
  local -a cache_args=()
  LAST_STEP="podman build web (PGO_PHASE=${PGO_PHASE} PGOROOT=${PGOROOT})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  git_commit="${HPCPERFSTATS_GIT_COMMIT:-unknown}"
  if [[ "${git_commit}" == "unknown" ]] \
    && command -v git >/dev/null 2>&1 \
    && git -C "${REPO_ROOT}" rev-parse HEAD >/dev/null 2>&1; then
    git_commit="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
  fi
  mapfile -t pgo_ctx < <(pgo_podman_build_context_args "${REPO_ROOT}")
  mapfile -t cache_args < <(pgo_image_build_cache_args)
  run_cmd "${PODMAN[@]}" build \
    "${cache_args[@]}" \
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
  local -a cache_args=()
  LAST_STEP="podman build proxy (PGO_PHASE=${PGO_PHASE} PGOROOT=${PGOROOT})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  mapfile -t pgo_ctx < <(pgo_podman_build_context_args "${REPO_ROOT}")
  mapfile -t cache_args < <(pgo_image_build_cache_args)
  run_cmd "${PODMAN[@]}" build \
    "${cache_args[@]}" \
    -f "${REPO_ROOT}/services-conf/proxy.Dockerfile" \
    -t hpcperfstats-proxy \
    --build-arg "PGO_PHASE=${PGO_PHASE}" \
    --build-arg "PGO_ROOT=/root/.hpcperfstats_pgo" \
    "${pgo_ctx[@]}" \
    "${REPO_ROOT}"
}

compose_build_db_pg18() {
  local pgo_ctx
  local -a cache_args=()
  LAST_STEP="podman build ${PG18_SERVICE} (PGO_PHASE=${PGO_PHASE} PGOROOT=${PGOROOT})"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  mapfile -t pgo_ctx < <(pgo_podman_build_context_args "${REPO_ROOT}")
  mapfile -t cache_args < <(pgo_image_build_cache_args)
  run_cmd "${PODMAN[@]}" build \
    "${cache_args[@]}" \
    -f "${REPO_ROOT}/services-conf/db.Dockerfile" \
    -t hpcperfstats-db \
    --build-arg "PGO_PHASE=${PGO_PHASE}" \
    --build-arg "PGO_ROOT=/root/.hpcperfstats_pgo" \
    "${pgo_ctx[@]}" \
    "${REPO_ROOT}/services-conf"
}

compose_build_all_with_pgo() {
  compose_build_web_image || return 1
  compose_build_proxy_image || return 1
  compose_build_db_pg18 || return 1
  compose_ensure_local_stack_image_tags
}

compose_down_project() {
  local timeout="${HPCPERFSTATS_COMPOSE_DOWN_TIMEOUT:-30}"
  LAST_STEP="podman-compose down -t ${timeout} --remove-orphans"
  echo "rebuild_full_site.sh: ${LAST_STEP} (volumes preserved; no -v) ..."
  run_cmd "${PODMAN_COMPOSE[@]}" down -t "${timeout}" --remove-orphans
}

up_default_stack() {
  compose_down_project
  LAST_STEP="podman-compose up -d --no-build (default stack)"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN_COMPOSE[@]}" up -d --no-build
}

up_db_pg18() {
  LAST_STEP="podman-compose --profile ${PG18_PROFILE} up -d --no-build ${PG18_SERVICE}"
  echo "rebuild_full_site.sh: ${LAST_STEP} ..."
  run_cmd "${PODMAN_COMPOSE[@]}" --profile "${PG18_PROFILE}" up -d --no-build "${PG18_SERVICE}"
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
  # Compose pgo_profiles bind needs the host path to exist (empty dir is enough).
  if [[ "${PGO_PHASE}" != stdlib ]]; then
    pgo_ensure_pg_root
  fi
  echo "=== rebuild_full_site.sh: starting podman-compose down + up (no --build) ==="
  run_stack_up_and_verify || exit 1

  PHASE=memory_high
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    apply_pipeline_memory_high || exit 1
  fi

  PHASE=summary
  print_stack_summary
}

main() {
  trap on_exit EXIT
  preflight
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
  resolve_pgo_phase
  if [[ "${PGO_PHASE}" != stdlib ]]; then
    pgo_die_if_partial_profile_collection "${REPO_ROOT}" "${PROFILE_PHASE}"
    pgo_prepare_host_pgroot_for_rebuild "${REPO_ROOT}" "${SCRIPT_DIR}/pgo_ensure_layout.sh"
  fi

  if [[ "${PGO_USE}" -eq 1 ]]; then
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

  PHASE=build
  echo "rebuild_full_site.sh: project=${HPCPERFSTATS_COMPOSE_PROJECT} DRY_RUN=${DRY_RUN} BUILD_ONLY=${BUILD_ONLY} NO_START=${NO_START} PGO_PHASE=${PGO_PHASE}"
  if [[ "${BUILD_ONLY}" -eq 1 || "${NO_START}" -eq 1 || "${DRY_RUN}" -eq 1 ]]; then
    echo "NOTE: compose down + up runs only when all three flags above are 0." >&2
  fi
  compose_build_all_with_pgo

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
  echo "Full-site rebuild complete (build + up + memory.high)."
}

main "$@"
