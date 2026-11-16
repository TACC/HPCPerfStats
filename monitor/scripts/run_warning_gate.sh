#!/usr/bin/env bash
# Compiler-warning gate for monitor C (pre-commit / pre-push hook entry point).
#
# Builds hpcperfstatsd (and, in --push mode, every make-check program) with
#   -Wall -Wextra -Werror
# against the pinned static prefix, in dedicated cached trees under
# monitor/.build-warn/<variant>/ (never .build-static, so it cannot clobber a
# verify build another shell is using).
#
# Usage (from anywhere):
#   scripts/run_warning_gate.sh            # same as --commit
#   scripts/run_warning_gate.sh --commit   # default config, daemon only (~25s cold, seconds warm)
#   scripts/run_warning_gate.sh --push     # default + DEBUG/shared-node configs, daemon + tests,
#                                          # plus the unreachable-function check below
#   scripts/run_warning_gate.sh --parse-gc-log FILE
#                                          # print "object function" pairs from a
#                                          # ld --print-gc-sections log (used by tests)
#
# Unreachable-function check (--push): links the daemon at -O0 with
# -ffunction-sections -Wl,--gc-sections -Wl,--print-gc-sections. Every .text.<fn>
# section the linker drops from an hpcperfstatsd-*.o object is a non-static
# function the daemon never reaches. Names listed in
# scripts/unreachable_functions.allowlist (test-only hooks, parsers reached only from
# tests, other-config paths) are accepted; anything new fails the gate. Remove a
# name from the allowlist when you delete or start calling that function.
#
# Environment:
#   PREFIX                  Static deps prefix (default: <workspace>/.build/prefix-static)
#   JOBS                    Parallel make jobs (default: nproc)
#   HPC_WARN_GATE_REQUIRE   1 = fail (not skip) when the host cannot run the gate
#   HPC_WARN_GATE_LOG       Log path (default: <workspace>/test_runs/warning-gate-<mode>.log)
#
# SKIPS: the gate needs Linux, gcc, autoreconf and the static prefix (libev.a,
# librabbitmq.a, plus liblikwid.a on x86). Without them it prints a bold SKIPPED
# banner and exits 0, so commits from such hosts (macOS checkouts, fresh clones) are
# NOT warning-checked. Set HPC_WARN_GATE_REQUIRE=1 to make a missing prerequisite
# fail instead.
#
# Clean-workspace policy (monitor-build-clean-workspace.mdc): this script keeps its
# trees between runs on purpose (incremental builds keep the hook fast). A variant
# tree is removed and reconfigured whenever its fingerprint changes (configure.ac,
# any Makefile.am, configure flags, compiler flags, this script, the prefix
# archives), so a stale configuration never feeds a build. monitor_tree_clean.sh
# helpers target .build-static / dist trees and do not apply here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly MONITOR_DIR
REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"
readonly REPO_ROOT
readonly ALLOWLIST="${SCRIPT_DIR}/unreachable_functions.allowlist"
readonly WARN_FLAGS="-Wall -Wextra -Werror"

PREFIX="${PREFIX:-${REPO_ROOT}/.build/prefix-static}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
GATE_ROOT="${MONITOR_DIR}/.build-warn"

# Print "<object> <function>" for each function section ld dropped from a daemon
# object. GCC clones (foo.part.0, foo.constprop.0, ...) are reported by base name.
parse_gc_log() {
  sed -n -E "s/.*removing unused section '\.text\.([^']+)' in file '([^']*\/)?(hpcperfstatsd-[^']+\.o)'.*/\3 \1/p" "$1" \
    | sed -E 's/ ([A-Za-z_][A-Za-z0-9_]*)\..*$/ \1/' \
    | sort -u
}

# Allowlist names, comments and blank lines stripped.
allowlist_names() {
  sed -e 's/#.*//' -e 's/[[:space:]]//g' -e '/^$/d' "${ALLOWLIST}" | sort -u
}

if test "${1:-}" = "--parse-gc-log"; then
  test $# -eq 2 || {
    echo "usage: $0 --parse-gc-log FILE" >&2
    exit 2
  }
  parse_gc_log "$2"
  exit 0
fi

MODE=commit
case "${1:-}" in
  "" | --commit) MODE=commit ;;
  --push) MODE=push ;;
  -h | --help)
    sed -n '2,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "usage: $0 [--commit|--push|--parse-gc-log FILE]" >&2
    exit 2
    ;;
esac

LOG="${HPC_WARN_GATE_LOG:-${REPO_ROOT}/test_runs/warning-gate-${MODE}.log}"
mkdir -p "$(dirname "${LOG}")"
: >"${LOG}"

log() {
  printf '%s\n' "$*" | tee -a "${LOG}" >&2
}

