#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# shellcheck source=colima_compose_teardown.sh
. "$(dirname "${BASH_SOURCE[0]}")/colima_compose_teardown.sh"
# shellcheck source=compose_test_cmd.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
colima_export_docker_env

cleanup() {
  colima_compose_teardown "${COMPOSE_TEST[@]}"
}
trap cleanup EXIT

echo "[security-audit] running pip-audit inside compose web image"
compose_test run --rm --entrypoint sh web -lc \
  "pip install --disable-pip-version-check pip-audit >/dev/null && pip-audit"

if command -v npm >/dev/null 2>&1; then
  echo "[security-audit] running npm audit in frontend"
  (cd hpcperfstats/site/frontend && npm audit)
else
  echo "[security-audit] npm not available on host; skipping npm audit"
fi
