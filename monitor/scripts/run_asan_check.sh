#!/usr/bin/env bash
# AddressSanitizer (ASan) verify path for monitor unit/contract tests.
# Additive to dual-verify (build_static_bundle.sh + cross_compile_test.sh); does not replace them.
#
# Usage (from HPCPerfStats/monitor or via absolute path):
#   ./scripts/run_asan_check.sh
#
# Environment:
#   PREFIX          Static/shared deps prefix (default: <repo>/.build/prefix-static)
#   SKIP_DEPS       If 1, do not run build_static_bundle.sh --deps-only
#   SKIP_CLEAN      If 1, keep existing .build-asan tree
#   SKIP_DISTCLEAN  If 1, leave .build-asan after success (default: distclean)
#   JOBS            Parallel make jobs (default: nproc)
#   ASAN_LOG        Override log path (default: <workspace>/test_runs/asan-check-YYYY-MM-DD.log)
#
# Configure uses --disable-all-static so the ASan runtime can link cleanly.
# Requires a toolchain with libasan (on TACC: module load gcc/<ver> that ships libasan).
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
BUILD_DIR="${MONITOR_DIR}/.build-asan"
DATE_STAMP="$(date +%Y-%m-%d)"
ASAN_LOG="${ASAN_LOG:-${REPO_ROOT}/test_runs/asan-check-${DATE_STAMP}.log}"

mkdir -p "$(dirname "${ASAN_LOG}")"

log() {
  # shellcheck disable=SC2312
  printf '%s\n' "$*" | tee -a "${ASAN_LOG}"
}

die() {
  log "error: $*"
  exit 1
}

asan_runtime_preflight() {
  local overcommit=""
  if test -r /proc/sys/vm/overcommit_memory; then
    overcommit="$(cat /proc/sys/vm/overcommit_memory)"
  fi
  if test "${overcommit}" = "2"; then
    die "vm.overcommit_memory=2 — AddressSanitizer cannot reserve shadow memory on this host. Run on a host with overcommit enabled (0 or 1), or ask an admin to allow overcommit for ASan CI nodes. See monitor-asan-cpp-linter-gate.mdc."
  fi

  local probe_c probe_bin
  probe_c="$(mktemp "${TMPDIR:-/tmp}/asan_probe.XXXXXX.c")"
  probe_bin="$(mktemp "${TMPDIR:-/tmp}/asan_probe.XXXXXX")"
  rm -f "${probe_bin}"
  printf 'int main(void){return 0;}\n' >"${probe_c}"
  if ! "${CC:-gcc}" -fsanitize=address -fno-omit-frame-pointer -g -o "${probe_bin}" "${probe_c}" 2>>"${ASAN_LOG}"; then
    rm -f "${probe_c}" "${probe_bin}"
    die "cannot link with -fsanitize=address (install libasan / module load a gcc that provides it). See log: ${ASAN_LOG}"
  fi
  if ! "${probe_bin}" >>"${ASAN_LOG}" 2>&1; then
    rm -f "${probe_c}" "${probe_bin}"
    die "ASan runtime probe failed (shadow map / ulimit). See log: ${ASAN_LOG}"
  fi
  rm -f "${probe_c}" "${probe_bin}"
}

monitor_tree_clean_build_asan() {
  if test "${SKIP_CLEAN}" = "1"; then
    return 0
  fi
  if test -d "${BUILD_DIR}"; then
    log "Removing prior ASan build tree: ${BUILD_DIR}"
    rm -rf "${BUILD_DIR}"
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
  local -a flags=()
  local line
  while IFS= read -r line; do
    test -n "${line}" || continue
    flags+=("${line}")
  done < <(PREFIX="${PREFIX}" "${SCRIPT_DIR}/build_static_bundle.sh" --print-configure-flags 2>/dev/null)
  if test "${#flags[@]}" -gt 0; then
    printf '%s\n' "${flags[@]}"
  fi
}

: >"${ASAN_LOG}"
log "=== run_asan_check.sh $(date -Is) ==="
log "MONITOR_DIR=${MONITOR_DIR}"
log "BUILD_DIR=${BUILD_DIR}"
log "PREFIX=${PREFIX}"
log "log=${ASAN_LOG}"

asan_runtime_preflight
ensure_prefix
monitor_tree_clean_build_asan

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

if test -f "${MONITOR_DIR}/configure.ac" || test -f "${MONITOR_DIR}/configure.in"; then
  log "Regenerating Autotools: autoreconf -fi"
  (cd "${MONITOR_DIR}" && autoreconf -fi)
fi

export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"
export CFLAGS="-fsanitize=address -fno-omit-frame-pointer -g -O1 ${CFLAGS:-}"
export CXXFLAGS="-fsanitize=address -fno-omit-frame-pointer -g -O1 ${CXXFLAGS:-}"
export LDFLAGS="-fsanitize=address ${LDFLAGS}"

mapfile -t FEAT_FLAGS < <(collect_feat_flags || true)
CFG=(
  --disable-all-static
  --with-systemduserunitdir=no
  --with-cpu-counter-backend=auto
  "${FEAT_FLAGS[@]}"
)

log "configure ${CFG[*]}"
"${MONITOR_DIR}/configure" "${CFG[@]}"

log "make -j${JOBS}"
make -j"${JOBS}"

log "make check"
make -j"${JOBS}" check

if test "${SKIP_DISTCLEAN}" != "1"; then
  log "make distclean (monitor-post-verify-distclean)"
  make distclean
else
  log "SKIP_DISTCLEAN=1; leaving ${BUILD_DIR}"
fi

log "=== run_asan_check.sh OK ==="
echo "ASan check passed; log: ${ASAN_LOG}"
