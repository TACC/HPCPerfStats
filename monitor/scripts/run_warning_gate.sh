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
#   scripts/run_warning_gate.sh --commit   # default x86 config, daemon only
#   scripts/run_warning_gate.sh --push     # + DEBUG/shared-node x86 config (daemon + make-check
#                                          #   programs), aarch64 Grace config (daemon, clang),
#                                          #   and the unreachable-function check below
#   scripts/run_warning_gate.sh --parse-gc-log FILE
#                                          # print "object function" pairs from a GNU ld or
#                                          # lld --print-gc-sections log (used by tests)
#   scripts/run_warning_gate.sh --dead-from-sets DIR
#                                          # print functions unreachable in every configuration
#                                          # from DIR/<variant>.{defined,unreachable} (tests)
#
# Variants (--push):
#   default  x86_64 gcc -O2, static-bundle configure flags (warnings: daemon + tests)
#   debug    x86_64 gcc -O2, + --enable-debug --enable-shared-node-multi-jid (daemon + tests)
#   arm      aarch64 clang -O2 + lld, Grace fleet flags (arm backend, shared-node,
#            --enable-ib-mad-dlopen), sysroot + aarch64 prefix (daemon only; compile + link,
#            nothing runs)
#   gc-*     the same three configurations at -O0 -ffunction-sections, linked with
#            --gc-sections --print-gc-sections (reachability only, no -Werror)
#
# Unreachable-function check (--push): a non-static function fails the gate only when it is
# unreachable from main() in EVERY configuration that compiles it (a helper only ARM, DEBUG
# or shared-node code calls is reachable, not dead). Names listed in
# scripts/unreachable_functions.allowlist (functions only tests call) are accepted. Allowlist
# entries that some configuration reaches, or that no configuration defines, are stale and
# printed as a note. Remove a name when you delete or start calling that function.
#
# Environment:
#   PREFIX                  x86 static deps prefix (default: <workspace>/.build/prefix-static)
#   ARM_SYSROOT             aarch64 sysroot (default: <workspace>/.build/sysroots/rocky9-aarch64-linux-gnu)
#   ARM_PREFIX              aarch64 deps prefix (default: <workspace>/.build/prefix-qemu-AARCH64_LINUX_GNU)
#   JOBS                    Parallel make jobs (default: nproc)
#   HPC_WARN_GATE_REQUIRE   1 = fail (not skip) when the host cannot run the gate
#   HPC_WARN_GATE_LOG       Log path (default: <workspace>/test_runs/warning-gate-<mode>.log)
#
# SKIPS: the gate needs Linux, gcc, autoreconf and the static prefix (libev.a,
# librabbitmq.a). Without them it prints a bold SKIPPED banner
# and exits 0, so commits from such hosts (macOS checkouts, fresh clones) are NOT
# warning-checked. --push also needs clang, ld.lld, the aarch64 sysroot and prefix
# (sysroot and prefix come from scripts/cross_compile_test.sh); without those the x86
# variants still run and only the aarch64 variant and the unreachable-function check are
# skipped, behind the same bold banner. Set HPC_WARN_GATE_REQUIRE=1 to make any missing
# prerequisite fail instead.
#
# Clean-workspace policy (monitor-build-clean-workspace.mdc): this script keeps its
# trees between runs on purpose (incremental builds keep the hook fast). A variant
# tree is removed and reconfigured whenever its fingerprint changes (configure.ac,
# any Makefile.am, configure flags, compiler, compiler flags, this script, the prefix
# archives), so a stale configuration never feeds a build. Do not run make in
# .build-warn/ by hand: objects built without the gate's CFLAGS look up to date to it.
# monitor_tree_clean.sh helpers target .build-static / dist trees and do not apply here.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly MONITOR_DIR
REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"
readonly REPO_ROOT
readonly ALLOWLIST="${SCRIPT_DIR}/unreachable_functions.allowlist"
readonly WARN_FLAGS="-Wall -Wextra -Werror"
readonly GC_CFLAGS="-O0 -ffunction-sections"
readonly GC_LDFLAGS="-Wl,--gc-sections -Wl,--print-gc-sections"
readonly ARM_TRIPLET="aarch64-linux-gnu"

PREFIX="${PREFIX:-${REPO_ROOT}/.build/prefix-static}"
ARM_SYSROOT="${ARM_SYSROOT:-${REPO_ROOT}/.build/sysroots/rocky9-${ARM_TRIPLET}}"
ARM_PREFIX="${ARM_PREFIX:-${REPO_ROOT}/.build/prefix-qemu-AARCH64_LINUX_GNU}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
GATE_ROOT="${MONITOR_DIR}/.build-warn"

