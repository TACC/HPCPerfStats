#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=pgo_lib.sh
source "${SCRIPT_DIR}/pgo_lib.sh"

if pgo_reset_sketch_tree "${REPO_ROOT}" || [[ ! -f "$(pgo_root_dir)/manifest.yaml" ]]; then
  root="$(pgo_root_dir)"
  mkdir -p "${root}/breadcrumbs"
  cat >"${root}/manifest.yaml" <<EOF
# HPCPerfStats PGO manifest (operator-local; not committed).
created: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
namespaces_source: ${PGO_NAMESPACES_YAML}
EOF
else
  root="$(pgo_root_dir)"
  mkdir -p "${root}/breadcrumbs"
fi

while IFS= read -r ns; do
  [[ -n "${ns}" ]] || continue
  mkdir -p "${root}/${ns}/raw"
done < <(pgo_list_namespaces "${REPO_ROOT}")

pgo_chmod_shared_tree

echo "pgo_ensure_layout: ready under ${root}" >&2
