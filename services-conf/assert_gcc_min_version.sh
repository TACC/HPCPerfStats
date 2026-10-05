#!/usr/bin/env bash
# Fail closed when the active gcc is older than GCC_MIN_VERSION (e.g. 16.2).
set -euo pipefail

min="${GCC_MIN_VERSION:-16.2}"
if [[ $# -ge 1 ]]; then
  min="$1"
fi

if ! command -v gcc >/dev/null 2>&1; then
  echo "assert_gcc_min_version: gcc not found in PATH" >&2
  exit 1
fi

got="$(gcc -dumpfullversion)"
if [[ -z "${got}" ]]; then
  echo "assert_gcc_min_version: gcc -dumpfullversion returned empty" >&2
  exit 1
fi

# BusyBox sort (Alpine) has -c -V but not GNU -C; compare via sort -V only.
if [[ "$(printf '%s\n%s\n' "${min}" "${got}" | sort -V | head -n1)" != "${min}" ]]; then
  echo "assert_gcc_min_version: need gcc >= ${min}, got ${got}" >&2
  exit 1
fi

echo "assert_gcc_min_version: gcc ${got} >= ${min}"