# Print "<object> <function>" for each function section the linker dropped from a daemon
# object. GNU ld: removing unused section '.text.F' in file 'dir/hpcperfstatsd-X.o'
# lld:            removing unused section dir/hpcperfstatsd-X.o:(.text.F)
# GCC/clang clones (F.part.0, F.constprop.0, ...) are reported by base name.
parse_gc_log() {
  sed -n -E \
    -e "s/.*removing unused section '\.text\.([^']+)' in file '([^']*\/)?(hpcperfstatsd-[^'/]+\.o)'.*/\3 \1/p" \
    -e "s/.*removing unused section ([^ ]*\/)?(hpcperfstatsd-[^:/ ]+\.o):\(\.text\.([^)]+)\).*/\2 \3/p" \
    "$1" \
    | sed -E 's/ ([A-Za-z_][A-Za-z0-9_]*)\..*$/ \1/' \
    | sort -u
}

# dead_functions <dir>: print functions unreachable in every configuration that defines
# them, from <dir>/<variant>.defined and <dir>/<variant>.unreachable (sorted name lists).
# Reached somewhere = defined minus unreachable in any one configuration.
dead_functions() {
  local dir="$1" f
  for f in "${dir}"/*.defined; do
    comm -23 "${f}" "${f%.defined}.unreachable"
  done | sort -u >"${dir}/.reached"
  cat "${dir}"/*.unreachable | sort -u | comm -23 - "${dir}/.reached"
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
if test "${1:-}" = "--dead-from-sets"; then
  if test $# -ne 2 || test ! -d "$2"; then
    echo "usage: $0 --dead-from-sets DIR (DIR/<variant>.defined + .unreachable)" >&2
    exit 2
  fi
  dead_functions "$2"
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
    echo "usage: $0 [--commit|--push|--parse-gc-log FILE|--dead-from-sets DIR]" >&2
    exit 2
    ;;
esac

LOG="${HPC_WARN_GATE_LOG:-${REPO_ROOT}/test_runs/warning-gate-${MODE}.log}"
mkdir -p "$(dirname "${LOG}")"
: >"${LOG}"

log() {
  printf '%s\n' "$*" | tee -a "${LOG}" >&2
}

# Loud, unmissable notice: skip_banner <what was not checked> <reason>.
skip_banner() {
  local what="$1" reason="$2"
  local bold="" reset=""
  if test -t 2 || test -n "${PRE_COMMIT:-}"; then
    bold=$'\033[1;33m'
    reset=$'\033[0m'
  fi
  {
    printf '%s\n' "${bold}################################################################${reset}"
    printf '%s\n' "${bold}##  MONITOR WARNING GATE SKIPPED -- ${what}${reset}"
    printf '%s\n' "${bold}##  reason: ${reason}${reset}"
    printf '%s\n' "${bold}##  Commit monitor C from the Linux dev host, or set${reset}"
    printf '%s\n' "${bold}##  HPC_WARN_GATE_REQUIRE=1 to fail instead of skipping.${reset}"
    printf '%s\n' "${bold}################################################################${reset}"
  } | tee -a "${LOG}" >&2
}

# The hook passes but nothing was checked (or fails with HPC_WARN_GATE_REQUIRE=1).
skip_or_fail() {
  skip_banner "C CODE WAS NOT CHECKED" "$1"
  if test "${HPC_WARN_GATE_REQUIRE:-0}" = "1"; then
    exit 1
  fi
  exit 0
}

# --push without the aarch64 toolchain: x86 variants still run; the aarch64 variant and
# the unreachable-function check (which needs all three configurations) are skipped.
ARM_READY=1
arm_skip_or_fail() {
  skip_banner "AARCH64 + UNREACHABLE-FUNCTION CHECKS NOT RUN" "$1"
  if test "${HPC_WARN_GATE_REQUIRE:-0}" = "1"; then
    exit 1
  fi
  ARM_READY=0
}

preflight() {
  test "$(uname -s)" = "Linux" || skip_or_fail "not Linux ($(uname -s))"
  command -v gcc >/dev/null 2>&1 || skip_or_fail "gcc not on PATH"
  command -v autoreconf >/dev/null 2>&1 || skip_or_fail "autoreconf not on PATH"
  local lib
  local -a need=(libev.a librabbitmq.a)
  for lib in "${need[@]}"; do
    test -f "${PREFIX}/lib/${lib}" || test -f "${PREFIX}/lib64/${lib}" \
      || skip_or_fail "${lib} missing under PREFIX=${PREFIX}"
  done
  if test "${MODE}" = "push"; then
    if ! command -v clang >/dev/null 2>&1; then
      arm_skip_or_fail "clang not on PATH"
    elif ! command -v ld.lld >/dev/null 2>&1; then
      arm_skip_or_fail "ld.lld not on PATH (dnf install lld)"
    elif test ! -e "${ARM_SYSROOT}/usr/lib64/crt1.o"; then
      arm_skip_or_fail "aarch64 sysroot missing: ARM_SYSROOT=${ARM_SYSROOT} (scripts/cross_compile_test.sh creates it)"
    else
      for lib in libev.a librabbitmq.a; do
        if test ! -f "${ARM_PREFIX}/lib/${lib}" && test ! -f "${ARM_PREFIX}/lib64/${lib}"; then
          arm_skip_or_fail "${lib} missing under ARM_PREFIX=${ARM_PREFIX}"
          break
        fi
      done
    fi
  fi
}

# -isystem for vendored headers (and the deps prefix, e.g. libev ev.h strict-aliasing at
# -O2) so third-party warnings do not fail the gate; GCC and clang ignore a later -I for a
# directory already given as -isystem.
isystem_flags() {
  local prefix="$1"
  local d out="-isystem ${prefix}/include"
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
    ls -l --time-style=+%s "${PREFIX}"/lib*/lib*.a "${ARM_PREFIX}"/lib*/lib*.a 2>/dev/null || true
  } | sha256sum | cut -d' ' -f1
}

