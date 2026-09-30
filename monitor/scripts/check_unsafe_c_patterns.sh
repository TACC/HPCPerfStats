#!/usr/bin/env bash
# Fail if monitor src/ contains forbidden unbounded string APIs or unsafe exec helpers.
# Allowlist: scripts/check_unsafe_c_patterns.allowlist (file:line per entry).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/src"
ALLOWLIST="${ROOT}/scripts/check_unsafe_c_patterns.allowlist"

if [[ ! -d "$SRC" ]]; then
  echo "check_unsafe_c_patterns: missing $SRC" >&2
  exit 1
fi

is_allowlisted() {
  local file="$1"
  local num="$2"
  [[ -f "$ALLOWLIST" ]] || return 1
  while IFS= read -r entry || [[ -n "$entry" ]]; do
    [[ -z "$entry" || "$entry" =~ ^[[:space:]]*# ]] && continue
    if [[ "$entry" == "${file}:${num}"* ]]; then
      return 0
    fi
  done < "$ALLOWLIST"
  return 1
}

violations=()
total_hits=0

scan_pattern() {
  local pat="$1"
  local line file num rest

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    total_hits=$((total_hits + 1))
    file="${line%%:*}"
    rest="${line#*:}"
    num="${rest%%:*}"
    file="${file#"$SRC/"}"
    if ! is_allowlisted "$file" "$num"; then
      violations+=("${file}:${rest}")
    fi
  done < <(grep -rnE "$pat" "$SRC" --include='*.c' --include='*.h' 2>/dev/null || true)
}

# Absolute popen("...") is allowed without allowlist; relative / PATH-based popen must be listed.
scan_relative_popen() {
  local line file num rest cmd

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    total_hits=$((total_hits + 1))
    file="${line%%:*}"
    rest="${line#*:}"
    num="${rest%%:*}"
    file="${file#"$SRC/"}"
    cmd="${rest#*:}"
    # Allow only when the first string literal after popen( begins with /
    if [[ "$cmd" =~ popen[[:space:]]*\([[:space:]]*\"/ ]]; then
      continue
    fi
    if ! is_allowlisted "$file" "$num"; then
      violations+=("${file}:${rest}")
    fi
  done < <(grep -rnE '(^|[^a-zA-Z0-9_])popen[[:space:]]*\(' "$SRC" --include='*.c' --include='*.h' 2>/dev/null || true)
}

# Exclude fgets/asprintf/vasprintf via non-word char before token (portable grep).
scan_pattern '(^|[^a-zA-Z0-9_])gets[[:space:]]*\('
scan_pattern 'strcpy[[:space:]]*\('
scan_pattern '(^|[^a-zA-Z0-9_])sprintf[[:space:]]*\('
scan_pattern '(^|[^a-zA-Z0-9_])system[[:space:]]*\('
scan_pattern '(^|[^a-zA-Z0-9_])execvp[[:space:]]*\('
scan_relative_popen

if ((${#violations[@]} > 0)); then
  echo "check_unsafe_c_patterns: forbidden API use (not in allowlist):" >&2
  printf '  %s\n' "${violations[@]}" >&2
  echo "Add temporary entries to $ALLOWLIST only for legacy lines pending refactor." >&2
  exit 1
fi

echo "check_unsafe_c_patterns: OK (${total_hits} legacy hit(s) allowlisted, 0 new violations)"
