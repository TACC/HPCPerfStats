#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "[security-audit] running pip-audit inside compose web image"
docker-compose run --rm --entrypoint sh web -lc \
  "pip install --disable-pip-version-check pip-audit >/dev/null && pip-audit"

if command -v npm >/dev/null 2>&1; then
  echo "[security-audit] running npm audit in frontend"
  (cd hpcperfstats/site/frontend && npm audit)
else
  echo "[security-audit] npm not available on host; skipping npm audit"
fi