ensure_configure_script() {
  if test ! -x "${MONITOR_DIR}/configure" \
    || test "${MONITOR_DIR}/configure.ac" -nt "${MONITOR_DIR}/configure"; then
    log "autoreconf -fi (${MONITOR_DIR})"
    (cd "${MONITOR_DIR}" && autoreconf -fi) >>"${LOG}" 2>&1
  fi
}

# Configure-time settings of one variant: variant_settings <name> sets V_CC, V_PREFIX,
# V_CFLAGS, V_LDEXTRA and the V_ARGS array.
variant_settings() {
  local name="$1" f
  V_CC=""
  V_PREFIX="${PREFIX}"
  V_LDEXTRA=""
  V_ARGS=(--enable-all-static --with-systemduserunitdir=no --with-cpu-counter-backend=auto)
  case "${name}" in
    default | debug | gc | gc-debug)
      while IFS= read -r f; do
        test -n "${f}" && V_ARGS+=("${f}")
      done < <(feat_flags)
      ;;
    arm | gc-arm)
      V_CC="clang --target=${ARM_TRIPLET} --sysroot=${ARM_SYSROOT}"
      V_PREFIX="${ARM_PREFIX}"
      V_LDEXTRA="-fuse-ld=lld"
      # Grace fleet (Vista, Horizon): arm backend, MAD via dlopen shim, shared node.
      V_ARGS+=(--host="${ARM_TRIPLET}" --enable-ib-mad-dlopen --disable-amd-gpu
        --enable-shared-node-multi-jid)
      ;;
  esac
  case "${name}" in
    debug | gc-debug) V_ARGS+=(--enable-debug --enable-shared-node-multi-jid) ;;
  esac
  case "${name}" in
    gc*)
      V_CFLAGS="${GC_CFLAGS}"
      V_LDEXTRA="${V_LDEXTRA:+${V_LDEXTRA} }${GC_LDFLAGS}"
      ;;
    *) V_CFLAGS="-O2" ;;
  esac
}

