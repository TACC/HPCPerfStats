#!/usr/bin/env bash
# Install static archives (libev, rabbitmq-c, LIKWID) into <repo>/.build/prefix
# so monitor default --enable-all-static can link (-llikwid -llikwid-lua ...).
#
# Reuses trees under <repo>/.build/src where possible; may download tarballs if
# missing. See build_static_bundle.sh for pins and env overrides.
#
set -euo pipefail
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"

export PREFIX="${PREFIX:-${REPO_ROOT}/.build/prefix}"
export SRCDIR="${SRCDIR:-${REPO_ROOT}/.build/src}"
export JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"

mkdir -p "${PREFIX}/include" "${PREFIX}/lib" "${PREFIX}/lib64" "${PREFIX}/lib/pkgconfig"

exec "${SCRIPT_DIR}/build_static_bundle.sh" --deps-only "$@"
