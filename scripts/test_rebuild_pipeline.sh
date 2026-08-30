#!/usr/bin/env bash
# Static regression for scripts/rebuild_pipeline.sh (no compose required).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_SCRIPT="${SCRIPT_DIR}/rebuild_pipeline.sh"
HELPERS="${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"
FRONTEND_SCRIPT="${SCRIPT_DIR}/rebuild_frontend.sh"

if [[ ! -f "${PIPELINE_SCRIPT}" ]]; then
  echo "missing ${PIPELINE_SCRIPT}" >&2
  exit 1
fi

if [[ ! -f "${HELPERS}" ]]; then
  echo "missing ${HELPERS}" >&2
  exit 1
fi

if ! bash -n "${PIPELINE_SCRIPT}"; then
  echo "bash -n failed for rebuild_pipeline.sh" >&2
  exit 1
fi

if ! bash -n "${HELPERS}"; then
  echo "bash -n failed for compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! grep -q 'compose_frontend_helpers.sh' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must source compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! grep -q 'hpcperfstats-pipeline-refresh' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must use hpcperfstats-pipeline-refresh build target" >&2
  exit 1
fi

if ! grep -q 'build_web_image_with_target' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must build via build_web_image_with_target" >&2
  exit 1
fi

if ! grep -q 'build_web_image_with_target' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define build_web_image_with_target" >&2
  exit 1
fi

if ! grep -q 'compose_backend_is_podman' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must detect podman-compose for build --target fallback" >&2
  exit 1
fi

if ! grep -qE 'podman build|docker build' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must fall back to podman/docker build --target" >&2
  exit 1
fi

if ! grep -q 'resolve_hpcperfstats_git_commit' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define resolve_hpcperfstats_git_commit" >&2
  exit 1
fi

if ! grep -q -- '--build-arg.*HPCPERFSTATS_GIT_COMMIT' "${HELPERS}"; then
  echo "build_web_image_with_target must pass --build-arg HPCPERFSTATS_GIT_COMMIT" >&2
  exit 1
fi

if ! grep -q 'docker compose stop -t.* pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must stop pipeline with grace timeout" >&2
  exit 1
fi

if ! grep -q 'docker compose stop -t.* web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must stop web with grace timeout" >&2
  exit 1
fi

pipeline_line="$(grep -n 'docker compose stop.*pipeline' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
web_line="$(grep -n 'docker compose stop -t.* web' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
if [[ -z "${pipeline_line}" || -z "${web_line}" || "${pipeline_line}" -ge "${web_line}" ]]; then
  echo "rebuild_pipeline.sh must stop pipeline before web (pipeline line ${pipeline_line:-?}, web line ${web_line:-?})" >&2
  exit 1
fi

if ! grep -q 'compose_recreate_web_after_image_refresh' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must recreate web via compose_recreate_web_after_image_refresh" >&2
  exit 1
fi

if ! grep -q 'compose_recreate_pipeline_after_image_refresh' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must recreate pipeline via compose_recreate_pipeline_after_image_refresh" >&2
  exit 1
fi

if ! grep -q 'compose_restore_proxy_if_was_running' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must restore proxy after web recreate when needed" >&2
  exit 1
fi

if ! grep -q 'compose_recreate_web_after_image_refresh' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define compose_recreate_web_after_image_refresh" >&2
  exit 1
fi

if ! grep -q 'force-recreate --no-deps web' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must force-recreate web on non-podman backends" >&2
  exit 1
fi

if ! grep -q 'compose_podman_rm_service_containers' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define compose_podman_rm_service_containers (podman-compose has no rm)" >&2
  exit 1
fi

if grep -qE 'docker compose rm -sf|compose rm -sf' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must not call docker compose rm (invalid on podman-compose)" >&2
  exit 1
fi

if ! grep -q 'compose_podman_rm_service_containers pipeline web' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must podman-rm pipeline+web before web up" >&2
  exit 1