# configure_variant <name>: (re)configure .build-warn/<name> when its fingerprint changed.
configure_variant() {
  local name="$1"
  local dir="${GATE_ROOT}/${name}"
  variant_settings "${name}"
  local fp
  fp="$(fingerprint "${name}" "${V_CC}" "${V_CFLAGS}" "${V_LDEXTRA}" "${WARN_FLAGS}" "${V_ARGS[@]}")"
  if test -f "${dir}/.gate-fingerprint" && test "$(cat "${dir}/.gate-fingerprint")" = "${fp}" \
    && test -f "${dir}/Makefile"; then
    return 0
  fi
  log "[${name}] (re)configuring clean tree ${dir}"
  rm -rf "${dir}"
  mkdir -p "${dir}"
  ensure_configure_script
  if ! (cd "${dir}" \
    && if test -n "${V_CC}"; then export CC="${V_CC}"; fi \
    && CPPFLAGS="$(isystem_flags "${V_PREFIX}")" \
      LDFLAGS="${V_LDEXTRA:+${V_LDEXTRA} }-L${V_PREFIX}/lib -L${V_PREFIX}/lib64" \
      CFLAGS="${V_CFLAGS}" \
      "${MONITOR_DIR}/configure" "${V_ARGS[@]}") >>"${LOG}" 2>&1; then
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

# build_variant <name> <with-tests 0|1>: -Wall -Wextra -Werror compile of the daemon
# (and the make-check programs).
build_variant() {
  local name="$1" with_tests="$2"
  configure_variant "${name}"
  local brc=0
  log "[${name}] daemon: make CFLAGS='${V_CFLAGS} ${WARN_FLAGS}'"
  make_variant "${name}" "${V_CFLAGS} ${WARN_FLAGS}" || brc=1
  if test "${with_tests}" = "1"; then
    local -a progs
    # Test programs need src/ generated headers; build those even if the daemon failed.
    make -C "${GATE_ROOT}/${name}/src" monitor_capability_slug.h >>"${LOG}" 2>&1 || true
    read -r -a progs <<<"$(check_programs "${name}")"
    log "[${name}] ${#progs[@]} make-check programs"
    make_variant "${name}" "${V_CFLAGS} ${WARN_FLAGS}" -C tests "${progs[@]}" || brc=1
  fi
  return "${brc}"
}

# gc_variant <name> <out-dir>: link the daemon with --print-gc-sections; write
# <out-dir>/<name>.defined (global functions it compiles) and <name>.unreachable.
gc_variant() {
  local name="$1" out="$2"
  local dir="${GATE_ROOT}/${name}"
  configure_variant "${name}"
  rm -f "${dir}/src/hpcperfstatsd"
  log "[${name}] link daemon with --gc-sections --print-gc-sections (reachability only)"
  make_variant "${name}" "${V_CFLAGS}" || return 1
  # Global (nm "T") symbols only: at -O0 unused static helpers from headers are dropped
  # per object too, and those are not dead code (-Wunused-function covers statics).
  nm --defined-only "${dir}"/src/hpcperfstatsd-*.o 2>/dev/null | awk '$2 == "T" { print $3 }' \
    | sort -u >"${out}/${name}.defined"
  comm -12 <(parse_gc_log "${dir}/.gate-make.log" | cut -d' ' -f2 | sort -u) "${out}/${name}.defined" \
    >"${out}/${name}.unreachable"
}

unreachable_check() {
  local tmp v rc=0
  local -a variants=(gc gc-debug gc-arm)
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/warn_gate_gc_XXXXXX")"
  for v in "${variants[@]}"; do
    gc_variant "${v}" "${tmp}" || rc=1
  done
  if test "${rc}" != "0"; then
    rm -rf "${tmp}"
    return 1
  fi
  dead_functions "${tmp}" >"${tmp}/dead"
  cat "${tmp}"/*.defined | sort -u >"${tmp}/all_defined"

  local new stale
  new="$(comm -23 "${tmp}/dead" <(allowlist_names))"
  if test -n "${new}"; then
    log "[gc] FAILED: non-static functions unreachable in every configuration (not in $(basename "${ALLOWLIST}")):"
    printf '%s\n' "${new}" | sed 's/^/  /' | tee -a "${LOG}" >&2
    log "Delete them, make them static, call them, or (only tests call it) add them to the allowlist."
    rc=1
  fi
  stale="$(comm -13 "${tmp}/dead" <(allowlist_names))"
  if test -n "${stale}"; then
    log "[gc] note: allowlist entries reached by some configuration or no longer defined (safe to remove):"
    printf '%s\n' "${stale}" | sed 's/^/  /' | tee -a "${LOG}" >&2
  fi
  if test "${rc}" = "0"; then
    log "[gc] OK ($(wc -l <"${tmp}/dead") unreachable in every configuration, all allowlisted;" \
      "$(wc -l <"${tmp}/all_defined") global functions across ${#variants[@]} configurations)"
  fi
  rm -rf "${tmp}"
  return "${rc}"
}

preflight
log "=== run_warning_gate.sh --${MODE} $(date -Is) PREFIX=${PREFIX} ==="
rc=0
case "${MODE}" in
  commit)
    build_variant default 0 || rc=1
    ;;
  push)
    build_variant default 1 || rc=1
    build_variant debug 1 || rc=1
    if test "${ARM_READY}" = "1"; then
      build_variant arm 0 || rc=1
      unreachable_check || rc=1
    fi
    ;;
esac
if test "${rc}" = "0"; then
  log "=== monitor warning gate (--${MODE}) passed ==="
fi
exit "${rc}"
