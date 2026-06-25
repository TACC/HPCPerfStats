#!/usr/bin/env bash
# Install pre-commit / pre-push lint hooks for HPCPerfStats.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "../.venv/bin/python3" ]]; then
  echo "error: workspace venv missing at ../.venv — create it before installing hooks" >&2
  exit 1
fi

../.venv/bin/pip3 install -e ".[dev]"
../.venv/bin/pre-commit install
../.venv/bin/pre-commit install --hook-type pre-push

FRONTEND="$ROOT/hpcperfstats/site/frontend"
if [[ -f "$FRONTEND/package.json" ]]; then
  (cd "$FRONTEND" && npm ci)
fi

echo "Git hooks installed (pre-commit + pre-push)."
