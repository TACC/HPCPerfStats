#!/usr/bin/env bash
# Create ./rpmbuild/* under the monitor directory, copy hpcperfstats.spec into SPECS,
# and build hpcperfstats-<Version>.tar.gz from this checkout via make dist (no external
# tarball download — the tree containing this script is the package source).
#
# Before ./configure, runs scripts/build_static_bundle.sh --deps-only so pinned static
# libev, rabbitmq-c, and (on x86) LIKWID exist under rpmbuild/static-prefix; configure
# sees them via CPPFLAGS/LDFLAGS/PKG_CONFIG_PATH (same idea as the RPM %%build PREFIX).
#
# Usage (from anywhere; paths are anchored to HPCPerfStats/monitor):
#   ./scripts/prepare_rpmbuild_dirs.sh
#
# Environment:
#   SKIP_DEPS  If 1, skip rebuilding static deps when PREFIX already has the .a files
#              (passed through to build_static_bundle.sh --deps-only).
#
set -euo pipefail
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly SPEC_SRC="${MONITOR_DIR}/hpcperfstats.spec"

monitor_spec_field() {
  local field="$1"
  local file="$2"
  grep -E "^${field}:" "${file}" | head -1 | sed 's/^[^:]*:[[:space:]]*//;s/[[:space:]]*$//'
}

cd "${MONITOR_DIR}"

if test ! -f "${SPEC_SRC}"; then
  echo "Missing spec file: ${SPEC_SRC}" >&2
  exit 1
fi

pkg="$(monitor_spec_field Name "${SPEC_SRC}")"
ver="$(monitor_spec_field Version "${SPEC_SRC}")"
if test -z "${pkg}" || test -z "${ver}"; then
  echo "Could not read Name/Version from ${SPEC_SRC}" >&2
  exit 1
fi

tb="${pkg}-${ver}.tar.gz"
sources_dir="${MONITOR_DIR}/rpmbuild/SOURCES"
specs_dir="${MONITOR_DIR}/rpmbuild/SPECS"
topdir="${MONITOR_DIR}/rpmbuild"
static_prefix="${topdir}/static-prefix"
static_srcdir="${topdir}/static-src"

mkdir -p \
  "${sources_dir}" \
  "${specs_dir}" \
  "${topdir}/BUILD" \
  "${topdir}/RPMS" \
  "${topdir}/SRPMS" \
  "${topdir}/BUILDROOT"

cp -f "${SPEC_SRC}" "${specs_dir}/hpcperfstats.spec"
echo "Spec installed: ${specs_dir}/hpcperfstats.spec"

echo "Building pinned static deps into ${static_prefix} (build_static_bundle.sh --deps-only) ..."
export PREFIX="${static_prefix}"
export SRCDIR="${static_srcdir}"
mkdir -p "${PREFIX}/include" "${PREFIX}/lib" "${PREFIX}/lib64" "${PREFIX}/lib/pkgconfig"
(cd "${MONITOR_DIR}" && ./scripts/build_static_bundle.sh --deps-only)

export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PREFIX}/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"

echo "Building ${tb} from ${MONITOR_DIR} (make distclean; configure; make dist) ..."
if test -f "${MONITOR_DIR}/Makefile"; then
  echo "Running make distclean ..."
  (cd "${MONITOR_DIR}" && make distclean)
fi

# Regenerate Makefile.in etc. from *.am even when configure exists. Otherwise
# make dist can fail (e.g. stale tests/Makefile.in still listing
# test_monitor_configure_help.sh while Makefile.am ships .sh.in only).
echo "Running autoreconf -fi in ${MONITOR_DIR} ..."
(cd "${MONITOR_DIR}" && autoreconf -fi)

if ! (cd "${MONITOR_DIR}" && ./configure --with-systemduserunitdir=no); then
  cat <<EOF >&2
configure failed while running make dist for ${tb}.
Static libraries were expected under ${PREFIX} (from build_static_bundle.sh --deps-only).
Check the bundle script output, network access for pinned tarballs, and host toolchain.
EOF
  exit 1
fi

(cd "${MONITOR_DIR}" && make dist)

if test ! -f "${MONITOR_DIR}/${tb}"; then
  echo "make dist did not produce ${MONITOR_DIR}/${tb}" >&2
  exit 1
fi

cp -f "${MONITOR_DIR}/${tb}" "${sources_dir}/${tb}"
echo "Source tarball: ${sources_dir}/${tb}"
echo "RPM tree ready under ${topdir}"
echo ""
echo "Build binary and source RPMs with:"
echo "  rpmbuild -ba --define \"_topdir ${topdir}\" \"${specs_dir}/hpcperfstats.spec\""
