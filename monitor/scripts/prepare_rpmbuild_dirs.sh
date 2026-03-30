#!/usr/bin/env bash
# Create ./rpmbuild/* under the monitor directory, copy hpcperfstats.spec into SPECS,
# and build hpcperfstats-<Version>.tar.gz from this checkout via make dist (no external
# tarball download — the tree containing this script is the package source).
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

mkdir -p \
  "${sources_dir}" \
  "${specs_dir}" \
  "${topdir}/BUILD" \
  "${topdir}/RPMS" \
  "${topdir}/SRPMS" \
  "${topdir}/BUILDROOT"

cp -f "${SPEC_SRC}" "${specs_dir}/hpcperfstats.spec"
echo "Spec installed: ${specs_dir}/hpcperfstats.spec"

echo "Building ${tb} from ${MONITOR_DIR} (make dist) ..."
if test ! -f "${MONITOR_DIR}/Makefile"; then
  if test ! -f "${MONITOR_DIR}/configure"; then
    (cd "${MONITOR_DIR}" && autoreconf -fi)
  fi
  if ! (cd "${MONITOR_DIR}" && ./configure --with-systemduserunitdir=no); then
    cat <<EOF >&2
configure failed while running make dist for ${tb}.
Install BuildRequires from hpcperfstats.spec (e.g. rabbitmq-c / librabbitmq devel), then re-run.
EOF
    exit 1
  fi
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