# Loud, unmissable notice: the hook passes but nothing was checked.
skip_or_fail() {
  local reason="$1"
  local bold="" reset=""
  if test -t 2 || test -n "${PRE_COMMIT:-}"; then
    bold=$'\033[1;33m'
    reset=$'\033[0m'
  fi
  {
    printf '%s\n' "${bold}################################################################${reset}"
    printf '%s\n' "${bold}##  MONITOR WARNING GATE SKIPPED -- C CODE WAS NOT CHECKED${reset}"
    printf '%s\n' "${bold}##  reason: ${reason}${reset}"
    printf '%s\n' "${bold}##  Commit monitor C from the Linux dev host, or set${reset}"
    printf '%s\n' "${bold}##  HPC_WARN_GATE_REQUIRE=1 to fail instead of skipping.${reset}"
    printf '%s\n' "${bold}################################################################${reset}"
  } | tee -a "${LOG}" >&2
  if test "${HPC_WARN_GATE_REQUIRE:-0}" = "1"; then
    exit 1
  fi
  exit 0
}

preflight() {
  test "$(uname -s)" = "Linux" || skip_or_fail "not Linux ($(uname -s))"
  command -v gcc >/dev/null 2>&1 || skip_or_fail "gcc not on PATH"
  command -v autoreconf >/dev/null 2>&1 || skip_or_fail "autoreconf not on PATH"
  local lib
  local -a need=(libev.a librabbitmq.a)
  case "$(uname -m)" in
    x86_64 | i?86) need+=(liblikwid.a) ;;
  esac
  for lib in "${need[@]}"; do
    test -f "${PREFIX}/lib/${lib}" || test -f "${PREFIX}/lib64/${lib}" \
      || skip_or_fail "${lib} missing under PREFIX=${PREFIX}"
  done
}

# -isystem for vendored headers (and PREFIX/include, e.g. libev ev.h strict-aliasing
# at -O2) so third-party warnings do not fail the gate; GCC ignores a later -I for a
# directory already given as -isystem.
third_party_isystem() {
  local d out=""
  for d in "${MONITOR_DIR}"/third_party/*/; do
    test -d "${d}" && out+=" -isystem ${d%/}"
  done
  printf '%s' "${out}"
}

feat_flags() {
  PREFIX="${PREFIX}" "${SCRIPT_DIR}/build_static_bundle.sh" --print-configure-flags 2>/dev/null || true
}

fingerprint() {
  {
    printf '%s\n' "$@"
    cat "${MONITOR_DIR}/configure.ac" "${BASH_SOURCE[0]}"
    find "${MONITOR_DIR}" -name Makefile.am -not -path '*/.build*' -print0 | sort -z | xargs -0 cat
    ls -l --time-style=+%s "${PREFIX}"/lib*/lib*.a 2>/dev/null || true
  } | sha256sum | cut -d' ' -f1
}

ensure_configure_script() {
  if test ! -x "${MONITOR_DIR}/configure" \
    || test "${MONITOR_DIR}/configure.ac" -nt "${MONITOR_DIR}/configure"; then
    log "autoreconf -fi (${MONITOR_DIR})"
    (cd "${MONITOR_DIR}" && autoreconf -fi) >>"${LOG}" 2>&1
  fi
}

