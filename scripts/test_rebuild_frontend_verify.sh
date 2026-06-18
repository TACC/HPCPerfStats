#!/usr/bin/env bash
# Regression for rebuild_frontend.sh SPA shell verification and deploy helpers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REBUILD_SCRIPT="${SCRIPT_DIR}/rebuild_frontend.sh"
# shellcheck source=rebuild_frontend.sh
source "${REBUILD_SCRIPT}"

tmpdir=""
cleanup() {
  if [[ -n "${tmpdir}" && -d "${tmpdir}" ]]; then
    rm -rf "${tmpdir}"
  fi
}
trap cleanup EXIT

if ! declare -F copy_frontend_into_web >/dev/null; then
  echo "expected copy_frontend_into_web to be defined in rebuild_frontend.sh" >&2
  exit 1
fi

if ! declare -F compose_cp_supported >/dev/null; then
  echo "expected compose_cp_supported to be defined in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'compose cp unavailable; using tar pipe via exec' "${REBUILD_SCRIPT}"; then
  echo "expected tar pipe fallback in rebuild_frontend.sh" >&2
  exit 1
fi

echo "test: copy_frontend_into_web and compose_cp_supported are defined"

tmpdir="$(mktemp -d)"

echo "test: empty dir fails verify_spa_shells"
if verify_spa_shells "${tmpdir}" "test-empty" 2>/dev/null; then
  echo "expected verify_spa_shells to fail on empty dir" >&2
  exit 1
fi

echo "test: root index.html alone does not satisfy verify_spa_shells"
mkdir -p "${tmpdir}"
echo "<html></html>" > "${tmpdir}/index.html"
if verify_spa_shells "${tmpdir}" "test-root-only" 2>/dev/null; then
  echo "expected verify_spa_shells to fail when only root index.html exists" >&2
  exit 1
fi

echo "test: machine/index.html and pub/index.html pass verify_spa_shells"
mkdir -p "${tmpdir}/machine" "${tmpdir}/pub"
echo "<html>machine</html>" > "${tmpdir}/machine/index.html"
echo "<html>pub</html>" > "${tmpdir}/pub/index.html"
verify_spa_shells "${tmpdir}" "test-ok"

echo "test_rebuild_frontend_verify.sh: all checks passed"
