#!/usr/bin/env bash
# Static regression for scripts/rebuild_full_site.sh (no compose required).
set -euo pipefail

: "${USER:?USER must identify the static-test account}"
: "${TMPDIR:=/data/user/${USER}/tmp}"
[[ "${TMPDIR}" == /data/* ]] || {
  echo "static rebuild tests require TMPDIR under /data" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_SITE_SCRIPT="${SCRIPT_DIR}/rebuild_full_site.sh"
MEMORY_LIB="${SCRIPT_DIR}/lib/compose_pipeline_memory_high.sh"
RUNTIME_ADAPTER="${SCRIPT_DIR}/lib/podman_runtime.sh"
PGO_LIB="${SCRIPT_DIR}/pgo_lib.sh"

for path in "${FULL_SITE_SCRIPT}" "${MEMORY_LIB}" "${RUNTIME_ADAPTER}" "${PGO_LIB}"; do
  if [[ ! -f "${path}" ]]; then
    echo "missing ${path}" >&2
    exit 1
  fi
done

if ! bash -n "${FULL_SITE_SCRIPT}"; then
  echo "bash -n failed for rebuild_full_site.sh" >&2
  exit 1
fi
if ! bash -n "${MEMORY_LIB}"; then
  echo "bash -n failed for compose_pipeline_memory_high.sh" >&2
  exit 1
fi
if ! bash -n "${PGO_LIB}"; then
  echo "bash -n failed for pgo_lib.sh" >&2
  exit 1
fi

if ! grep -q 'podman_runtime.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must source podman_runtime.sh" >&2
  exit 1
fi
if ! grep -q 'compose_pipeline_memory_high.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must source compose_pipeline_memory_high.sh" >&2
  exit 1
fi
if ! grep -q 'pgo_lib.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must source pgo_lib.sh" >&2
  exit 1
fi
if ! grep -q 'podman_runtime_require' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must call podman_runtime_require" >&2
  exit 1
fi
if ! grep -q 'apply_pipeline_memory_high' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must call apply_pipeline_memory_high" >&2
  exit 1
fi

if grep -q 'build_musl_gcc_toolchain_image' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not build musl GCC toolchain image" >&2
  exit 1
fi
if ! grep -q 'pgo_ensure_layout.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must run pgo_ensure_layout.sh" >&2
  exit 1
fi
if grep -q 'run_cmd "${SCRIPT_DIR}/pgo_ensure_layout.sh"' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must run pgo_ensure_layout.sh outside run_cmd (dry-run must still mkdir PGOROOT)" >&2
  exit 1
fi
if ! grep -q 'pgo_ensure_pg_root' "${PGO_LIB}"; then
  echo "pgo_lib.sh must define pgo_ensure_pg_root for host PGOROOT bootstrap" >&2
  exit 1
fi
if ! grep -q 'pgo_chmod_shared_tree' "${PGO_LIB}"; then
  echo "pgo_lib.sh must define pgo_chmod_shared_tree for shared PGOROOT permissions" >&2
  exit 1
fi
if ! grep -q 'pgo_chmod_shared_tree' "${SCRIPT_DIR}/pgo_ensure_layout.sh"; then
  echo "pgo_ensure_layout.sh must call pgo_chmod_shared_tree" >&2
  exit 1
fi
if ! grep -q 'pgo_podman_build_context_args' "${PGO_LIB}"; then
  echo "pgo_lib.sh must define pgo_podman_build_context_args for Podman PGO mounts" >&2
  exit 1
fi
if ! grep -q 'compose_build_proxy_image' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must podman build proxy with --build-context=pgo=PGOROOT" >&2
  exit 1
fi
if ! grep -q 'compose_build_web_image' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must podman build web with --build-context=pgo=PGOROOT" >&2
  exit 1
fi
if ! grep -q 'pgo_podman_build_context_args' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must call pgo_podman_build_context_args for proxy/db podman build" >&2
  exit 1
fi
if ! grep -q '\-\-profile-phase' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must support --profile-phase" >&2
  exit 1
fi
if ! grep -q 'pgo_die' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must fail loud on PGO errors (pgo_die)" >&2
  exit 1
fi
if ! grep -q 'COMPOSE_IMAGE_BUILD_SERVICES=(web proxy)' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must list compose build services (web proxy only)" >&2
  exit 1
fi
if grep -q 'build "${DEFAULT_BUILD_SERVICES' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not batch all stack services in one compose build" >&2
  exit 1
fi
if ! grep -q 'for svc in "${COMPOSE_IMAGE_BUILD_SERVICES' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must compose build web and proxy serially" >&2
  exit 1
fi
if ! grep -q 'LAST_STEP=' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must track LAST_STEP for build failures" >&2
  exit 1
fi
if grep -qE '"\$\{PODMAN_COMPOSE\[@\]\}" up -d --build|"$\{PODMAN_COMPOSE\[@\]\}" up --build' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not pass --build to compose up" >&2
  exit 1
fi
if ! grep -qE '"\$\{PODMAN_COMPOSE\[@\]\}" up -d --force-recreate' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must use up -d --force-recreate for default stack" >&2
  exit 1
fi
if ! grep -q 'PG18_PROFILE=pg18-migrate' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must set pg18-migrate profile" >&2
  exit 1
fi
if ! grep -q 'compose_build_db_pg18' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must build db_pg18 via compose_build_db_pg18" >&2
  exit 1
fi
if ! grep -q 'compose_build_db_pg18' "${FULL_SITE_SCRIPT}" \
  || ! grep -q 'hpcperfstats-db' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must build db_pg18 image (hpcperfstats-db)" >&2
  exit 1
fi
if ! grep -qF -- '--profile "${PG18_PROFILE}" up -d --force-recreate "${PG18_SERVICE}"' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must up db_pg18 with profile and --force-recreate" >&2
  exit 1
fi

build_line="$(grep -n 'build_default_stack_images' "${FULL_SITE_SCRIPT}" | head -n 1 | cut -d: -f1)"
up_line="$(grep -n 'up_default_stack' "${FULL_SITE_SCRIPT}" | head -n 1 | cut -d: -f1)"
if [[ -z "${build_line}" || -z "${up_line}" ]]; then
  echo "rebuild_full_site.sh must define build and up helpers" >&2
  exit 1
fi
main_build="$(awk '/^main\(\)/ {m=1} m && /build_default_stack_images/ {print NR; exit}' "${FULL_SITE_SCRIPT}")"
main_up="$(awk '/^main\(\)/ {m=1} m && /up_default_stack/ {print NR; exit}' "${FULL_SITE_SCRIPT}")"
if [[ -z "${main_build}" || -z "${main_up}" ]]; then
  echo "rebuild_full_site.sh main must call default stack build and up helpers" >&2
  exit 1
fi

up_default_body="$(awk '/^up_default_stack\(\)/ {u=1; next} u && /^}/ {exit} u' "${FULL_SITE_SCRIPT}")"
up_db_body="$(awk '/^up_db_pg18\(\)/ {u=1; next} u && /^}/ {exit} u' "${FULL_SITE_SCRIPT}")"
if grep -q 'podman build' <<<"${up_default_body}${up_db_body}"; then
  echo "rebuild_full_site.sh must not podman build inside up_* helpers" >&2
  exit 1
fi
if ! grep -q 'verify_default_stack_running' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must verify stack running after up" >&2
  exit 1
fi
if ! grep -qF 'starting podman-compose up' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must log before compose up" >&2
  exit 1
fi
if ! grep -q 'print_stack_summary' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must print stack summary (containers + network) after up" >&2
  exit 1
fi
if ! grep -q 'HPCPERFSTATS_NETWORK_NAME' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh stack summary must include compose network id" >&2
  exit 1
fi
if ! grep -q 'UP_COMPLETED=1' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must set UP_COMPLETED after successful up" >&2
  exit 1
fi

# shellcheck source=lib/compose_pipeline_memory_high.sh
source "${MEMORY_LIB}"
high="$(compute_pipeline_memory_high_bytes 192000000000)"
want=$((192000000000 * 2 / 3))
if [[ "${high}" != "${want}" ]]; then
  echo "compute_pipeline_memory_high_bytes mismatch: got ${high}, want ${want}" >&2
  exit 1
fi

echo "test_rebuild_full_site.sh: all checks passed"
