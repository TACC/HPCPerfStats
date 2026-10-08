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
# (device path in docker-compose.yaml). Idempotent; safe every rebuild.
pgo_ensure_pg_root() {
  local root
  root="$(pgo_root_dir)"
  if [[ -e "${root}" && ! -d "${root}" ]]; then
    pgo_die "PGOROOT exists but is not a directory: ${root}"
  fi
  if ! mkdir -p "${root}"; then
    pgo_die "cannot mkdir PGOROOT=${root} (permission denied?). Set PGOROOT to a writable path, e.g. /data/user/\$USER/hpcperfstats-pgo, and match pgo_profiles.device in docker-compose.yaml"
  fi
  if [[ ! -w "${root}" ]]; then
    pgo_die "PGOROOT not writable: ${root}"
  fi
}

# World-writable dirs (sticky) + world-readable/writable files so rootless builders,
# container UIDs, and merge/rebuild on different host users share one PGOROOT bind.
pgo_chmod_shared_tree() {
  local root
  root="$(pgo_root_dir)"
  [[ -d "${root}" ]] || return 0
  # Only fix modes that are wrong — avoid rewriting the whole tree (Podman pgo context digest).
  if [[ "$(stat -c '%a' "${root}" 2>/dev/null || echo "")" != "1777" ]]; then
    chmod 1777 "${root}" || pgo_die "chmod 1777 failed: ${root}"
  fi
  find "${root}" -type d ! -perm 1777 -exec chmod 1777 {} +
  find "${root}" -type f ! -perm -666 -exec chmod a+rw {} +
}

pgo_sketch_podman_context_dir() {
  local repo_root="${1:?repo_root}"
  printf '%s' "${repo_root}/services-conf/pgo_sketch_podman_context"
}

# Local-only Podman pgo context (gitignored). Created once; never rewritten on rebuild.
pgo_ensure_sketch_podman_context_dir() {
  local repo_root="${1:?repo_root}"
  local stub
  stub="$(pgo_sketch_podman_context_dir "${repo_root}")"
  mkdir -p "${stub}"
  if [[ ! -f "${stub}/.stable" ]]; then
    printf '%s\n' 'sketch-only podman --build-context=pgo' >"${stub}/.stable"
  fi
}

# FHS empty dir (stable Podman --build-context=pgo digest); never written by rebuild.
pgo_stdlib_empty_build_context_dir() {
  local d="${HPCPERFSTATS_PGO_EMPTY_BUILD_CONTEXT:-/usr/share/empty}"
  if [[ ! -d "${d}" ]]; then
    pgo_die \
      "stdlib podman build requires an existing empty directory for --build-context=pgo (set HPCPERFSTATS_PGO_EMPTY_BUILD_CONTEXT): ${d}"
  fi
  printf '%s' "${d}"
}

pgo_use_sketch_podman_build_context() {
  local repo_root="${1:?repo_root}"
  pgo_tree_sketch_only "${repo_root}" \
    || return 1
  [[ "${PGO_PHASE:-}" == skip ]]
}

# Host PGOROOT tree for compose bind / generate / skip+profdata — not for stdlib builds.
pgo_prepare_host_pgroot_for_rebuild() {
  local repo_root="${1:?repo_root}"
  local ensure_layout="${2:?ensure_layout script path}"
  if [[ "${PGO_PHASE:-}" == stdlib ]]; then
    echo "PGO: stdlib — no host PGOROOT layout" >&2
    return 0
  fi
  "${ensure_layout}"
}

pgo_stderr_why_full_pgroot_context() {
  local repo_root="${1:?repo_root}"
  local ns root
  root="$(pgo_root_dir)"
  case "${PGO_PHASE:-}" in
    stdlib | skip) ;;
    *) return 0 ;;
  esac
  if pgo_tree_sketch_only "${repo_root}"; then
    return 0
  fi
  echo "pgo podman build-context: sketch unavailable — profile artifacts under ${root}:" >&2
  while IFS= read -r ns; do
    [[ -n "${ns}" ]] || continue
    if pgo_namespace_has_any_profile_artifact "${ns}" "${root}"; then
      echo "  - ${ns}" >&2
    fi
  done < <(pgo_list_namespaces "${repo_root}")
}

# Host PGOROOT for podman build --build-context=pgo=… (proxy/db PGO profraw mounts).
pgo_podman_build_context_args() {
  local repo_root="${1:?repo_root}"
  local root stub empty
  if [[ "${PGO_PHASE:-}" == stdlib ]]; then
    empty="$(pgo_stdlib_empty_build_context_dir)"
    echo "pgo podman build-context: stdlib empty ${empty} (no PGOROOT)" >&2
    printf '%s\n' "--build-context=pgo=${empty}"
    return 0
  fi
  root="$(cd "$(pgo_root_dir)" && pwd)"
  if pgo_use_sketch_podman_build_context "${repo_root}"; then
    pgo_ensure_sketch_podman_context_dir "${repo_root}"
    stub="$(pgo_sketch_podman_context_dir "${repo_root}")"
    echo "pgo podman build-context: local sketch ${stub} (PGO_PHASE=${PGO_PHASE})" >&2
    printf '%s\n' "--build-context=pgo=${stub}"
    return 0
  fi
  pgo_stderr_why_full_pgroot_context "${repo_root}"
  echo "pgo podman build-context: full PGOROOT ${root} (PGO_PHASE=${PGO_PHASE})" >&2
  printf '%s\n' "--build-context=pgo=${root}"
}

