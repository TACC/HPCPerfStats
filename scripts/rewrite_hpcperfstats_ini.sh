#!/usr/bin/env bash
# Rewrite the site hpcperfstats.ini onto hpcperfstats.ini.example, using the
# Python inside the release image (the host has no project Python).
#
# Runs scripts/rewrite_hpcperfstats_ini.py from THIS checkout in a throwaway
# container (no network, nothing else touched): the checkout is mounted at
# /work, so the code and the defaults it compares against are this release's,
# and the ini is rewritten on the host (old copy kept as <ini>.bak.<UTC>).
# No running container is used and no redeploy is needed first; rebuild
# afterwards to bake the rewritten ini into the image.
#
# Usage (from the checkout, or a directory inside it; paths are relative):
#   ./scripts/rewrite_hpcperfstats_ini.sh --dry-run
#   ./scripts/rewrite_hpcperfstats_ini.sh
#   ./scripts/rewrite_hpcperfstats_ini.sh hpcperfstats.ini --output new.ini
#   ./scripts/rewrite_hpcperfstats_ini.sh --image localhost/hpcperfstats:latest
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE="localhost/hpcperfstats:latest"

usage() {
  cat <<'EOF'
Usage: scripts/rewrite_hpcperfstats_ini.sh [--image IMAGE] [INI] [OPTIONS]

INI defaults to hpcperfstats.ini. OPTIONS go to rewrite_hpcperfstats_ini.py:
  --dry-run       print a diff, write nothing
  --output PATH   write PATH instead of rewriting INI in place
  --example PATH  example to rewrite onto (default: checkout example)
--image defaults to localhost/hpcperfstats:latest (any built release image).
Paths are relative to the current directory, which must be in the checkout.
EOF
}

args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE="${2:?--image needs a value}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done
if [[ ${#args[@]} -eq 0 || "${args[0]}" == --* ]]; then
  args=("hpcperfstats.ini" "${args[@]}")
fi

case "${PWD}/" in
  "${REPO_ROOT}/"*) rel="${PWD#"${REPO_ROOT}"}" ;;
  *)
    echo "Run from inside the checkout (${REPO_ROOT}); it is mounted at /work." >&2
    exit 2
    ;;
esac

if ! command -v podman >/dev/null 2>&1; then
  echo "podman not found on PATH." >&2
  exit 1
fi
if ! podman image exists "${IMAGE}"; then
  echo "Image ${IMAGE} not found; build it first (./scripts/rebuild_pipeline.sh) or pass --image." >&2
  exit 1
fi

# Container root maps to the invoking user (rootless) or root (rootful), so the
# rewritten ini keeps the checkout's ownership. label=disable avoids relabeling
# the whole checkout under SELinux.
exec podman run --rm --network none --security-opt label=disable \
  -v "${REPO_ROOT}:/work" -w "/work${rel}" \
  --entrypoint /usr/local/bin/python3 "${IMAGE}" \
  /work/scripts/rewrite_hpcperfstats_ini.py "${args[@]}"
