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

hpcperfstats_pgo_ensure_raw_dir() {
  local namespace="${1:?namespace required}"
  local phase root
  phase="$(hpcperfstats_pgo_phase)"
  [[ "${phase}" == generate ]] || return 0
  root="$(hpcperfstats_pgo_root)"
  mkdir -p "${root}/${namespace}/raw"
}

# Linking /opt/* built with PGO generate pulls instrumented deps (e.g. zstd→libz).
hpcperfstats_pgo_ensure_web_shared_link_dirs() {
  local ns
  for ns in web/shared/jemalloc web/shared/zlib-ng web/shared/zstd \
    web/shared/mpdecimal web/shared/libffi; do
    hpcperfstats_pgo_ensure_raw_dir "${ns}"
  done
}

hpcperfstats_pgo_ensure_web_gil_cpython_link_dirs() {
  hpcperfstats_pgo_ensure_web_shared_link_dirs
  hpcperfstats_pgo_ensure_raw_dir web/gil/cpython
}

hpcperfstats_pgo_ensure_web_ft_cpython_link_dirs() {
  hpcperfstats_pgo_ensure_web_shared_link_dirs
  hpcperfstats_pgo_ensure_raw_dir web/ft/cpython
}

hpcperfstats_pgo_ensure_web_gil_optimization_stack_link_dirs() {
  hpcperfstats_pgo_ensure_web_shared_link_dirs
  hpcperfstats_pgo_ensure_raw_dir web/gil/cpython
  hpcperfstats_pgo_ensure_raw_dir web/gil/optimization-stack
}

hpcperfstats_pgo_ensure_web_ft_optimization_stack_link_dirs() {
  hpcperfstats_pgo_ensure_web_shared_link_dirs
  hpcperfstats_pgo_ensure_raw_dir web/ft/cpython
  hpcperfstats_pgo_ensure_raw_dir web/ft/optimization-stack
}

hpcperfstats_pgo_ensure_db_zstd_upstream() {
  hpcperfstats_pgo_ensure_raw_dir db/lz4
  hpcperfstats_pgo_ensure_raw_dir db/zlib-ng
}

hpcperfstats_pgo_ensure_db_postgresql_link_dirs() {
  local ns
  for ns in db/jemalloc db/lz4 db/zlib-ng db/zstd db/icu db/liburing \
    db/postgresql; do
    hpcperfstats_pgo_ensure_raw_dir "${ns}"
  done
}

hpcperfstats_pgo_ensure_db_timescaledb_link_dirs() {
  hpcperfstats_pgo_ensure_db_postgresql_link_dirs
  hpcperfstats_pgo_ensure_raw_dir db/timescaledb
}

hpcperfstats_pgo_ensure_proxy_nginx_link_dirs() {
  local ns
  for ns in proxy/jemalloc proxy/zlib-ng proxy/brotli proxy/zstd \
    proxy/openssl proxy/nginx; do
    hpcperfstats_pgo_ensure_raw_dir "${ns}"
  done
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
     s/--ld-path=[^[:space:]]+//g; \
     s/-fprofile-instr-generate=[^[:space:]]+//g; s/-fprofile-instr-use=[^[:space:]]+//g; \
     s/[[:space:]]+/ /g; s/^ //; s/ $//'
}

# variant: libs (Alpine default), pg (PostgreSQL vector width), debian-lib (Debian web
# native deps: ThinLTO in bake CFLAGS; --ld-path only via LDFLAGS / HPC_CLANG_LD_PATH).
hpcperfstats_native_base_cflags() {
  local variant="${1:-libs}"
  case "${variant}" in
    pg)
      printf '%s' "-O2 -march=native -mprefer-vector-width=512 -mtune=native -flto=thin --ld-path=/usr/lib/llvm22/bin/ld.lld -g0"
      ;;
    debian-lib)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
    libs | *)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
  esac
}

# Autotools (jemalloc shared): compile uses bake CFLAGS (-flto=thin); link must repeat LTO.
hpcperfstats_alpine_thinlto_ldflags() {
  printf '%s' "-flto=thin --ld-path=/usr/lib/llvm22/bin/ld.lld"
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
      hpcperfstats_pgo_ensure_raw_dir "${namespace}"
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
