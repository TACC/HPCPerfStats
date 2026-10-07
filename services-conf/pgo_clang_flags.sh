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
      printf '%s' "-O2 -march=native -mprefer-vector-width=512 -mtune=native -flto=thin --ld-path=$(hpcperfstats_alpine_ld_lld_path) -g0"
      ;;
    debian-lib)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
    libs | *)
      printf '%s' "-O2 -march=native -mtune=native -flto=thin -g0"
      ;;
  esac
}

# Alpine lld22 installs ld.lld under /usr/bin (llvm-ar stays under /usr/lib/llvm22/bin).
hpcperfstats_alpine_ld_lld_path() {
  printf '%s' '/usr/bin/ld.lld'
}

# Autotools (jemalloc shared): compile uses bake CFLAGS (-flto=thin); link must repeat LTO.
hpcperfstats_alpine_thinlto_ldflags() {
  local lld flags
  lld="$(hpcperfstats_alpine_ld_lld_path)"
  # -fuse-ld=lld: clang++ must not fall back to musl gcc ld (TPOFF32 / ThinLTO .so link).
  flags="-flto=thin -fuse-ld=lld --ld-path=${lld}"
  # PGO generate: libclang_rt.profile.a needs libc when lld links .so (e.g. db/liburing).
  case "$(hpcperfstats_pgo_phase)" in
    generate) flags="${flags} -lc" ;;
  esac
  printf '%s' "${flags}"
}

# llvm-ar/ranlib/nm only (safe under BASH_ENV: do not rewrite CC — probe uses "${CC}" as argv0).
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

# Full autotools/cmake link: clang driver + lld (call from PGO RUN before make, not global bootstrap).
hpcperfstats_alpine_export_clang_link_toolchain() {
  local lld
  hpcperfstats_alpine_export_llvm_binutils
  lld="$(hpcperfstats_alpine_ld_lld_path)"
  export CC="clang -fuse-ld=lld --ld-path=${lld}"
  export CXX="clang++ -fuse-ld=lld --ld-path=${lld}"
}

# jemalloc DSO link must use lld (musl gcc ld rejects ThinLTO .pic.o); export before make.
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
  stripped="$(hpcperfstats_strip_lto_and_pgo "${bake}")"
  printf '%s' "${stripped}"
}

# CPython 3.14: --enable-optimizations + profile-gen-stamp / profile-run-stamp / profile-opt.
hpcperfstats_cpython_pgo_namespace() {
  case "${1}" in
    web/gil/cpython | web/ft/cpython) return 0 ;;
    *) return 1 ;;
  esac
}

# Pass to ./configure for Clang soak PGO only (generate/use). stdlib/skip: no --enable-optimizations.
hpcperfstats_cpython_enable_optimizations_for_configure() {
  case "$(hpcperfstats_pgo_phase)" in
    generate | use) printf '%s' '--enable-optimizations' ;;
  esac
}

# Live soak (Clang): LLVM_PROFILE_FILE → PGOROOT/.../raw/python-%p.profraw
# %p = PID so listend, sync_timedb, update_metrics, gunicorn workers each get a unique file;
# Upstream Makefile LLVM_PROF_MERGER merges $(pwd)/*.profclangr → code.profclangd before profile-opt.
hpcperfstats_cpython_llvm_profile_file() {
  local namespace="${1:?namespace required}"
  hpcperfstats_cpython_pgo_namespace "${namespace}" \
    || hpcperfstats_pgo_die "not a CPython PGO namespace: ${namespace}"
  printf '%s' "$(hpcperfstats_pgo_root)/${namespace}/raw/python-%p.profraw"
}

# Run $(LLVM_PROF_MERGER) from the configured CPython Makefile (same as profile-run-stamp tail).
hpcperfstats_cpython_run_makefile_prof_merger() {
  [[ -f Makefile ]] || hpcperfstats_pgo_die "CPython Makefile missing (run ./configure first)"
  make -s --eval '.PHONY: hpcperfstats-prof-merge' \
    --eval 'hpcperfstats-prof-merge: ; $(LLVM_PROF_MERGER)' \
    hpcperfstats-prof-merge
}

