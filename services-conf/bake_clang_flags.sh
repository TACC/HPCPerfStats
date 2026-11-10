# Native ThinLTO bake flags for db/proxy/web image builds (no custom Clang host profiles).
# Source from Dockerfile RUN via BASH_ENV or `. /usr/local/lib/hpcperfstats/bake_clang_flags.sh`.
# CPython always uses upstream --enable-optimizations + plain make/altinstall.

hpcperfstats_bake_die() {
  echo "bake_clang_flags: $*" >&2
  exit 1
}

# Historical call sites expected raw profile dirs; custom host profiling is gone — no-ops.
hpcperfstats_ensure_web_shared_link_dirs() { :; }
hpcperfstats_ensure_web_gil_cpython_link_dirs() { :; }
hpcperfstats_ensure_web_ft_cpython_link_dirs() { :; }
hpcperfstats_ensure_web_gil_optimization_stack_link_dirs() { :; }
hpcperfstats_ensure_web_ft_optimization_stack_link_dirs() { :; }
hpcperfstats_ensure_db_zstd_upstream() { :; }
hpcperfstats_ensure_db_postgresql_link_dirs() { :; }
hpcperfstats_ensure_db_timescaledb_link_dirs() { :; }
hpcperfstats_ensure_proxy_nginx_link_dirs() { :; }

# Shared libmpdec/jemalloc/libffi: ThinLTO link of a .so needs PIC on LTO objects.
hpcperfstats_shared_lib_namespace() {
  case "${1}" in
    */jemalloc | */pcre2 | web/shared/libffi | web/shared/mpdecimal) return 0 ;;
    *) return 1 ;;
  esac
}

# Drop ThinLTO / linker plugin flags from flags used at ./configure time.
hpcperfstats_strip_lto() {
  local s="${1}"
  printf '%s' "${s}" | sed -E \
    's/-flto=thin//g; s/-fuse-ld=lld//g; s/-fuse-ld=[^[:space:]]+//g; \
     s/--ld-path=[^[:space:]]+//g; \
     s/[[:space:]]+/ /g; s/^ //; s/ $//'
}

# Link-only flags (-fuse-ld / --ld-path) ride along in CC/CFLAGS so every link
# uses lld; this silences clang's per-compile "argument unused" warning for them.
HPC_NO_UNUSED_ARG_WARN="-Wno-unused-command-line-argument"

# variant: libs (Alpine default), pg (PostgreSQL vector width), debian-lib (Debian web).
hpcperfstats_native_base_cflags() {
  local variant="${1:-libs}"
  case "${variant}" in
    pg)
      printf '%s' "-O2 -march=native -mprefer-vector-width=512 -mtune=native -flto=thin --ld-path=$(hpcperfstats_alpine_ld_lld_path) ${HPC_NO_UNUSED_ARG_WARN} -g0"
      ;;
    debian-lib)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
    libs | *)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
  esac
}

hpcperfstats_alpine_ld_lld_path() {
  printf '%s' '/usr/bin/ld.lld'
}

hpcperfstats_alpine_thinlto_ldflags() {
  local lld
  lld="$(hpcperfstats_alpine_ld_lld_path)"
  printf '%s' "-flto=thin -fuse-ld=lld --ld-path=${lld} ${HPC_NO_UNUSED_ARG_WARN}"
}

hpcperfstats_alpine_export_llvm_binutils() {
  local llvm_bin lto_ld
  llvm_bin="/usr/lib/llvm22/bin"
  lto_ld="$(hpcperfstats_alpine_thinlto_ldflags)"
  export AR="${llvm_bin}/llvm-ar"
  export RANLIB="${llvm_bin}/llvm-ranlib"
  export NM="${llvm_bin}/llvm-nm"
  export LLVM_CONFIG="${llvm_bin}/llvm-config"
  export HPC_THINLTO_LDFLAGS="${lto_ld}"
  export PATH="${llvm_bin}:${PATH}"
}

hpcperfstats_alpine_export_clang_link_toolchain() {
  local lld
  hpcperfstats_alpine_export_llvm_binutils
  lld="$(hpcperfstats_alpine_ld_lld_path)"
  export CC="clang -fuse-ld=lld --ld-path=${lld} ${HPC_NO_UNUSED_ARG_WARN}"
  export CXX="clang++ -fuse-ld=lld --ld-path=${lld} ${HPC_NO_UNUSED_ARG_WARN}"
}

hpcperfstats_alpine_jemalloc_export_link_toolchain() {
  hpcperfstats_alpine_export_clang_link_toolchain
  export LDFLAGS="${HPC_THINLTO_LDFLAGS}"
  export EXTRA_LDFLAGS="${HPC_THINLTO_LDFLAGS}"
}

hpcperfstats_alpine_maybe_bootstrap_llvm_toolchain() {
  [[ "${HPC_ALPINE_LLVM_TOOLCHAIN:-}" == 1 ]] || return 0
  hpcperfstats_alpine_export_llvm_binutils
}

hpcperfstats_alpine_maybe_bootstrap_llvm_toolchain

hpcperfstats_configure_cflags() {
  local namespace="${1:?namespace required}"
  local variant="${2:-libs}"
  local bake stripped
  bake="$(hpcperfstats_bake_cflags "${namespace}" "${variant}")"
  stripped="$(hpcperfstats_strip_lto "${bake}")"
  printf '%s' "${stripped}"
}

hpcperfstats_cpython_namespace() {
  case "${1}" in
    web/gil/cpython | web/ft/cpython) return 0 ;;
    *) return 1 ;;
  esac
}

hpcperfstats_cpython_enable_optimizations_for_configure() {
  printf '%s' '--enable-optimizations'
}

# Always upstream --enable-optimizations path: plain make + altinstall/install.
hpcperfstats_cpython_make_install() {
  local namespace="${1:?namespace required}"
  local jobs="${2:-40}"

  hpcperfstats_cpython_namespace "${namespace}" \
    || hpcperfstats_bake_die "hpcperfstats_cpython_make_install: ${namespace}"

  make -j"${jobs}"
  make altinstall
}

# ThinLTO bake CFLAGS only (no Clang instrumented-profile flags).
hpcperfstats_bake_cflags() {
  local namespace="${1:?namespace required}"
  local variant="${2:-libs}"
  local out

  out="$(hpcperfstats_native_base_cflags "${variant}")"
  if hpcperfstats_shared_lib_namespace "${namespace}"; then
    out="${out} -fPIC"
  fi
  printf '%s' "${out}"
}
