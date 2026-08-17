#!/usr/bin/env bash
# Remove any existing ./rpmbuild tree, then create ./rpmbuild/* under the monitor directory,
# copy hpcperfstats.spec into SPECS, build pinned static deps once into ./embedded-static-prefix/,
# run configure + make dist with HPC_BUNDLE_EMBED_PREFIX=1 so the tarball includes that tree,
# and copy the tarball to rpmbuild/SOURCES.
#
# Each run is idempotent: removes prior rpmbuild/, embedded-static-prefix/, and monitor
# Autotools artifacts (via scripts/lib/monitor_tree_clean.sh) before dist; post-dist cleanup
# leaves outputs under rpmbuild/ only.
#
# The spec build uses SKIP_DEPS=1 and PREFIX=.../embedded-static-prefix inside the unpacked
# sources so only hpcperfstatsd is compiled (no second libev/rabbitmq-c/LIKWID build).
#
# Usage (from anywhere; paths are anchored to HPCPerfStats/monitor):
#   ./scripts/prepare_rpmbuild_dirs.sh
#   ./scripts/prepare_rpmbuild_dirs.sh --debug-build
#
# Footer: prints only the rpmbuild command for the requested build. Validation
# notes and the debug verify chain appear only with --debug-build.
#
# Environment:
#   SKIP_DEPS  If 1, skip rebuilding static deps when PREFIX already has the .a files
#              (passed through to build_static_bundle.sh --deps-only). Prepare removes
#              embedded-static-prefix/ each run; leave unset for normal use.
#   HPC_BUNDLE_RELEASE_BUILD  Defaults to 1 so dependency builds match release tuning;
#              set to 0 to override.
#
set -euo pipefail
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/monitor_tree_clean.sh
source "${SCRIPT_DIR}/lib/monitor_tree_clean.sh"
readonly SPEC_SRC="${MONITOR_DIR}/hpcperfstats.spec"
readonly EMBED_PREFIX="${MONITOR_DIR}/embedded-static-prefix"

debug_build=0
while (($# > 0)); do
  case "$1" in
    --debug-build)
      debug_build=1
      ;;
    -h|--help)
      cat <<EOF
Usage: ./scripts/prepare_rpmbuild_dirs.sh [--debug-build]

  Default:           Print only the release rpmbuild -ba command.
  --debug-build      Print only the debug rpmbuild (hpc_debug_build 1) chained with
                     ./scripts/rpm_debug_shm_verify.sh, plus debug verify notes.
                     Debug RPM uses sample_freq=30 / sample_freq_slow=60 in hpcperfstats.conf.

  Release (default): rpmbuild without hpc_debug_build — production RPM, no /dev/shm mirror.
  Debug (opt-in):    rpmbuild with hpc_debug_build 1 — -g symbols and --enable-debug.

  See README.md "DEBUG /dev/shm mirror" for the RPM debug workflow.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

monitor_spec_field() {
  local field="$1"
  local file="$2"
  grep -E "^${field}:" "${file}" | head -1 | sed 's/^[^:]*:[[:space:]]*//;s/[[:space:]]*$//'
}

is_x86_build_host() {
  case "$(uname -m)" in
  x86_64|i?86) return 0 ;;
  *) return 1 ;;
  esac
}

have_static_archive_basename() {
  local name="$1"
  test -f "${PREFIX}/lib/${name}" || test -f "${PREFIX}/lib64/${name}"
}

verify_likwid_static_link_probe() {
  local tbase out rc
  tbase="$(mktemp "${TMPDIR:-/tmp}/hps_likwid_probe.XXXXXX")"
  out="${tbase}.out"
  printf '%s\n' '#include <likwid.h>' 'int main(void){ return perfmon_init(0, (int*)0); }' > "${tbase}.c"
  rc=0
  # LIBS from the caller must already include -lpthread -ldl (same as configure env).
  if ! ${CC:-gcc} ${CPPFLAGS:-} ${LDFLAGS:-} "${tbase}.c" ${LIBS:-} -o "${out}" >/dev/null 2>&1; then
    rc=1
  fi
  rm -f "${tbase}.c" "${out}"
  return "${rc}"
}

cd "${MONITOR_DIR}"

if test ! -f "${SPEC_SRC}"; then
  echo "Missing spec file: ${SPEC_SRC}" >&2
  exit 1
fi

ver="$(monitor_spec_field Version "${SPEC_SRC}")"
tarbase="$(sed -n 's/^AC_INIT(\[\([^]]*\)\].*/\1/p' "${MONITOR_DIR}/configure.ac" | head -1)"
if test -z "${ver}" || test -z "${tarbase}"; then
  echo "Could not read Version from ${SPEC_SRC} or package name from configure.ac AC_INIT" >&2
  exit 1
fi