# Use-phase: copy soak profiles into $(pwd) as code-*.profclangr for upstream LLVM_PROF_MERGER.
hpcperfstats_cpython_stage_profiles_for_profile_opt() {
  local namespace="${1:?namespace required}"
  local root raw_dir prof staged=() f dest

  root="$(hpcperfstats_pgo_root)"
  raw_dir="${root}/${namespace}/raw"
  prof="${root}/${namespace}/default.profdata"

  rm -f ./code-*.profclangr ./code.profclangd

  shopt -s nullglob
  for f in "${raw_dir}"/python-*.profraw; do
    [[ -s "${f}" ]] || continue
    dest="./code-$(basename "${f}" .profraw).profclangr"
    cp "${f}" "${dest}"
    staged+=( "${dest}" )
  done
  for f in "${raw_dir}"/code-*.profclangr; do
    [[ -s "${f}" ]] || continue
    dest="./$(basename "${f}")"
    cp "${f}" "${dest}"
    staged+=( "${dest}" )
  done
  for f in "${raw_dir}"/*.profclangr; do
    [[ -s "${f}" ]] || continue
    case "$(basename "${f}")" in
      code-*.profclangr) continue ;;
    esac
    dest="./code-$(basename "${f}")"
    cp "${f}" "${dest}"
    staged+=( "${dest}" )
  done
  shopt -u nullglob

  if [[ ${#staged[@]} -gt 0 ]]; then
    hpcperfstats_cpython_run_makefile_prof_merger
  elif [[ -s "${prof}" ]]; then
    cp "${prof}" ./code.profclangd
  else
    hpcperfstats_pgo_die "PGO use requires raw profiles under ${raw_dir} or ${prof}"
  fi

  if [[ ! -s ./code.profclangd ]]; then
    hpcperfstats_pgo_die "PGO use: empty code.profclangd after merge"
  fi
}

# generate: install instrumented tree without running profile-opt (Makefile uses tabs after ':').
hpcperfstats_cpython_relax_install_deps_for_instrumented() {
  [[ -f Makefile ]] || hpcperfstats_pgo_die "CPython Makefile missing (run ./configure first)"
  sed -i \
    -e 's/^all:[[:space:]]*profile-opt/all: build_all/' \
    -e 's/^libinstall:[[:space:]]*all/libinstall: build_all/' \
    -e 's/^sharedinstall:[[:space:]]*all/sharedinstall: build_all/' \
    -e 's/^libainstall:[[:space:]]*all/libainstall: build_all/' \
    Makefile
  grep -q '^all:[[:space:]]*build_all' Makefile \
    || hpcperfstats_pgo_die "PGO generate: failed to repoint all: away from profile-opt"
  grep -q '^libinstall:[[:space:]]*build_all' Makefile \
    || hpcperfstats_pgo_die "PGO generate: failed to repoint libinstall: away from all"
}

# PGO generate must not run upstream profile-run-stamp (unittest) or profile-opt without soak data.
hpcperfstats_cpython_makefile_block_profile_opt_without_profclangd() {
  [[ -f Makefile ]] || hpcperfstats_pgo_die "CPython Makefile missing (run ./configure first)"
  cat >>Makefile <<'EOF'

.PHONY: hpcperfstats-block-profile-run-stamp hpcperfstats-profile-opt-guard
hpcperfstats-block-profile-run-stamp:
	@echo "pgo_clang_flags: profile-run-stamp blocked in PGO generate (use live soak, not unittest)" >&2
	@exit 1
hpcperfstats-profile-opt-guard:
	@test -s "$(CURDIR)/code.profclangd" || { echo "pgo_clang_flags: profile-opt requires non-empty code.profclangd (soak + merge/stage first)" >&2; exit 1; }
EOF
  if grep -q '^profile-run-stamp:' Makefile; then
    sed -i 's/^profile-run-stamp:[[:space:]]*/profile-run-stamp: hpcperfstats-block-profile-run-stamp /' Makefile
  fi
  if grep -q '^profile-opt:' Makefile; then
    sed -i 's/^profile-opt:[[:space:]]*/profile-opt: hpcperfstats-profile-opt-guard /' Makefile
  fi
}

# skip → make install; generate → profile-gen-stamp + altinstall; use → profile-run-stamp + profile-opt + altinstall.
hpcperfstats_cpython_make_install() {
  local namespace="${1:?namespace required}"
  local jobs="${2:-40}"
  local phase root prof

  hpcperfstats_cpython_pgo_namespace "${namespace}" \
    || hpcperfstats_pgo_die "hpcperfstats_cpython_make_install: ${namespace}"

  phase="$(hpcperfstats_pgo_phase)"
  root="$(hpcperfstats_pgo_root)"

  case "${phase}" in
    skip | "" | stdlib)
      make -j"${jobs}"
      make install
      ;;
    generate)
      hpcperfstats_cpython_relax_install_deps_for_instrumented
      hpcperfstats_cpython_makefile_block_profile_opt_without_profclangd
      make -j"${jobs}" profile-gen-stamp
      make altinstall
      ;;
    use)
      hpcperfstats_cpython_stage_profiles_for_profile_opt "${namespace}"
      [[ -s ./code.profclangd ]] \
        || hpcperfstats_pgo_die "PGO use: refuse profile-opt without code.profclangd"
      touch profile-run-stamp
      make -j"${jobs}" profile-opt
      make altinstall
      ;;
    *)
      hpcperfstats_pgo_die "unknown PGO_PHASE=${phase}"
      ;;
  esac
}

# variant: libs (default) or pg (PostgreSQL / Timescale extra vector width)
hpcperfstats_bake_cflags() {
  local namespace="${1:?namespace required}"
  local variant="${2:-libs}"
  local base phase root prof out

  base="$(hpcperfstats_native_base_cflags "${variant}")"

  if hpcperfstats_cpython_pgo_namespace "${namespace}"; then
    printf '%s' "${base}"
    return 0
  fi

  phase="$(hpcperfstats_pgo_phase)"
  root="$(hpcperfstats_pgo_root)"

  case "${phase}" in
    skip | "" | stdlib)
      out="${base}"
      ;;
    generate)
      hpcperfstats_pgo_ensure_raw_dir "${namespace}"
      out="${base} -fprofile-instr-generate=${root}/${namespace}/raw/%m.profraw"
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
