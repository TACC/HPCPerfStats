#!/bin/sh
# Regression: static bundle and cross-compile scripts pin the same LIKWID release.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
bundle="${ROOT}/scripts/build_static_bundle.sh"
cross="${ROOT}/scripts/cross_compile_test.sh"

grep -q 'STATIC_PIN_LIKWID_VERSION="5.5.2"' "${bundle}" \
  || { echo "build_static_bundle.sh must pin STATIC_PIN_LIKWID_VERSION=5.5.2" >&2; exit 1; }
grep -q 'LIKWID_TAG="${LIKWID_TAG:-5.5.2}"' "${cross}" \
  || { echo "cross_compile_test.sh default LIKWID_TAG must match STATIC_PIN_LIKWID_VERSION" >&2; exit 1; }
grep -q '5\.5\.2rc' "${bundle}" "${cross}" \
  && { echo "LIKWID pin must not remain a 5.5.2rc* prerelease" >&2; exit 1; }

echo "test_likwid_static_pin passed"
