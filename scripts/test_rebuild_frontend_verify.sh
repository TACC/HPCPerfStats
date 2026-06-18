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

for fn in copy_frontend_into_web compose_cp_supported web_container_ref verify_container_frontend_matches_host verify_proxy_frontend_matches_web copy_tree_via_staged_tar compose_backend_is_podman deploy_frontend_via_collectstatic; do
  if ! declare -F "${fn}" >/dev/null; then
    echo "expected ${fn} to be defined in rebuild_frontend.sh" >&2
    exit 1
  fi
done

if ! grep -q 'copy_tree_via_staged_tar' "${REBUILD_SCRIPT}"; then
  echo "expected staged tar deploy path in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'staged tar + compose exec extract' "${REBUILD_SCRIPT}"; then
  echo "expected staged tar + compose exec extract message in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'compose_backend_is_podman' "${REBUILD_SCRIPT}"; then
  echo "expected podman-compose detection in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'verify_proxy_frontend_matches_web' "${REBUILD_SCRIPT}"; then
  echo "expected proxy nginx volume verification in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'PROXY_STATIC_ROOT_FRONTEND' "${REBUILD_SCRIPT}"; then
  echo "expected PROXY_STATIC_ROOT_FRONTEND in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'deploy_frontend_via_collectstatic' "${REBUILD_SCRIPT}"; then
  echo "expected podman collectstatic deploy path in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'run_collectstatic_into_volume' "${REBUILD_SCRIPT}"; then
  echo "expected collectstatic volume refresh for podman in rebuild_frontend.sh" >&2
  exit 1
fi

if grep -q 'compose cp unavailable; using tar pipe via exec' "${REBUILD_SCRIPT}"; then
  echo "unexpected broken compose exec tar pipe message in rebuild_frontend.sh" >&2
  exit 1
fi

if ! grep -q 'CONTAINER_STATIC_ROOT_FRONTEND' "${REBUILD_SCRIPT}"; then
  echo "expected STATIC_ROOT/frontend volume deploy in rebuild_frontend.sh" >&2
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