fi

if ! grep -q 'podman rm -f' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must use podman rm -f for stale containers" >&2
  exit 1
fi

web_recreate_line="$(
  awk '
    /^[a-zA-Z_][a-zA-Z0-9_]*\(\)/ { in_fn = ($0 ~ /^start_app_containers\(\)/) }
    in_fn && /compose_recreate_web_after_image_refresh/ { print NR; exit }
  ' "${PIPELINE_SCRIPT}"
)"
pipe_recreate_line="$(
  awk '
    /^[a-zA-Z_][a-zA-Z0-9_]*\(\)/ { in_fn = ($0 ~ /^start_app_containers\(\)/) }
    in_fn && /compose_recreate_pipeline_after_image_refresh/ { print NR; exit }
  ' "${PIPELINE_SCRIPT}"
)"
if [[ -z "${web_recreate_line}" || -z "${pipe_recreate_line}" || "${web_recreate_line}" -ge "${pipe_recreate_line}" ]]; then
  echo "rebuild_pipeline.sh start_app_containers must recreate web before pipeline (web line ${web_recreate_line:-?}, pipeline line ${pipe_recreate_line:-?})" >&2
  exit 1
fi

no_web_pipe_line="$(
  awk '
    /^[a-zA-Z_][a-zA-Z0-9_]*\(\)/ { in_fn = ($0 ~ /^start_pipeline_only\(\)/) }
    in_fn && /compose_recreate_pipeline_after_image_refresh/ { print NR; exit }
  ' "${PIPELINE_SCRIPT}"
)"
if [[ -z "${no_web_pipe_line}" ]]; then
  echo "rebuild_pipeline.sh start_pipeline_only must recreate pipeline" >&2
  exit 1
fi
if awk '
  /^[a-zA-Z_][a-zA-Z0-9_]*\(\)/ { in_fn = ($0 ~ /^start_pipeline_only\(\)/) }
  in_fn && /compose_recreate_web_after_image_refresh/ { found=1 }
  END { exit found ? 0 : 1 }
' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh start_pipeline_only must not recreate web" >&2
  exit 1
fi

if grep -q 'docker compose up -d web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not use bare compose up -d web (use recreate helper)" >&2
  exit 1
fi

if grep -q 'docker compose up -d pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not use bare compose up -d pipeline (use recreate helper)" >&2
  exit 1
fi

if grep -Eiq '\bnpm ci\b|\bnpm run build\b' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not invoke npm ci or npm run build" >&2
  exit 1
fi

if grep -q 'docker compose build.*proxy' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not rebuild proxy service" >&2
  exit 1
fi

if ! grep -q -- '--no-web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must support --no-web for pipeline-only rebuild without running web" >&2
  exit 1
fi

if ! grep -q 'preserve_frontend_without_web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must define preserve_frontend_without_web for --no-web" >&2
  exit 1
fi

if ! grep -q 'start_pipeline_only' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must define start_pipeline_only for --no-web" >&2
  exit 1
fi

if ! grep -q 'warn_no_web_temporary' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must warn that --no-web requires a proper rebuild before full stack/web" >&2
  exit 1
fi

if ! grep -q 'Before bringing web' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh --no-web warning must tell operators to rebuild properly before web/full stack" >&2
  exit 1
fi

if ! grep -q 'compose_frontend_helpers.sh' "${FRONTEND_SCRIPT}"; then
  echo "rebuild_frontend.sh must source compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! grep -q 'cleanup_pipeline_rebuild_scratch' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define cleanup_pipeline_rebuild_scratch" >&2
  exit 1
fi

if ! grep -q 'cleanup_pipeline_rebuild_scratch' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must call cleanup_pipeline_rebuild_scratch on exit" >&2
  exit 1
fi

if ! grep -q 'trap cleanup EXIT' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must trap cleanup EXIT so scratch is removed after success or failure" >&2
  exit 1
