#!/usr/bin/env bash
# Native bake + optional Clang PGO flags for db/proxy/web image builds.
# Source from Dockerfile RUN; set PGO_PHASE / PGO_ROOT (or HPC_PGO_*) before calling.
set -euo pipefail

hpcperfstats_pgo_die() {
  echo "pgo_clang_flags: $*" >&2
  exit 1
}

hpcperfstats_pgo_phase() {
  printf '%s' "${HPC_PGO_PHASE:-${PGO_PHASE:-skip}}"
}

hpcperfstats_pgo_root() {
  printf '%s' "${HPC_PGO_ROOT:-${PGO_ROOT:-/root/.hpcperfstats_pgo}}"
}

# variant: libs (default) or pg (PostgreSQL / Timescale extra vector width)
hpcperfstats_bake_cflags() {
  local namespace="${1:?namespace required}"
  local variant="${2:-libs}"
  local base phase root prof

  if [[ "${variant}" == "pg" ]]; then
    base="-O2 -march=native -mprefer-vector-width=512 -mtune=native -flto=thin -fuse-ld=lld -g0"
  else
    base="-O2 -march=native -mtune=native -flto=thin -fuse-ld=lld -g0"
  fi

  phase="$(hpcperfstats_pgo_phase)"
  root="$(hpcperfstats_pgo_root)"

  case "${phase}" in
    skip | "")
      printf '%s' "${base}"
      ;;
    generate)
      mkdir -p "${root}/${namespace}/raw"
      printf '%s' "${base} -fprofile-instr-generate=${root}/${namespace}/raw"
      ;;
    use)
      prof="${root}/${namespace}/default.profdata"
      if [[ ! -s "${prof}" ]]; then
        hpcperfstats_pgo_die "PGO_PHASE=use requires non-empty ${prof}"
      fi
      printf '%s' "${base} -fprofile-instr-use=${prof}"
      ;;
    *)
      hpcperfstats_pgo_die "unknown PGO_PHASE=${phase}"
      ;;
  esac
}
