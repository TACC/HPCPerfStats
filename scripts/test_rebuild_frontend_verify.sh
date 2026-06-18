#!/usr/bin/env bash
# Regression for rebuild_frontend.sh SPA shell verification (Next export layout).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=rebuild_frontend.sh
source "${SCRIPT_DIR}/rebuild_frontend.sh"

tmpdir=""
cleanup() {
  if [[ -n "${tmpdir}" && -d "${tmpdir}" ]]; then
    rm -rf "${tmpdir}"
  fi
}
trap cleanup EXIT

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
