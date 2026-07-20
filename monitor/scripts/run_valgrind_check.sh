#!/usr/bin/env bash
# Valgrind Memcheck verify path for monitor unit/contract tests.
# Additive to dual-verify (build_static_bundle.sh + cross_compile_test.sh); does not replace them.
#
# Usage (from HPCPerfStats/monitor or via absolute path):
#   ./scripts/run_valgrind_check.sh
#
# Environment:
#   PREFIX          Static/shared deps prefix (default: <repo>/.build/prefix-static)
#   SKIP_DEPS       If 1, do not run build_static_bundle.sh --deps-only
#   SKIP_CLEAN      If 1, keep existing .build-valgrind tree
#   SKIP_DISTCLEAN  If 1, leave .build-valgrind after success (default: distclean)
#   JOBS            Parallel make jobs (default: nproc)
#   VALGRIND_LOG    Override log path (default: <workspace>/test_runs/valgrind-check-YYYY-MM-DD.log)
#
# Configure uses --enable-all-static (same as canonical static bundle) with -g -O1.
# check_PROGRAMS run under Valgrind via Automake LOG_COMPILER; check-local is unwrapped.
# On TACC: module load valgrind (after fixing MODULEPATH — tacc-lmod-build-environment).
#
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"
# shellcheck source=lib/monitor_tree_clean.sh
source "${SCRIPT_DIR}/lib/monitor_tree_clean.sh"

PREFIX="${PREFIX:-${REPO_ROOT}/.build/prefix-static}"
SKIP_DEPS="${SKIP_DEPS:-0}"
SKIP_CLEAN="${SKIP_CLEAN:-0}"
SKIP_DISTCLEAN="${SKIP_DISTCLEAN:-0}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
BUILD_DIR="${MONITOR_DIR}/.build-valgrind"
SUPP_FILE="${SCRIPT_DIR}/valgrind.supp"
DATE_STAMP="$(date +%Y-%m-%d)"
VALGRIND_LOG="${VALGRIND_LOG:-${REPO_ROOT}/test_runs/valgrind-check-${DATE_STAMP}.log}"

mkdir -p "$(dirname "${VALGRIND_LOG}")"

log() {
  # shellcheck disable=SC2312
  printf '%s\n' "$*" | tee -a "${VALGRIND_LOG}"
}

die() {
  log "error: $*"
  exit 1
}

valgrind_preflight() {
  if ! command -v valgrind >/dev/null 2>&1; then
    die "valgrind not found on PATH. On TACC: fix MODULEPATH then module load valgrind (see tacc-lmod-build-environment.mdc / monitor-valgrind-cpp-linter-gate.mdc)."
  fi
  log "valgrind: $(command -v valgrind) ($(valgrind --version 2>/dev/null | head -1))"
  test -f "${SUPP_FILE}" || die "missing suppressions file: ${SUPP_FILE}"
}

monitor_tree_clean_build_valgrind() {
  if test "${SKIP_CLEAN}" = "1"; then
    return 0
  fi
  if test -d "${BUILD_DIR}"; then
    log "Removing prior Valgrind build tree: ${BUILD_DIR}"
    rm -rf "${BUILD_DIR}"
  fi
  # Drop leftover ASan tree from the previous gate if present.
  if test -d "${MONITOR_DIR}/.build-asan"; then
    log "Removing stale ASan build tree: ${MONITOR_DIR}/.build-asan"
    rm -rf "${MONITOR_DIR}/.build-asan"
  fi
}

ensure_prefix() {
  if test "${SKIP_DEPS}" = "1"; then
    test -d "${PREFIX}" || die "SKIP_DEPS=1 but PREFIX missing: ${PREFIX}"
    return 0
  fi
  if test -f "${PREFIX}/lib/libev.a" || test -f "${PREFIX}/lib64/libev.a"; then
    log "PREFIX already has libev.a; skipping deps build (${PREFIX})"
    return 0
  fi
  log "Building static deps into PREFIX=${PREFIX}"
  PREFIX="${PREFIX}" "${SCRIPT_DIR}/build_static_bundle.sh" --deps-only
}

collect_feat_flags() {
  local line
  while IFS= read -r line; do
    test -n "${line}" || continue
    printf '%s\n' "${line}"
  done < <(PREFIX="${PREFIX}" "${SCRIPT_DIR}/build_static_bundle.sh" --print-configure-flags 2>/dev/null)
}

: >"${VALGRIND_LOG}"
log "=== run_valgrind_check.sh $(date -Is) ==="
log "MONITOR_DIR=${MONITOR_DIR}"
log "BUILD_DIR=${BUILD_DIR}"
log "PREFIX=${PREFIX}"
log "log=${VALGRIND_LOG}"

valgrind_preflight
ensure_prefix
monitor_tree_clean_build_valgrind

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

if test -f "${MONITOR_DIR}/configure.ac" || test -f "${MONITOR_DIR}/configure.in"; then
  log "Regenerating Autotools: autoreconf -fi"
  (cd "${MONITOR_DIR}" && autoreconf -fi)
fi

export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"
export CFLAGS="-g -O1 ${CFLAGS:-}"
export CXXFLAGS="-g -O1 ${CXXFLAGS:-}"

mapfile -t FEAT_FLAGS < <(collect_feat_flags || true)
CFG=(
  --enable-all-static
  --with-systemduserunitdir=no
  --with-cpu-counter-backend=auto
  "${FEAT_FLAGS[@]}"
)

log "configure ${CFG[*]}"
"${MONITOR_DIR}/configure" "${CFG[@]}"

log "make -j${JOBS}"
make -j"${JOBS}"

# Automake wraps check_PROGRAMS with LOG_COMPILER; check-local shell/Python stay unwrapped.
VG_FLAGS=(
  --tool=memcheck
  --error-exitcode=1
  --leak-check=full
  --errors-for-leak-kinds=definite
  --track-origins=yes
  "--suppressions=${SUPP_FILE}"
)
log "make check LOG_COMPILER=valgrind LOG_FLAGS=${VG_FLAGS[*]}"
make -j"${JOBS}" check \
  LOG_COMPILER=valgrind \
  LOG_FLAGS="${VG_FLAGS[*]}"

if test "${SKIP_DISTCLEAN}" != "1"; then
  log "make distclean (monitor-post-verify-distclean)"
  make distclean
else
  log "SKIP_DISTCLEAN=1; leaving ${BUILD_DIR}"
fi

log "=== run_valgrind_check.sh OK ==="
echo "Valgrind Memcheck passed; log: ${VALGRIND_LOG}"