# tests/Makefile.am EXTRA_DIST — each must exist or `make dist` fails with
# "No rule to make target '<file>'" (partial checkout / stale tree).
for distfile in \
  tests/test_monitor_configure_help.sh.in \
  tests/run_tests.sh \
  tests/README.md \
  tests/scripts/bootstrap_local_rabbitmq.sh \
  scripts/check_unsafe_c_patterns.sh \
  scripts/check_unsafe_c_patterns.allowlist \
  scripts/check_emitted_variable_names.py \
  scripts/emit_build_capabilities.py \
  scripts/build_message_expectations.py \
  scripts/validate_shm_messages.py \
  scripts/lib/__init__.py \
  scripts/lib/message_parse.py \
  scripts/lib/row_validate.py \
  scripts/lib/payload_parse.py \
  scripts/lib/device_validate.py \
  scripts/lib/listend_contract.py \
  scripts/lib/host_live_probes.py \
  scripts/lib/value_plausibility.py \
  scripts/lib/papi_shm_validate.py \
  scripts/lib/live_spot_check.py \
  scripts/lib/golden_diff.py \
  scripts/lib/tacc_system_profiles.py \
  scripts/validate_stampede3_profile.sh \
  scripts/lib/daemon_conf.py \
  scripts/lib/shm_snapshot.py \
  scripts/lib/cross_sample_validate.py \
  scripts/lib/cross_sample_stimulus.py \
  scripts/lib/monitor_tree_clean.sh \
  scripts/rpm_debug_shm_verify.sh \
  scripts/prepare_rpmbuild_stampede3.sh \
  scripts/prepare_rpmbuild_ls6.sh \
  scripts/gpu_lspci_probe.sh \
  scripts/gpu_lspci_detect.awk \
  scripts/fleet/README
do
  if test ! -f "${MONITOR_DIR}/${distfile}"; then
    echo "Missing file required for make dist: ${MONITOR_DIR}/${distfile}" >&2
    echo "Restore tests/ from the full repository; partial copies cannot build the tarball." >&2
    exit 1
  fi
done

tb="${tarbase}-${ver}.tar.gz"
sources_dir="${MONITOR_DIR}/rpmbuild/SOURCES"
specs_dir="${MONITOR_DIR}/rpmbuild/SPECS"
topdir="${MONITOR_DIR}/rpmbuild"
static_srcdir="${topdir}/static-src"

echo "Removing any prior ${EMBED_PREFIX} ..."
rm -rf "${EMBED_PREFIX}"

if test -e "${topdir}"; then
  echo "Removing existing ${topdir} ..."
  rm -rf "${topdir}"
fi

mkdir -p \
  "${sources_dir}" \
  "${specs_dir}" \
  "${topdir}/BUILD" \
  "${topdir}/RPMS" \
  "${topdir}/SRPMS" \
  "${topdir}/BUILDROOT"

cp -f "${SPEC_SRC}" "${specs_dir}/hpcperfstats.spec"
echo "Spec installed: ${specs_dir}/hpcperfstats.spec"

echo "Building pinned static deps into ${EMBED_PREFIX} (build_static_bundle.sh --deps-only) ..."
export PREFIX="${EMBED_PREFIX}"
export SRCDIR="${static_srcdir}"
export HPC_BUNDLE_RELEASE_BUILD="${HPC_BUNDLE_RELEASE_BUILD:-1}"
mkdir -p "${PREFIX}/include" "${PREFIX}/lib" "${PREFIX}/lib64" "${PREFIX}/lib/pkgconfig"
(cd "${MONITOR_DIR}" && ./scripts/build_static_bundle.sh --deps-only)

export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PREFIX}/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"

# Always required (libev + rabbitmq-c on every arch).
if ! have_static_archive_basename "libev.a"; then
  cat <<EOF >&2
libev static archive was not found under ${PREFIX}/lib or ${PREFIX}/lib64.
Expected: libev.a
Rebuild deps with SKIP_DEPS unset:
  PREFIX="${PREFIX}" SRCDIR="${SRCDIR}" ./scripts/build_static_bundle.sh --deps-only
EOF
  exit 1
fi
if ! have_static_archive_basename "librabbitmq.a"; then
  cat <<EOF >&2
rabbitmq-c static archive was not found under ${PREFIX}/lib or ${PREFIX}/lib64.
Expected: librabbitmq.a (cmake CMAKE_INSTALL_LIBDIR=lib installs under lib/)
Rebuild deps with SKIP_DEPS unset:
  PREFIX="${PREFIX}" SRCDIR="${SRCDIR}" ./scripts/build_static_bundle.sh --deps-only
EOF
  exit 1
fi

if is_x86_build_host; then
  if ! have_static_archive_basename "liblikwid.a" \
     || ! have_static_archive_basename "liblikwid-hwloc.a" \
     || ! have_static_archive_basename "liblikwid-lua.a"; then
    cat <<EOF >&2
LIKWID static archives were not found under ${PREFIX}/lib or ${PREFIX}/lib64.
Expected: liblikwid.a, liblikwid-hwloc.a, liblikwid-lua.a
Rebuild deps with SKIP_DEPS unset:
  PREFIX="${PREFIX}" SRCDIR="${SRCDIR}" ./scripts/build_static_bundle.sh --deps-only
