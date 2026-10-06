#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=pgo_lib.sh
source "${SCRIPT_DIR}/pgo_lib.sh"

if ! command -v llvm-profdata >/dev/null 2>&1; then
  pgo_die "llvm-profdata not found on PATH (install llvm tools on the build host)"
fi

root="$(pgo_root_dir)"
pgo_chmod_shared_tree
failed=()

while IFS= read -r ns; do
  [[ -n "${ns}" ]] || continue
  raw_dir="${root}/${ns}/raw"
  out="${root}/${ns}/default.profdata"
  shopt -s nullglob
  raw=( "${raw_dir}"/*.profraw )
  if [[ ${#raw[@]} -eq 0 ]]; then
    if [[ -s "${out}" ]]; then
      echo "PGO merge: ${ns} skip (existing default.profdata)" >&2
      continue
    fi
    failed+=( "${ns}: no raw/*.profraw and no default.profdata" )
    continue
  fi
  echo "PGO merge: ${ns} (${#raw[@]} profraw) …" >&2
  if ! llvm-profdata merge -output="${out}" "${raw[@]}"; then
    failed+=( "${ns}: llvm-profdata merge failed" )
    continue
  fi
  if [[ ! -s "${out}" ]]; then
    failed+=( "${ns}: empty default.profdata after merge" )
  fi
done < <(pgo_list_namespaces "${REPO_ROOT}")

pgo_chmod_shared_tree

if [[ ${#failed[@]} -gt 0 ]]; then
  pgo_die "$(printf '%s; ' "${failed[@]}")"
fi

echo "PGO merge: all namespaces OK under ${root}" >&2
