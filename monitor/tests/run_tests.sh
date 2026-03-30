#!/usr/bin/env bash
# Run the full monitor test suite (same as top-level `make check` in a build tree).
#
# Usage:
#   ./tests/run_tests.sh [BUILD_DIR]
#
# BUILD_DIR defaults to <monitor>/.build-static (the canonical static-bundle tree).
#
# Requires an already-configured build directory (e.g. from
# scripts/build_static_bundle.sh or manual configure && make).
set -euo pipefail

MONITOR_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-${MONITOR_ROOT}/.build-static}"

if [[ ! -f "${BUILD_DIR}/Makefile" ]]; then
  echo "error: no Makefile in ${BUILD_DIR}; configure the monitor there first." >&2
  echo "  Example: ${MONITOR_ROOT}/scripts/build_static_bundle.sh" >&2
  exit 1
fi

exec make -C "${BUILD_DIR}" check