EOF
    exit 1
  fi

  # Include -lpthread -ldl in exported LIBS (not only on the probe cmdline). LS6 static
  # liblikwid needs them for configure AC_SEARCH_LIBS(perfmon_init); the probe used to
  # pass while configure still failed with "(cached) no".
  export LIBS="-Wl,--start-group -llikwid -llikwid-hwloc -llikwid-lua -Wl,--end-group -lm -lrt -lpthread -ldl ${LIBS:-}"
  if ! verify_likwid_static_link_probe; then
    cat <<EOF >&2
Unable to link a trivial LIKWID program from PREFIX=${PREFIX}.
Check that liblikwid*.a archives match this host/toolchain and that no stale build artifacts remain.
Try:
  rm -rf "${PREFIX}" "${SRCDIR}"
  PREFIX="${PREFIX}" SRCDIR="${SRCDIR}" ./scripts/build_static_bundle.sh --deps-only
EOF
    exit 1
  fi
else
  if ! have_static_archive_basename "libpapi.a"; then
    cat <<EOF >&2
PAPI static archive was not found under ${PREFIX}/lib or ${PREFIX}/lib64.
Expected: libpapi.a (aarch64 / non-x86 DCGM+PAPI hybrid CPU path)
Rebuild deps with SKIP_DEPS unset:
  PREFIX="${PREFIX}" SRCDIR="${SRCDIR}" ./scripts/build_static_bundle.sh --deps-only
EOF
    exit 1
  fi
fi

rm -f "${MONITOR_DIR}/${tb}"
echo "Rebuilding ${tb} from ${MONITOR_DIR} (clean tree; autoreconf -fi; configure; make dist) ..."
monitor_tree_clean_pre_dist "${MONITOR_DIR}" "${tb}"

echo "Running autoreconf -fi in ${MONITOR_DIR} ..."
(cd "${MONITOR_DIR}" && autoreconf -fi)

mapfile -t feat_flags < <(cd "${MONITOR_DIR}" && ./scripts/build_static_bundle.sh --print-configure-flags)
if test "${#feat_flags[@]}" -gt 0; then
  echo "Host probe configure flags: ${feat_flags[*]}"
else
  echo "Host probe configure flags: (none)"
fi

if ! (cd "${MONITOR_DIR}" && ./configure --with-systemduserunitdir=no "${feat_flags[@]}"); then
  cat <<EOF >&2
configure failed while running make dist for ${tb}.
Static libraries were expected under ${PREFIX} (from build_static_bundle.sh --deps-only).
Host probes should pass --disable-amd-gpu when GPUPerfAPI headers are missing (see
build_static_bundle.sh --print-configure-flags). Check the bundle script output,
network access for pinned tarballs, and host toolchain.
EOF
  exit 1
fi

(cd "${MONITOR_DIR}" && HPC_BUNDLE_EMBED_PREFIX=1 make dist)

if test ! -f "${MONITOR_DIR}/${tb}"; then
  echo "make dist did not produce ${MONITOR_DIR}/${tb}" >&2
  exit 1
fi

dist_top="${tarbase}-${ver}"
verify_dist_tarball_host_headers "${MONITOR_DIR}/${tb}" "${dist_top}"
verify_dist_tarball_build_scripts "${MONITOR_DIR}/${tb}" "${dist_top}"

cp -f "${MONITOR_DIR}/${tb}" "${sources_dir}/${tb}"
monitor_tree_clean_post_dist "${MONITOR_DIR}" "${tb}"
echo "Source tarball: ${sources_dir}/${tb}"
echo "RPM tree ready under ${topdir}"
echo ""
release_rpmbuild="rpmbuild -ba --define \"_topdir ${topdir}\" \"${specs_dir}/hpcperfstats.spec\""
debug_rpmbuild="rpmbuild -ba --define \"_topdir ${topdir}\" --define \"hpc_debug_build 1\" \"${specs_dir}/hpcperfstats.spec\""
if test "${debug_build}" = "1"; then
  echo "Build, install, and validate /dev/shm (from ${MONITOR_DIR}):"
  echo "  ${debug_rpmbuild} && ./scripts/rpm_debug_shm_verify.sh"
  cat <<EOF

Debug verify notes:
  - Debug %install stashes monitor-build-capabilities.json under ${topdir}/debug-verify/
    (survives EL10 rpmbuild rmbuild, which deletes BUILD/).
  - rpm_debug_shm_verify.sh reads that stash; BUILD/.build-static is optional.
  - Optional: add --noclean to rpmbuild only if you need to inspect the Autotools tree.
  - After debug RPM install: systemctl start hpcperfstatsd (or run foreground).
  - Payload mirror: /dev/shm/hpcperfstatsd-debug/{schema,fast,full}
  - Override base dir: HPCPERFSTATS_DEBUG_SHM_DIR
EOF
else
  echo "Build binary and source RPMs with:"
  echo "  ${release_rpmbuild}"
fi
