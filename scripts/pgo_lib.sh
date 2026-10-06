#!/usr/bin/env bash
# Shared PGO helpers for rebuild_full_site.sh (host-side).
set -euo pipefail

PGO_NAMESPACES_YAML="${PGO_NAMESPACES_YAML:-services-conf/pgo_namespaces.yaml}"

pgo_die() {
  echo "rebuild_full_site.sh: PGO FAILED: $*" >&2
  exit 1
}

pgo_root_dir() {
  printf '%s' "${PGOROOT:-${HPCPERFSTATS_PGO_ROOT:-/root/.hpcperfstats_pgo}}"
}

# Host PGOROOT must exist before compose bind mounts or Dockerfile RUN --mount=bind
# (device path in docker-compose.settings.yaml). Idempotent; safe every rebuild.
pgo_ensure_pg_root() {
  local root
  root="$(pgo_root_dir)"
  if [[ -e "${root}" && ! -d "${root}" ]]; then
    pgo_die "PGOROOT exists but is not a directory: ${root}"
  fi
  if ! mkdir -p "${root}"; then
    pgo_die "cannot mkdir PGOROOT=${root} (permission denied?). Set PGOROOT to a writable path, e.g. /data/user/\$USER/hpcperfstats-pgo, and match pgo_profiles.device in docker-compose.settings.yaml"
  fi
  if [[ ! -w "${root}" ]]; then
    pgo_die "PGOROOT not writable: ${root}"
  fi
}

pgo_use_breadcrumb_path() {
  printf '%s/breadcrumbs/pgo_use_full_rebuild.done' "$(pgo_root_dir)"
}

pgo_use_failed_path() {
  printf '%s/breadcrumbs/pgo_use_full_rebuild.failed' "$(pgo_root_dir)"
}

pgo_list_namespaces() {
  local repo_root="${1:?repo_root}"
  local yaml_path="${repo_root}/${PGO_NAMESPACES_YAML}"
  grep -E '^[[:space:]]+- namespace:' "${yaml_path}" | sed -E 's/^[[:space:]]+- namespace:[[:space:]]*//'
}

pgo_namespace_has_mergeable_raw() {
  local root ns
  root="$(pgo_root_dir)"
  ns="$1"
  shopt -s nullglob
  local files=("${root}/${ns}/raw/"*.profraw)
  [[ ${#files[@]} -gt 0 ]]
}

pgo_namespace_has_profdata() {
  local root ns
  root="$(pgo_root_dir)"
  ns="$1"
  [[ -s "${root}/${ns}/default.profdata" ]]
}

profiles_ready() {
  local repo_root="${1:?repo_root}"
  local ns
  while IFS= read -r ns; do
    [[ -n "${ns}" ]] || continue
    if pgo_namespace_has_profdata "${ns}"; then
      continue
    fi
    if pgo_namespace_has_mergeable_raw "${ns}"; then
      continue
    fi
    return 1
  done < <(pgo_list_namespaces "${repo_root}")
  return 0
}

pgo_print_summary() {
  local phase="$1"
  local crumb="absent"
  if [[ -f "$(pgo_use_breadcrumb_path)" ]]; then
    crumb="present"
  elif [[ -f "$(pgo_use_failed_path)" ]]; then
    crumb="failed"
  fi
  echo "PGO phase=${phase} breadcrumb=${crumb} PGOROOT=$(pgo_root_dir)" >&2
}
