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

if ! grep -q 'stop_and_remove_web_pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must define stop_and_remove_web_pipeline" >&2
  exit 1
fi

if ! grep -q 'start_web_proxy_pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must define start_web_proxy_pipeline" >&2
  exit 1
fi

# Bring-up contract: one command, no --no-deps.
if ! grep -qE 'docker compose up -d web proxy pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must run: docker compose up -d web proxy pipeline" >&2
  exit 1
fi

if grep -q 'up -d web proxy pipeline' "${PIPELINE_SCRIPT}" \
  && grep -qE 'up -d web proxy pipeline.*--no-deps|--no-deps.*up -d web proxy pipeline' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not pass --no-deps on up -d web proxy pipeline" >&2
  exit 1
fi

# Must take proxy down before web recreate.
if ! awk '
  /^stop_and_remove_web_pipeline\(\)/ { in_fn=1; next }
  /^[a-zA-Z_][a-zA-Z0-9_]*\(\)/ { if (in_fn) exit 1 }
  in_fn && /compose_podman_rm_service_containers proxy/ { found=1 }
  END { exit found ? 0 : 1 }
' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must take proxy down before replacing web" >&2
  exit 1
fi

# Never stop/rm db, redis, rabbitmq.
if grep -Eiq 'docker compose stop.*(redis|rabbitmq|db_pg18)|compose_podman_rm_service_containers.*(redis|rabbitmq)' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not stop/rm db, redis, or rabbitmq" >&2
  exit 1
fi

# Must not rebuild the proxy image.
if grep -qE 'compose build[[:space:]].*proxy|proxy\.Dockerfile|podman build.*proxy' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not rebuild the proxy image" >&2
  exit 1
fi

# Build must happen before stop (stack stays up during image build).
build_call="$(awk '/^main\(\)/ {m=1} m && /build_pipeline_image/ {print NR; exit}' "${PIPELINE_SCRIPT}")"
stop_call="$(awk '/^main\(\)/ {m=1} m && /stop_and_remove_web_pipeline/ {print NR; exit}' "${PIPELINE_SCRIPT}")"
if [[ -z "${build_call}" || -z "${stop_call}" || "${build_call}" -ge "${stop_call}" ]]; then
  echo "rebuild_pipeline.sh main must build_pipeline_image before stop_and_remove_web_pipeline (build ${build_call:-?}, stop ${stop_call:-?})" >&2
  exit 1
fi

# Stop order: pipeline before web.
pipe_stop="$(grep -n 'stop -t.*pipeline' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
web_stop="$(grep -n 'stop -t.*web' "${PIPELINE_SCRIPT}" | head -n 1 | cut -d: -f1)"
if [[ -z "${pipe_stop}" || -z "${web_stop}" || "${pipe_stop}" -ge "${web_stop}" ]]; then
  echo "rebuild_pipeline.sh must stop pipeline before web" >&2
  exit 1
fi

if grep -Eiq '\bnpm ci\b|\bnpm run build\b' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must not invoke npm" >&2
  exit 1
fi

if ! grep -q 'trap cleanup EXIT' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must trap cleanup EXIT" >&2
  exit 1
fi

if ! grep -q 'cleanup_pipeline_rebuild_scratch' "${PIPELINE_SCRIPT}"; then
  echo "rebuild_pipeline.sh must call cleanup_pipeline_rebuild_scratch" >&2
  exit 1
fi

if ! grep -q 'compose_frontend_helpers.sh' "${FRONTEND_SCRIPT}"; then
  echo "rebuild_frontend.sh must source compose_frontend_helpers.sh" >&2
  exit 1
fi

# shellcheck source=lib/compose_frontend_helpers.sh
source "${HELPERS}"
if ! declare -F build_web_image_with_target >/dev/null; then
  echo "build_web_image_with_target must be defined" >&2
  exit 1
fi
if ! declare -F cleanup_pipeline_rebuild_scratch >/dev/null; then
  echo "cleanup_pipeline_rebuild_scratch must be defined" >&2
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
  echo "cleanup must keep sibling .build contents" >&2
  exit 1
fi

echo "test_rebuild_pipeline.sh: all checks passed"
