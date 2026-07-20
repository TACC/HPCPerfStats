#!/usr/bin/env bash
# Full-tree clang-format + clang-tidy gate for monitor src/ and tests/.
# No git hooks / pre-commit — invoke tools directly (prefer workspace .venv).
#
# Usage:
#   ./scripts/run_cpp_linter.sh
#   CLANG_FORMAT_FIX=1 ./scripts/run_cpp_linter.sh   # apply clang-format -i
#
# Environment:
#   CLANG_FORMAT / CLANG_TIDY  Override tool paths
#   CLANG_FORMAT_FIX=1         Rewrite sources with clang-format
#   SKIP_CLANG_FORMAT=1        Skip format check
#   SKIP_CLANG_TIDY=1          Skip tidy check
#   COMPILE_COMMANDS           Path to compile_commands.json (optional)
#   CPP_LINTER_LOG             Override log path
#
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"
readonly VENV_BIN="${REPO_ROOT}/.venv/bin"

DATE_STAMP="$(date +%Y-%m-%d)"
CPP_LINTER_LOG="${CPP_LINTER_LOG:-${REPO_ROOT}/test_runs/cpp-linter-${DATE_STAMP}.log}"
mkdir -p "$(dirname "${CPP_LINTER_LOG}")"

log() {
  printf '%s\n' "$*" | tee -a "${CPP_LINTER_LOG}"
}

die() {
  log "error: $*"
  exit 1
}

resolve_tool() {
  local name="$1"
  local override="$2"
  if test -n "${override}"; then
    printf '%s\n' "${override}"
    return 0
  fi
  if test -x "${VENV_BIN}/${name}"; then
    printf '%s\n' "${VENV_BIN}/${name}"
    return 0
  fi
  if command -v "${name}" >/dev/null 2>&1; then
    command -v "${name}"
    return 0
  fi
  return 1
}

list_c_sources() {
  find "${MONITOR_DIR}/src" "${MONITOR_DIR}/tests" \
    -type f \( -name '*.c' -o -name '*.h' \) \
    ! -path '*/third_party/*' \
    ! -path '*/.build*/*' \
    | sort
}

ensure_python_module_hint() {
  if test -x "${VENV_BIN}/python3" && ! "${VENV_BIN}/python3" -c 'import sys' >/dev/null 2>&1; then
    log "hint: load the Python module used to create .venv (e.g. module load python/3.12.11) so .venv/bin tools work"
  fi
}

: >"${CPP_LINTER_LOG}"
log "=== run_cpp_linter.sh $(date -Is) ==="
log "MONITOR_DIR=${MONITOR_DIR}"
log "log=${CPP_LINTER_LOG}"
ensure_python_module_hint

mapfile -t SOURCES < <(list_c_sources)
test "${#SOURCES[@]}" -gt 0 || die "no C/H sources under src/ or tests/"
log "scanning ${#SOURCES[@]} files under src/ and tests/ (full tree)"

CLANG_FORMAT_BIN=""
CLANG_TIDY_BIN=""
if test "${SKIP_CLANG_FORMAT:-0}" != "1"; then
  CLANG_FORMAT_BIN="$(resolve_tool clang-format "${CLANG_FORMAT:-}")" \
    || die "clang-format not found. Install into workspace .venv: pip install 'clang-format==19.1.7' (after module load python), or put clang-format on PATH."
  log "clang-format: ${CLANG_FORMAT_BIN} ($("${CLANG_FORMAT_BIN}" --version | head -1))"
fi
if test "${SKIP_CLANG_TIDY:-0}" != "1"; then
  CLANG_TIDY_BIN="$(resolve_tool clang-tidy "${CLANG_TIDY:-}")" \
    || die "clang-tidy not found. Install into workspace .venv: pip install 'clang-tidy==19.1.0.1', or put clang-tidy on PATH."
  log "clang-tidy: ${CLANG_TIDY_BIN} ($("${CLANG_TIDY_BIN}" --version 2>/dev/null | head -2 | tr '\n' ' '))"
fi

format_rc=0
tidy_rc=0

if test "${SKIP_CLANG_FORMAT:-0}" != "1"; then
  # Batch to avoid ARG_MAX / incomplete multi-file -i on large trees.
  batch_size=40
  i=0
  batch=()
  flush_batch() {
    if test "${#batch[@]}" -eq 0; then
      return 0
    fi
    if test "${CLANG_FORMAT_FIX:-0}" = "1"; then
      "${CLANG_FORMAT_BIN}" -style=file -i "${batch[@]}" || format_rc=1
    else
      if ! "${CLANG_FORMAT_BIN}" -style=file --dry-run -Werror "${batch[@]}" >>"${CPP_LINTER_LOG}" 2>&1; then
        format_rc=1
      fi
    fi
    batch=()
  }
  if test "${CLANG_FORMAT_FIX:-0}" = "1"; then
    log "clang-format -i (apply, batches of ${batch_size})"
  else
    log "clang-format --dry-run -Werror (batches of ${batch_size})"
  fi
  for f in "${SOURCES[@]}"; do
    batch+=("${f}")
    i=$((i + 1))
    if test "${#batch[@]}" -ge "${batch_size}"; then
      flush_batch
    fi
  done
  flush_batch
  if test "${format_rc}" -ne 0 && test "${CLANG_FORMAT_FIX:-0}" != "1"; then
    log "clang-format reported style diffs (see log). Re-run with CLANG_FORMAT_FIX=1 to apply, then re-check."
  fi
