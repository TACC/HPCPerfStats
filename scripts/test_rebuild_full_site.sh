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

for path in "${FULL_SITE_SCRIPT}" "${MEMORY_LIB}" "${RUNTIME_ADAPTER}"; do
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

if ! grep -q 'podman_runtime.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must source podman_runtime.sh" >&2
  exit 1
fi
if ! grep -q 'compose_pipeline_memory_high.sh' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must source compose_pipeline_memory_high.sh" >&2
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

if ! grep -q 'DEFAULT_BUILD_SERVICES=(web pipeline redis proxy db rabbitmq)' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must list default stack build services" >&2
  exit 1
fi
if ! grep -q 'build "${DEFAULT_BUILD_SERVICES' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must compose build default stack services" >&2
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
if ! grep -qF -- '--profile "${PG18_PROFILE}" build "${PG18_SERVICE}"' "${FULL_SITE_SCRIPT}"; then
  echo "rebuild_full_site.sh must build db_pg18 with profile" >&2
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
if [[ -z "${main_build}" || -z "${main_up}" || "${main_build}" -ge "${main_up}" ]]; then
  echo "rebuild_full_site.sh main must build before up" >&2
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