# configure_variant <name> <cflags> <ldflags-extra> [configure args...]
configure_variant() {
  local name="$1" cflags="$2" ldextra="$3"
  shift 3
  local dir="${GATE_ROOT}/${name}"
  local -a cfg=(--enable-all-static --with-systemduserunitdir=no --with-cpu-counter-backend=auto)
  local f
  while IFS= read -r f; do
    test -n "${f}" && cfg+=("${f}")
  done < <(feat_flags)
  cfg+=("$@")
  local fp
  fp="$(fingerprint "${name}" "${cflags}" "${ldextra}" "${WARN_FLAGS}" "${cfg[@]}")"
  if test -f "${dir}/.gate-fingerprint" && test "$(cat "${dir}/.gate-fingerprint")" = "${fp}" \
    && test -f "${dir}/Makefile"; then
    return 0
  fi
  log "[${name}] (re)configuring clean tree ${dir}"
  rm -rf "${dir}"
  mkdir -p "${dir}"
  ensure_configure_script
  if ! (cd "${dir}" \
    && CPPFLAGS="-isystem ${PREFIX}/include$(third_party_isystem)" \
      LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${ldextra}" \
      CFLAGS="${cflags}" \
      "${MONITOR_DIR}/configure" "${cfg[@]}") >>"${LOG}" 2>&1; then
    log "[${name}] configure FAILED; see ${LOG}"
    exit 1
  fi
  printf '%s\n' "${fp}" >"${dir}/.gate-fingerprint"
}

# make_variant <name> <full CFLAGS> [make args...] -- build; print diagnostics on failure.
make_variant() {
  local name="$1" cflags="$2"
  shift 2
  local dir="${GATE_ROOT}/${name}"
  local out="${dir}/.gate-make.log"
  # -k: report every failing file in one run, not just the first.
  if make -k -C "${dir}" -j"${JOBS}" CFLAGS="${cflags}" "$@" >"${out}" 2>&1; then
    cat "${out}" >>"${LOG}"
    return 0
  fi
  cat "${out}" >>"${LOG}"
  log "[${name}] FAILED (CFLAGS=${cflags}) diagnostics:"
  grep -E "(error|warning):" "${out}" | sort -u | sed 's/^/  /' >&2 || tail -40 "${out}" >&2
  log "full log: ${LOG}"
  return 1
}

check_programs() {
  local dir="${GATE_ROOT}/$1"
  # shellcheck disable=SC2016
  make -s -C "${dir}/tests" -f Makefile -f <(printf 'gate-print-check-programs:\n\t@echo $(check_PROGRAMS)\n') \
    gate-print-check-programs
}

# build_variant <name> <cflags> <with-tests 0|1> [configure args...]
build_variant() {
  local name="$1" cflags="$2" with_tests="$3"
  shift 3
  configure_variant "${name}" "${cflags}" "" "$@"
  local brc=0
  log "[${name}] daemon: make CFLAGS='${cflags} ${WARN_FLAGS}'"
  make_variant "${name}" "${cflags} ${WARN_FLAGS}" || brc=1
  if test "${with_tests}" = "1"; then
    local -a progs
    # Test programs need src/ generated headers; build those even if the daemon failed.
    make -C "${GATE_ROOT}/${name}/src" monitor_capability_slug.h >>"${LOG}" 2>&1 || true
    read -r -a progs <<<"$(check_programs "${name}")"
    log "[${name}] ${#progs[@]} make-check programs"
    make_variant "${name}" "${cflags} ${WARN_FLAGS}" -C tests "${progs[@]}" || brc=1
  fi
  return "${brc}"
}

unreachable_check() {
  local name=gc cflags="-O0 -ffunction-sections"
  local dir="${GATE_ROOT}/${name}"
  configure_variant "${name}" "${cflags}" "-Wl,--gc-sections -Wl,--print-gc-sections"
  rm -f "${dir}/src/hpcperfstatsd"
  log "[${name}] link daemon with --gc-sections --print-gc-sections (reachability only, no -Werror)"
  make_variant "${name}" "${cflags}" || return 1
  local found new stale
  # Keep only global (nm "T") symbols: at -O0 unused static helpers from headers also
  # get dropped per object; those are not dead code (-Wunused-function covers statics).
  found="$(comm -12 \
    <(parse_gc_log "${dir}/.gate-make.log" | cut -d' ' -f2 | sort -u) \
    <(nm --defined-only "${dir}"/src/hpcperfstatsd-*.o 2>/dev/null | awk '$2 == "T" { print $3 }' | sort -u) \
    | sed '/^$/d')"
  new="$(comm -23 <(printf '%s\n' "${found}" | sed '/^$/d') <(allowlist_names))"
  if test -n "${new}"; then
    log "[${name}] FAILED: non-static functions the daemon never reaches (not in $(basename "${ALLOWLIST}")):"
    printf '%s\n' "${new}" | sed 's/^/  /' | tee -a "${LOG}" >&2
    log "Delete them, make them static, call them, or (test-only / other-config) add them to the allowlist."
    return 1
  fi
  stale="$(comm -13 <(printf '%s\n' "${found}" | sed '/^$/d') <(allowlist_names))"
  if test -n "${stale}"; then
    log "[${name}] note: allowlist entries no longer unreachable (safe to remove):"
    printf '%s\n' "${stale}" | sed 's/^/  /' | tee -a "${LOG}" >&2
  fi
  log "[${name}] OK ($(printf '%s\n' "${found}" | sed '/^$/d' | wc -l) allowlisted unreachable functions)"
}

preflight
log "=== run_warning_gate.sh --${MODE} $(date -Is) PREFIX=${PREFIX} ==="
rc=0
case "${MODE}" in
  commit)
    build_variant default "-O2" 0 || rc=1
    ;;
  push)
    build_variant default "-O2" 1 || rc=1
    build_variant debug "-O2" 1 --enable-debug --enable-shared-node-multi-jid || rc=1
    unreachable_check || rc=1
    ;;
esac
if test "${rc}" = "0"; then
  log "=== monitor warning gate (--${MODE}) passed ==="
fi
exit "${rc}"
