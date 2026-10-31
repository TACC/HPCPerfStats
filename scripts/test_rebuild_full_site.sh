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
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FULL_SITE_SCRIPT="${SCRIPT_DIR}/rebuild_full_site.sh"
MEMORY_LIB="${SCRIPT_DIR}/lib/compose_pipeline_memory_high.sh"
RUNTIME_ADAPTER="${SCRIPT_DIR}/lib/podman_runtime.sh"
HELPERS="${SCRIPT_DIR}/lib/compose_frontend_helpers.sh"
BAKE_FLAGS="${REPO_ROOT}/services-conf/bake_clang_flags.sh"

for path in "${FULL_SITE_SCRIPT}" "${MEMORY_LIB}" "${RUNTIME_ADAPTER}" "${HELPERS}" "${BAKE_FLAGS}"; do
  if [[ ! -f "${path}" ]]; then
    echo "missing ${path}" >&2
    exit 1
  fi
done

if [[ -f "${SCRIPT_DIR}/pgo_lib.sh" ]]; then
  echo "pgo_lib.sh must be deleted (custom PGO removed)" >&2
  exit 1
fi
if [[ -f "${REPO_ROOT}/services-conf/pgo_clang_flags.sh" ]]; then
  echo "pgo_clang_flags.sh must be renamed/deleted (expect bake_clang_flags.sh)" >&2
  exit 1
fi

if ! bash -n "${FULL_SITE_SCRIPT}"; then
  echo "bash -n failed for rebuild_full_site.sh" >&2
  exit 1
fi
if ! bash -n "${MEMORY_LIB}"; then
  echo "bash -n failed for compose_pipeline_memory_high.sh" >&2
  exit 1
fi
if ! bash -n "${BAKE_FLAGS}"; then
  echo "bash -n failed for bake_clang_flags.sh" >&2
  exit 1
fi
BIND_GUARD="${SCRIPT_DIR}/lib/compose_bind_volume_guard.sh"
if ! bash -n "${BIND_GUARD}"; then
  echo "bash -n failed for compose_bind_volume_guard.sh" >&2
  exit 1
fi
if ! grep -q 'source "${SCRIPT_DIR}/lib/compose_bind_volume_guard.sh"' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must source compose_bind_volume_guard.sh" >&2
  exit 1
fi
if ! grep -q 'compose_bind_volumes_preflight' "${FULL_SITE_SCRIPT}" \
  || ! grep -q 'compose_bind_volumes_verify_mounted' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must run the bind volume guard before and after up" >&2
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
if grep -q 'pgo_lib.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not source pgo_lib.sh" >&2
  exit 1
fi
if ! grep -q 'podman_runtime_require' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must call podman_runtime_require" >&2
  exit 1
fi
if grep -E '^[[:space:]]*apply_pipeline_memory_high([[:space:]]|\|)' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must keep apply_pipeline_memory_high commented out" >&2
  exit 1
fi
if ! grep -q '^[[:space:]]*#[[:space:]]*apply_pipeline_memory_high' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must retain a commented apply_pipeline_memory_high call" >&2
  exit 1
fi
if ! grep -q 'do not source' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must refuse being sourced (exit kills login shell)" >&2
  exit 1
fi
if ! grep -q '^read_pipeline_memory_max_bytes()' "${MEMORY_LIB}"; then
  echo "compose_pipeline_memory_high.sh must define read_pipeline_memory_max_bytes" >&2
  exit 1
fi
if grep -q 'build_musl_gcc_toolchain_image' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not build musl GCC toolchain image" >&2
  exit 1
fi
if ! grep -q 'custom Clang host profiling was removed' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must refuse legacy profile-phase / pgo flags" >&2
  exit 1
fi
if grep -qE 'PGO_PHASE|PGOROOT|pgo_profiles|LLVM_PROFILE_FILE' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not reference custom profile env/tokens" >&2
  exit 1
fi
if ! grep -q 'compose_ensure_local_stack_image_tags' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must call compose_ensure_local_stack_image_tags" >&2
  exit 1
fi
if ! grep -q '^compose_ensure_local_stack_image_tags()' "${HELPERS}"; then
  echo "compose_frontend_helpers.sh must define compose_ensure_local_stack_image_tags" >&2
  exit 1
fi
if grep -A8 'compose_ensure_local_stack_image_tags()' "${HELPERS}" | grep -q 'IFS= read -r canonical alias'; then
  echo "compose_ensure_local_stack_image_tags: IFS= read with two vars merges canonical+alias (use read -r canonical alias)" >&2
  exit 1
fi
if grep -qE '"\$\{PODMAN_COMPOSE\[@\]\}" up -d --build|"\$\{PODMAN_COMPOSE\[@\]\}" up --build' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not use compose up --build" >&2
  exit 1
fi
if grep -qE '"\$\{PODMAN_COMPOSE\[@\]\}" up -d --force-recreate' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must not use --force-recreate on up" >&2
  exit 1
fi
if ! grep -q 'up -d --no-build' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must up -d --no-build after podman build" >&2
  exit 1
fi
if ! grep -q 'hpcperfstats_cpython_make_install' "${BAKE_FLAGS}"; then
  echo "bake_clang_flags.sh must define hpcperfstats_cpython_make_install" >&2
  exit 1
fi
if ! grep -q 'enable-optimizations' "${BAKE_FLAGS}"; then
  echo "bake_clang_flags.sh must keep --enable-optimizations helper" >&2
  exit 1
fi
if grep -qE 'fprofile-instr|PGO_PHASE|profile-opt|profile-gen-stamp' "${BAKE_FLAGS}"; then
  echo "bake_clang_flags.sh must not contain custom Clang PGO tokens" >&2
  exit 1
fi

echo "test_rebuild_full_site.sh: All checks passed"
