# shellcheck shell=bash
# Helpers for prepare_rpmbuild_dirs.sh --measure-overhead: fail fast before the build, find
# the release RPM, and hand it to measurement/bin/run_overhead_profile.sh as root.
# Sourced; every function returns non-zero (with a message on stderr) instead of exiting.

# Tools the release build + measurement need on the reserved node (plan decision 6).
MEASURE_OVERHEAD_TOOLS="gcc make autoreconf rpmbuild curl"

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
