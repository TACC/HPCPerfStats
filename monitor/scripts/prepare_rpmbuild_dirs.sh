#!/usr/bin/env bash
# Create ./rpmbuild/* under the monitor directory and copy hpcperfstats.spec into SPECS.
#
# This script does not download, build static dependencies, or run configure/make.
# Pinned libev / rabbitmq-c / LIKWID fetch and build, plus the daemon link, run only
# during rpmbuild inside hpcperfstats.spec (%%build: scripts/build_static_bundle.sh).
#
# Before rpmbuild -ba, place the source tarball in rpmbuild/SOURCES. It must match
# Source: in the spec (hpcperfstats-<Version>.tar.gz from configure.ac AC_INIT). For
# example, from this checkout:
#   autoreconf -fi && ./configure --with-systemduserunitdir=no && make dist
#   cp hpcperfstats-<version>.tar.gz rpmbuild/SOURCES/
#
# Usage (from anywhere; paths are anchored to HPCPerfStats/monitor):
#   ./scripts/prepare_rpmbuild_dirs.sh
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
  tests/README.md
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

mkdir -p \
  "${sources_dir}" \
  "${specs_dir}" \
  "${topdir}/BUILD" \
  "${topdir}/RPMS" \
  "${topdir}/SRPMS" \
  "${topdir}/BUILDROOT"

cp -f "${SPEC_SRC}" "${specs_dir}/hpcperfstats.spec"
echo "Spec installed: ${specs_dir}/hpcperfstats.spec"

if test -f "${MONITOR_DIR}/${tb}"; then
  cp -f "${MONITOR_DIR}/${tb}" "${sources_dir}/${tb}"
  echo "Source tarball copied: ${sources_dir}/${tb}"
else
  echo "Note: ${MONITOR_DIR}/${tb} not found — copy it to ${sources_dir}/ before rpmbuild -ba."
  echo "  Example: (autoreconf -fi && ./configure --with-systemduserunitdir=no && make dist) && cp ${tb} ${sources_dir}/"
fi

echo "RPM tree ready under ${topdir}"
echo ""
echo "Build binary and source RPMs with:"
echo "  rpmbuild -ba --define \"_topdir ${topdir}\" \"${specs_dir}/hpcperfstats.spec\""