fi

if grep -q 'rm -rf "${REPO_ROOT}/.build"' "${PIPELINE_SCRIPT}" \
  || grep -q 'rm -rf "${REPO_ROOT}/.build"' "${HELPERS}"; then
  echo "must not rm -rf entire .build (monitor/other sibling scratch must survive)" >&2
  exit 1
fi

# shellcheck source=lib/compose_frontend_helpers.sh
source "${HELPERS}"
if ! declare -F build_web_image_with_target >/dev/null; then
  echo "build_web_image_with_target must be defined in compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! declare -F compose_recreate_web_after_image_refresh >/dev/null; then
  echo "compose_recreate_web_after_image_refresh must be defined in compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! declare -F compose_recreate_pipeline_after_image_refresh >/dev/null; then
  echo "compose_recreate_pipeline_after_image_refresh must be defined in compose_frontend_helpers.sh" >&2
  exit 1
fi

if ! declare -F cleanup_pipeline_rebuild_scratch >/dev/null; then
  echo "cleanup_pipeline_rebuild_scratch must be defined in compose_frontend_helpers.sh" >&2
  exit 1
fi

scratch_root="$(mktemp -d /tmp/hps-test-pipeline-rebuild-scratch.XXXXXX)"
scratch_cleanup() { rm -rf "${scratch_root}"; }
trap scratch_cleanup EXIT

mkdir -p "${scratch_root}/keep/.build/pipeline-rebuild-frontend/machine"
echo "spa" >"${scratch_root}/keep/.build/pipeline-rebuild-frontend/machine/index.html"
echo "monitor-prefix" >"${scratch_root}/keep/.build/keep-me"
touch "${scratch_root}/keep/backup.tar"
restore_dir="${scratch_root}/keep/restore-parent/hps-pipeline-frontend-restore.abc123"
mkdir -p "${restore_dir}"
echo "restore" >"${restore_dir}/file"

cleanup_pipeline_rebuild_scratch \
  "${scratch_root}/keep/.build/pipeline-rebuild-frontend" \
  "${scratch_root}/keep/backup.tar" \
  "${restore_dir}"

if [[ -e "${scratch_root}/keep/.build/pipeline-rebuild-frontend" ]]; then
  echo "cleanup must remove .build/pipeline-rebuild-frontend" >&2
  exit 1
fi
if [[ ! -f "${scratch_root}/keep/.build/keep-me" ]]; then
  echo "cleanup must keep sibling .build contents (e.g. monitor prefix)" >&2
  exit 1
fi
if [[ -e "${scratch_root}/keep/backup.tar" ]]; then
  echo "cleanup must remove the frontend backup tar" >&2
  exit 1
fi
if [[ -e "${restore_dir}" ]]; then
  echo "cleanup must remove the frontend restore dir" >&2
  exit 1
fi

empty_root="${scratch_root}/empty-build"
mkdir -p "${empty_root}/.build/pipeline-rebuild-frontend/machine"
echo "spa" >"${empty_root}/.build/pipeline-rebuild-frontend/machine/index.html"
cleanup_pipeline_rebuild_scratch \
  "${empty_root}/.build/pipeline-rebuild-frontend" \
  "" \
  ""
if [[ -d "${empty_root}/.build" ]]; then
  echo "cleanup must rmdir .build when it is empty after removing staging" >&2
  exit 1
fi

refuse_root="${scratch_root}/refuse"
mkdir -p "${refuse_root}/not-staging"
echo "keep" >"${refuse_root}/not-staging/file"
cleanup_pipeline_rebuild_scratch "${refuse_root}/not-staging" "" "" >/dev/null 2>&1 || true
if [[ ! -f "${refuse_root}/not-staging/file" ]]; then
  echo "cleanup must refuse to delete an unexpected preserve path" >&2
  exit 1
fi

echo "test_rebuild_pipeline.sh: all checks passed"
