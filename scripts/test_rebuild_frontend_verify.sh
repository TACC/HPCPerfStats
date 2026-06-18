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

for fn in copy_frontend_into_web compose_cp_supported web_container_id verify_container_frontend_matches_host; do
  if ! declare -F "${fn}" >/dev/null; then
    echo "expected ${fn} to be defined in rebuild_frontend.sh" >&2
    exit 1
  fi
done

if ! grep -q 'copy_frontend_via_podman_cp' "${REBUILD_SCRIPT}"; then
  echo "expected podman cp fallback in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'podman exec -i' "${REBUILD_SCRIPT}"; then
  echo "expected podman exec -i tar fallback in rebuild_frontend.sh" >&2
  exit 1
fi

if grep -q 'compose cp unavailable; using tar pipe via exec' "${REBUILD_SCRIPT}"; then
  echo "unexpected broken compose exec tar pipe message in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'rm -rf "\${STATIC_ROOT}/frontend"' "${REBUILD_SCRIPT}"; then
  echo "expected STATIC_ROOT/frontend refresh before collectstatic" >&2
  exit 1
fi

echo "test: deploy helpers and podman fallbacks are defined"

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
