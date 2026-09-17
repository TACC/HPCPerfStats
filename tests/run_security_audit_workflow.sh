#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# shellcheck source=compose_test_cmd.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)
      # The audit uses the image built by an earlier matrix phase.
      ;;
    -h|--help)
      echo "Usage: tests/run_security_audit_workflow.sh [--skip-build]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

podman_runtime_require

cleanup() {
  podman_compose_teardown "${COMPOSE_TEST[@]}"
}
trap cleanup EXIT

echo "[security-audit] running pip-audit inside compose web image"
compose_test run --rm --entrypoint sh web -lc \
  "python3 -m pip install --disable-pip-version-check pip-audit >/dev/null && python3 -m pip_audit"

echo "[security-audit] running npm audit in frontend"
(cd hpcperfstats/site/frontend && npm audit)
