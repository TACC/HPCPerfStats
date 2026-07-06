#!/bin/sh
# Regression: prepare/build scripts use shared monitor_tree_clean.sh pre-compile cleanup.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -f scripts/lib/monitor_tree_clean.sh \
  || { echo "missing scripts/lib/monitor_tree_clean.sh" >&2; exit 1; }

grep -q 'monitor_tree_clean.sh' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must source monitor_tree_clean.sh" >&2; exit 1; }
grep -q 'monitor_tree_clean_pre_dist' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must call monitor_tree_clean_pre_dist" >&2; exit 1; }
grep -q 'monitor_tree_clean_post_dist' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must call monitor_tree_clean_post_dist" >&2; exit 1; }

grep -q 'monitor_tree_clean.sh' scripts/build_static_bundle.sh \
  || { echo "build_static_bundle.sh must source monitor_tree_clean.sh" >&2; exit 1; }
grep -q 'monitor_tree_clean_build_static' scripts/build_static_bundle.sh \
  || { echo "build_static_bundle.sh must call monitor_tree_clean_build_static" >&2; exit 1; }

grep -q 'verify_dist_tarball_host_headers' scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must verify host_*.h in dist tarball" >&2; exit 1; }

# Functional: pre_dist removes generated paths from a fake monitor tree.
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/hps_tree_clean.XXXXXX")"
trap 'rm -rf "${tmpdir}"' EXIT INT HUP TERM
mkdir -p "${tmpdir}/.build-static" "${tmpdir}/autom4te.cache"
touch "${tmpdir}/stale.tar.gz"
# shellcheck source=../scripts/lib/monitor_tree_clean.sh
. "${ROOT}/scripts/lib/monitor_tree_clean.sh"
monitor_tree_clean_pre_dist "${tmpdir}" "stale.tar.gz"
test ! -d "${tmpdir}/.build-static" \
  || { echo "pre_dist should remove .build-static" >&2; exit 1; }
test ! -d "${tmpdir}/autom4te.cache" \
  || { echo "pre_dist should remove autom4te.cache" >&2; exit 1; }
test ! -f "${tmpdir}/stale.tar.gz" \
  || { echo "pre_dist should remove named tarball" >&2; exit 1; }

echo "test_prepare_monitor_tree_clean passed"