# PGO_PHASE=use must ignore stale layer cache (profile-opt flags); skip keeps cache.
pgo_image_build_cache_args() {
  if [[ "${PGO_PHASE:-}" == use ]]; then
    printf '%s\n' --no-cache
  fi
}

# After explicit podman build, compose up must not build/pull from a registry.
# Tag podman-compose fallback names ({project}_{service}) when include-merge drops image:.
compose_ensure_local_stack_image_tags() {
  local project canonical alias
  project="${HPCPERFSTATS_COMPOSE_PROJECT:-hpcperfstats}"
  while IFS= read -r canonical alias; do
    [[ -n "${canonical}" ]] || continue
    if ! podman image exists "${canonical}"; then
      pgo_die "stack image missing before compose up: ${canonical} (build images first)"
    fi
    if [[ "${canonical}" != "${alias}" ]] && ! podman image exists "${alias}"; then
      podman tag "${canonical}" "${alias}"
    fi
  done <<EOF
hpcperfstats:latest ${project}_web:latest
hpcperfstats:latest ${project}_pipeline:latest
hpcperfstats-proxy:latest ${project}_proxy:latest
hpcperfstats-db:latest ${project}_db_pg18:latest
EOF
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

pgo_namespace_has_nonempty_profdata() {
  local root ns
  root="$(pgo_root_dir)"
  ns="$1"
  [[ -s "${root}/${ns}/default.profdata" ]]
}

# Instrumentation profraw from PGO generate image builds (ignore zero-byte placeholders).
pgo_namespace_has_nonempty_instr_raw() {
  local root ns f
  root="$(pgo_root_dir)"
  ns="$1"
  shopt -s nullglob
  for f in "${root}/${ns}/raw/"*.profraw; do
    [[ -s "${f}" ]] && return 0
  done
  shopt -u nullglob
  return 1
}

# CPython use-phase needs live-soak profiles (not Makefile unittest output).
pgo_cpython_namespace_has_soak_raw() {
  local root ns f
  root="$(pgo_root_dir)"
  ns="$1"
  case "${ns}" in
    web/gil/cpython | web/ft/cpython) ;;
    *) return 1 ;;
  esac
  shopt -s nullglob
  for f in "${root}/${ns}/raw/"python-*.profraw "${root}/${ns}/raw/"code-*.profclangr; do
    [[ -s "${f}" ]] && return 0
  done
  for f in "${root}/${ns}/raw/"*.profclangr; do
    [[ -s "${f}" ]] && return 0
  done
  shopt -u nullglob
  return 1
}

pgo_namespace_profile_input_ready() {
  local ns="${1:?namespace}"
  if pgo_namespace_has_nonempty_profdata "${ns}"; then
    return 0
  fi
  case "${ns}" in
    web/gil/cpython | web/ft/cpython)
      pgo_cpython_namespace_has_soak_raw "${ns}"
      ;;
    *)
      pgo_namespace_has_nonempty_instr_raw "${ns}"
      ;;
  esac
}

# Any nonempty profile artifact under a namespace (ready or incomplete — blocks stdlib fallback).
pgo_namespace_has_any_profile_artifact() {
  local ns="${1:?namespace}"
  local root="${2:-$(pgo_root_dir)}"
  local f

  if [[ -s "${root}/${ns}/default.profdata" ]]; then
    return 0
  fi
  shopt -s nullglob
  for f in "${root}/${ns}/raw/"*.profraw "${root}/${ns}/raw/"*.profclangr; do
    [[ -s "${f}" ]] && return 0
  done
  shopt -u nullglob
  return 1
}

# True when every namespace can enter merge/use (merged profdata or soak-backed raw inputs).
profiles_ready() {
  local repo_root="${1:?repo_root}"
  local ns
  while IFS= read -r ns; do
    [[ -n "${ns}" ]] || continue
    if pgo_namespace_profile_input_ready "${ns}"; then
      continue
    fi
    return 1
  done < <(pgo_list_namespaces "${repo_root}")
  return 0
}

pgo_namespace_use_gap_reason() {
  local ns="${1:?namespace}"
  if pgo_namespace_profile_input_ready "${ns}"; then
    return 1
  fi
  case "${ns}" in
    web/gil/cpython | web/ft/cpython)
      printf '%s' \
        "${ns}: missing live soak (nonempty raw/python-*.profraw); check web/pipeline up, PGOROOT bind, LLVM_PROFILE_FILE"
      ;;
    *)
      printf '%s' \
        "${ns}: missing profiles (nonempty raw/*.profraw after --profile-phase generate, or default.profdata)"
      ;;
  esac
  return 0
}

