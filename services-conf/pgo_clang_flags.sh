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

# Shared libmpdec/jemalloc/libffi: ThinLTO link of a .so needs PIC on LTO objects.
hpcperfstats_shared_lib_namespace() {
  case "${1}" in
    */jemalloc | web/shared/libffi | web/shared/mpdecimal) return 0 ;;
    *) return 1 ;;
  esac
}

# Drop ThinLTO, linker plugin flags, and PGO from flags used at ./configure time.
hpcperfstats_strip_lto_and_pgo() {
  local s="${1}"
  printf '%s' "${s}" | sed -E \
    's/-flto=thin//g; s/-fuse-ld=lld//g; s/-fuse-ld=[^[:space:]]+//g; \
     s/-fprofile-instr-generate=[^[:space:]]+//g; s/-fprofile-instr-use=[^[:space:]]+//g; \
     s/[[:space:]]+/ /g; s/^ //; s/ $//'
}

# variant: libs (Alpine default), pg (PostgreSQL vector width), debian-lib (Debian web
# native deps: ThinLTO in bake CFLAGS; fuse-ld only via LDFLAGS / HPC_FUSE_LD_LLD).
hpcperfstats_native_base_cflags() {
  local variant="${1:-libs}"
  case "${variant}" in
    pg)
      printf '%s' "-O2 -march=native -mprefer-vector-width=512 -mtune=native -flto=thin -fuse-ld=lld -g0"
      ;;
    debian-lib)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
    libs | *)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -fuse-ld=lld -g0"
      ;;
  esac
}

hpcperfstats_configure_cflags() {
  local namespace="${1:?namespace required}"
  local variant="${2:-libs}"
  local bake stripped
  bake="$(hpcperfstats_bake_cflags "${namespace}" "${variant}")"
  stripped="$(hpcperfstats_strip_lto_and_pgo "${bake}")"
  printf '%s' "${stripped}"
}

# variant: libs (default) or pg (PostgreSQL / Timescale extra vector width)
hpcperfstats_bake_cflags() {
  local namespace="${1:?namespace required}"
  local variant="${2:-libs}"
  local base phase root prof out

  base="$(hpcperfstats_native_base_cflags "${variant}")"

  phase="$(hpcperfstats_pgo_phase)"
  root="$(hpcperfstats_pgo_root)"

  case "${phase}" in
    skip | "")
      out="${base}"
      ;;
    generate)
      mkdir -p "${root}/${namespace}/raw"
      out="${base} -fprofile-instr-generate=${root}/${namespace}/raw"
      ;;
    use)
      prof="${root}/${namespace}/default.profdata"
      if [[ ! -s "${prof}" ]]; then
        hpcperfstats_pgo_die "PGO_PHASE=use requires non-empty ${prof}"
      fi
      out="${base} -fprofile-instr-use=${prof}"
      ;;
    *)
      hpcperfstats_pgo_die "unknown PGO_PHASE=${phase}"
      ;;
  esac

  if hpcperfstats_shared_lib_namespace "${namespace}"; then
    out="${out} -fPIC"
  fi
  printf '%s' "${out}"
}
