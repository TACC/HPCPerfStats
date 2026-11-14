#!/usr/bin/env bash
# Optional compile/link accelerators for monitor builds: ccache and mold.
# Sourced by build_static_bundle.sh (build_monitor) and run_valgrind_check.sh.
#
# Both tools are optional. Each one is used only when it is installed AND a
# one-line probe compile/link with it succeeds; otherwise the build runs exactly
# as before (plain $CC, the system linker). Nothing here can fail a build.
#
# Environment:
#   HPC_BUNDLE_CCACHE      auto (default) = use ccache when it works; 0 = never.
#   HPC_BUNDLE_MOLD        auto (default) = use mold when it works, except for
#                          release builds (HPC_BUNDLE_RELEASE_BUILD), which keep the
#                          system linker unless this is 1; 0 = never; 1 = also for
#                          release builds.
#   HPC_BUNDLE_CCACHE_BIN  ccache command or path (default: ccache).
#   HPC_BUNDLE_MOLD_BIN    mold command or path (default: mold).
#
# ccache output is byte-identical to the plain compiler, so it is allowed in release
# builds. mold produces a different (equivalent) binary, so shipped RPMs stay on the
# system linker unless HPC_BUNDLE_MOLD=1.
#
# mold is selected with -B<dir>, where <dir> holds mold's "ld" shim (mold installs
# <prefix>/libexec/mold/ld). GCC 11 (EL9) has no -fuse-ld=mold; -B works for GCC and
# Clang alike.

build_accel_truthy() {
  case "${1:-}" in
  1 | yes | YES | true | TRUE | on | ON) return 0 ;;
  *) return 1 ;;
  esac
}

build_accel_off() {
  case "${1:-auto}" in
  0 | no | NO | false | FALSE | off | OFF) return 0 ;;
  *) return 1 ;;
  esac
}

# Compile (and link unless $1 is -c) a trivial C file with the given command words.
build_accel_probe() {
  local mode="$1"
  shift
  local tdir rc=1
  tdir="$(mktemp -d "${TMPDIR:-/tmp}/hpsaccel.XXXXXX")" || return 1
  printf 'int main(void) { return 0; }\n' >"${tdir}/p.c"
  if test "${mode}" = "-c"; then
    "$@" -c "${tdir}/p.c" -o "${tdir}/p.o" >/dev/null 2>&1 && rc=0
  else
    "$@" "${tdir}/p.c" -o "${tdir}/p" >/dev/null 2>&1 && rc=0
  fi
  rm -rf "${tdir}"
  return "${rc}"
}

# Directory with mold's "ld" shim next to the resolved mold binary, or nothing.
build_accel_mold_dir() {
  local mold bindir d
  mold="$(command -v "${HPC_BUNDLE_MOLD_BIN:-mold}" 2>/dev/null)" || return 1
  bindir="$(cd "$(dirname "$(readlink -f "${mold}")")" && pwd)" || return 1
  for d in "${bindir}/../libexec/mold" "${bindir}/../lib/mold" "${bindir}/../lib64/mold"; do
    if test -x "${d}/ld"; then
      (cd "${d}" && pwd)
      return 0
    fi
  done
  return 1
}

# Export CC / LDFLAGS for the optional accelerators and print one status line each.
# Call after host probes that run "${CC}" as a single word, before configure.
build_accel_apply() {
  local cc="${CC:-cc}" ccache_bin mold_dir
  local -a cc_words ld_words
  read -r -a cc_words <<<"${cc}"
  read -r -a ld_words <<<"${LDFLAGS:-}"

  ccache_bin="${HPC_BUNDLE_CCACHE_BIN:-ccache}"
  if build_accel_off "${HPC_BUNDLE_CCACHE:-auto}"; then
    echo "Build accel: ccache off (HPC_BUNDLE_CCACHE=${HPC_BUNDLE_CCACHE})" >&2
  elif case " ${cc} " in *ccache*) true ;; *) false ;; esac; then
    echo "Build accel: ccache already in CC (${cc})" >&2
  elif ! command -v "${ccache_bin}" >/dev/null 2>&1; then
    echo "Build accel: ccache not found; compiling without it" >&2
  elif build_accel_probe -c "${ccache_bin}" "${cc_words[@]}"; then
    export CC="${ccache_bin} ${cc}"
    echo "Build accel: ccache on (CC=${CC})" >&2
  else
    echo "Build accel: ccache probe failed; compiling without it" >&2
  fi

  if build_accel_off "${HPC_BUNDLE_MOLD:-auto}"; then
    echo "Build accel: mold off (HPC_BUNDLE_MOLD=${HPC_BUNDLE_MOLD})" >&2
  elif build_accel_truthy "${HPC_BUNDLE_RELEASE_BUILD:-0}" &&
    ! build_accel_truthy "${HPC_BUNDLE_MOLD:-auto}"; then
    echo "Build accel: mold skipped for release build (set HPC_BUNDLE_MOLD=1 to use it)" >&2
  elif ! mold_dir="$(build_accel_mold_dir)"; then
    echo "Build accel: mold not found; using the system linker" >&2
  elif build_accel_probe link "${cc_words[@]}" "-B${mold_dir}" "${ld_words[@]}"; then
    export LDFLAGS="-B${mold_dir}${LDFLAGS:+ }${LDFLAGS:-}"
    echo "Build accel: mold on (-B${mold_dir})" >&2
  else
    echo "Build accel: mold probe failed; using the system linker" >&2
  fi
}