# Stage-3-only PGOROOT (some raw/profdata, not all namespaces soak-ready): fail closed before compile.
pgo_die_if_partial_profile_collection() {
  local repo_root="${1:?repo_root}"
  local allow_profile_generate="${2:-0}"
  local ns
  local -a gaps=()

  if [[ "${allow_profile_generate}" -eq 1 ]]; then
    return 0
  fi
  if pgo_tree_sketch_only "${repo_root}" || profiles_ready "${repo_root}"; then
    return 0
  fi
  if ! pgo_tree_has_any_profile_data "${repo_root}"; then
    return 0
  fi

  while IFS= read -r ns; do
    [[ -n "${ns}" ]] || continue
    if pgo_namespace_profile_input_ready "${ns}"; then
      continue
    fi
    gaps+=( "$(pgo_namespace_use_gap_reason "${ns}")" )
  done < <(pgo_list_namespaces "${repo_root}")

  pgo_die \
    "PGO profile collection incomplete (soak or generate wiring not connected for every namespace); refusing compile. $(IFS='; '; echo "${gaps[*]}")"
}

pgo_tree_has_any_profile_data() {
  local repo_root="${1:?repo_root}" ns root
  root="$(pgo_root_dir)"
  while IFS= read -r ns; do
    [[ -n "${ns}" ]] || continue
    if pgo_namespace_has_any_profile_artifact "${ns}" "${root}"; then
      return 0
    fi
  done < <(pgo_list_namespaces "${repo_root}")
  return 1
}

# Stage 1 (missing PGOROOT) and stage 2 (layout sketch only): no profile inputs in any namespace.
pgo_tree_sketch_only() {
  local repo_root="${1:?repo_root}"
  if pgo_tree_has_any_profile_data "${repo_root}"; then
    return 1
  fi
  return 0
}

# --profile-phase: remove all prior PGO artifacts (raw, profdata, breadcrumbs) and start clean.
pgo_wipe_pgroot_for_profile_phase() {
  local root
  root="$(pgo_root_dir)"
  pgo_ensure_pg_root
  shopt -s nullglob
  local entries=("${root}"/*)
  shopt -u nullglob
  if [[ ${#entries[@]} -eq 0 ]]; then
    echo "pgo_wipe_pgroot_for_profile_phase: PGOROOT already empty under ${root}" >&2
    return 0
  fi
  rm -rf "${root:?}"/*
  echo "pgo_wipe_pgroot_for_profile_phase: removed all contents under ${root}" >&2
}

# Wipe for --profile-phase; prompt when every namespace already has profile inputs (profiles_ready).
pgo_confirm_wipe_pgroot_for_profile_phase() {
  local repo_root="${1:?repo_root}"
  if ! profiles_ready "${repo_root}"; then
    pgo_wipe_pgroot_for_profile_phase
    return 0
  fi
  if [[ "${HPC_PGO_PROFILE_PHASE_FORCE_WIPE:-0}" == 1 ]]; then
    echo "pgo: HPC_PGO_PROFILE_PHASE_FORCE_WIPE=1; wiping complete profile set under $(pgo_root_dir)" >&2
    pgo_wipe_pgroot_for_profile_phase
    return 0
  fi
  if [[ ! -t 0 ]] || [[ ! -t 1 ]]; then
    pgo_die \
      "PGOROOT has a complete profile set (all namespaces profiles_ready); refusing --profile-phase wipe without a TTY. Re-run interactively and confirm, or set HPC_PGO_PROFILE_PHASE_FORCE_WIPE=1."
  fi
  echo "rebuild_full_site.sh: PGOROOT=$(pgo_root_dir) has profiles for all PGO namespaces (ready for merge/use)." >&2
  printf 'Delete ALL profile data under PGOROOT and restart PGO from scratch? [y/N] ' >&2
  local ans
  read -r ans
  case "${ans}" in
    y | Y | yes | YES)
      pgo_wipe_pgroot_for_profile_phase
      ;;
    *)
      pgo_die "profile-phase aborted: PGOROOT unchanged"
      ;;
  esac
}

# Drop breadcrumbs, manifest, and namespace trees when PGOROOT has no profile data yet.
# Returns 0 if contents were removed; 1 if PGOROOT was left unchanged.
pgo_reset_sketch_tree() {
  local root repo_root
  repo_root="${1:?repo_root}"
  root="$(pgo_root_dir)"
  pgo_ensure_pg_root
  if pgo_tree_has_any_profile_data "${repo_root}"; then
    return 1
  fi
  shopt -s nullglob
  local entries=("${root}"/*)
  shopt -u nullglob
  if [[ ${#entries[@]} -eq 0 ]]; then
    return 1
  fi
  rm -rf "${root:?}"/*
  echo "pgo_reset_sketch_tree: cleared layout-only PGOROOT under ${root}" >&2
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