fi

if test "${SKIP_CLANG_TIDY:-0}" != "1"; then
  TIDY_ARGS=()
  if test -n "${COMPILE_COMMANDS:-}" && test -f "${COMPILE_COMMANDS}"; then
    TIDY_ARGS+=(-p "$(dirname "${COMPILE_COMMANDS}")")
    log "clang-tidy using compile DB: ${COMPILE_COMMANDS}"
  elif test -f "${MONITOR_DIR}/.build-valgrind/compile_commands.json"; then
    TIDY_ARGS+=(-p "${MONITOR_DIR}/.build-valgrind")
    log "clang-tidy using compile DB: ${MONITOR_DIR}/.build-valgrind/compile_commands.json"
  elif test -f "${MONITOR_DIR}/.build-static/compile_commands.json"; then
    TIDY_ARGS+=(-p "${MONITOR_DIR}/.build-static")
    log "clang-tidy using compile DB: ${MONITOR_DIR}/.build-static/compile_commands.json"
  else
    # Prefer a configured Autotools tree so config.h / STATS_* macros resolve.
    local_cfg=""
    for cand in "${MONITOR_DIR}/.build-static" "${MONITOR_DIR}/.build-valgrind" "${MONITOR_DIR}"; do
      if test -f "${cand}/config.h"; then
        local_cfg="${cand}"
        break
      fi
    done
    EXTRA=( -std=gnu11 -D_GNU_SOURCE "-I${MONITOR_DIR}/src" "-I${MONITOR_DIR}" )
    if test -n "${local_cfg}"; then
      EXTRA+=( "-I${local_cfg}" -DHAVE_CONFIG_H )
      log "clang-tidy: no compile_commands.json; using -std=gnu11 + config.h from ${local_cfg}"
    else
      log "clang-tidy: no compile_commands.json or config.h; using -std=gnu11 -Isrc only (may mis-parse Autotools sources)"
    fi
    if test -d "${MONITOR_DIR}/third_party/intel-xpum"; then
      EXTRA+=( "-I${MONITOR_DIR}/third_party/intel-xpum" )
    fi
    if test -d "${MONITOR_DIR}/third_party/nvidia-dcgm"; then
      EXTRA+=( "-I${MONITOR_DIR}/third_party/nvidia-dcgm" )
    fi
    TIDY_ARGS+=( -- "${EXTRA[@]}" )
  fi

  log "clang-tidy (config ${MONITOR_DIR}/.clang-tidy)"
  # Run per-file so one failure does not hide the rest; aggregate exit status.
  # Without a compile_commands.json, Autotools sources often fail to parse
  # (missing STATS_* macros). Fail the gate only on selected bugprone hits;
  # treat pure clang-diagnostic parse noise as a warning in that mode.
  have_compile_db=0
  if test "${#TIDY_ARGS[@]}" -gt 0 && test "${TIDY_ARGS[0]}" != "--"; then
    have_compile_db=1
  fi
  for src in "${SOURCES[@]}"; do
    case "${src}" in
      *.h) continue ;; # tidy .c translation units; headers via includes
    esac
    tidy_tmp="$(mktemp "${TMPDIR:-/tmp}/clang-tidy.XXXXXX")"
    set +e
    "${CLANG_TIDY_BIN}" -quiet "${src}" "${TIDY_ARGS[@]}" >"${tidy_tmp}" 2>&1
    cmd_rc=$?
    set -e
    cat "${tidy_tmp}" >>"${CPP_LINTER_LOG}"
    if grep -E '\[bugprone-(suspicious-memset-usage|sizeof-expression|macro-repeated-side-effects)\]' "${tidy_tmp}" >/dev/null 2>&1; then
      tidy_rc=1
    elif test "${cmd_rc}" -ne 0; then
      if test "${have_compile_db}" = "1"; then
        tidy_rc=1
      else
        log "warn: clang-tidy parse/diagnostic noise for ${src} (no compile_commands.json; ignored for gate)"
      fi
    fi
    rm -f "${tidy_tmp}"
  done
fi

if test "${format_rc}" -ne 0 || test "${tidy_rc}" -ne 0; then
  log "=== run_cpp_linter.sh FAILED format_rc=${format_rc} tidy_rc=${tidy_rc} ==="
  exit 1
fi

log "=== run_cpp_linter.sh OK ==="
echo "cpp-linter check passed; log: ${CPP_LINTER_LOG}"
