# shellcheck shell=bash
# Helpers for prepare_rpmbuild_dirs.sh --measure-overhead: fail fast before the build, find
# the release RPM, and hand it to measurement/bin/run_overhead_profile.sh as root.
# Sourced; every function returns non-zero (with a message on stderr) instead of exiting.

# Tools the release build + measurement need on the reserved node (plan decision 6).
MEASURE_OVERHEAD_TOOLS="gcc make autoreconf rpmbuild curl"

# Variables prepare_rpmbuild_dirs.sh exports for its own deps build and make dist configure.
MEASURE_OVERHEAD_PREPARE_ENV="PREFIX SRCDIR HPC_BUNDLE_RELEASE_BUILD CPPFLAGS LDFLAGS PKG_CONFIG_PATH LIBS"

# measure_overhead_caller_env -> env(1) arguments that restore the caller's values of
# MEASURE_OVERHEAD_PREPARE_ENV (VAR=value when set, -u VAR when unset). Capture them before
# prepare's exports and run rpmbuild as `env <these> rpmbuild ...`, so %build sees the same
# environment as the printed rpmbuild run from the operator's shell. c122-018: prepare's LIBS
# leaked into %build, put -lm inside the -Wl,-Bstatic group, and the link failed (no libm.a).
# All -u options come first: env(1) stops option parsing at the first VAR=value, so a set
# variable followed by an unset one ran `-u` as the command (make check: CPPFLAGS set).
measure_overhead_caller_env() {
  local v unset_opts="" set_args=""
  for v in ${MEASURE_OVERHEAD_PREPARE_ENV}; do
    if test "${!v+set}" = set; then
      set_args+="$(printf '%q ' "${v}=${!v}")"
    else
      unset_opts+="-u ${v} "
    fi
  done
  printf '%s%s' "${unset_opts}" "${set_args}"
}

measure_overhead_harness() {
  printf '%s\n' "$1/measurement/bin/run_overhead_profile.sh"
}

# measure_overhead_command MONITOR_DIR RPM BUILD_INFO [HARNESS_ARG...]
# Print the harness command, one shell-quoted word per argument.
measure_overhead_command() {
  local monitor_dir="$1" rpm="$2" build_info="$3"
  shift 3
  local words=()
  if test "$(id -u)" != "0"; then
    words+=(sudo -n)
  fi
  words+=("$(measure_overhead_harness "${monitor_dir}")" --rpm "${rpm}" --build-info "${build_info}" "$@")
  printf '%q ' "${words[@]}"
  printf '\n'
}

# measure_overhead_preflight MONITOR_DIR [HARNESS_ARG...]
# Runs before any build step: harness present, passwordless sudo (unless root), build tools.
measure_overhead_preflight() {
  local monitor_dir="$1"
  shift
  local harness missing=() tool
  harness="$(measure_overhead_harness "${monitor_dir}")"
  if test ! -f "${harness}"; then
    echo "--measure-overhead: ${harness} not found (run from the git checkout)" >&2
    return 1
  fi
  if test "$(id -u)" != "0" && ! sudo -n true >/dev/null 2>&1; then
    cat >&2 <<EOF
--measure-overhead: sudo would prompt for a password, so the root measurement step cannot run
unattended after the build. Either run this script as root, or build without the flag and run:
  sudo ${harness} --rpm ${monitor_dir}/rpmbuild/RPMS/$(uname -m)/<hpcperfstatsd RPM> --build-info ${monitor_dir}/rpmbuild/build-info $*
EOF
    return 1
  fi
  for tool in ${MEASURE_OVERHEAD_TOOLS}; do
    command -v "${tool}" >/dev/null 2>&1 || missing+=("${tool}")
  done
  if test "$(uname -m)" = "aarch64" && ! measure_overhead_have_libdcgm; then
    missing+=("libdcgm (datacenter-gpu-manager-4-devel or libdcgm-devel)")
  fi
  if test "${#missing[@]}" -gt 0; then
    echo "--measure-overhead: missing build prerequisites: ${missing[*]}" >&2
    return 1
  fi
}

measure_overhead_have_libdcgm() {
  local d
  for d in /usr/lib64 /usr/lib /usr/lib/aarch64-linux-gnu; do
    if compgen -G "${d}/libdcgm.so*" >/dev/null; then
      return 0
    fi
  done
  return 1
}

# measure_overhead_find_rpm TOPDIR VERSION -> the single hpcperfstatsd-VERSION-*.rpm for uname -m.
measure_overhead_find_rpm() {
  local topdir="$1" ver="$2" arch
  local found=()
  arch="$(uname -m)"
  shopt -s nullglob
  found=("${topdir}/RPMS/${arch}/hpcperfstatsd-${ver}-"*.rpm)
  shopt -u nullglob
  if test "${#found[@]}" -ne 1; then
    echo "--measure-overhead: expected exactly one ${topdir}/RPMS/${arch}/hpcperfstatsd-${ver}-*.rpm, found ${#found[@]}" >&2
    return 1
  fi
  printf '%s\n' "${found[0]}"
}
