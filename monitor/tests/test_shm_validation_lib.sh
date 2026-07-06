#!/bin/sh
# Run Python unit tests for shm validation libraries.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${VENV_PY}"; then
  VENV_PY="$(command -v python3)"
fi

"${VENV_PY}" "${ROOT}/tests/test_shm_validation_lib.py"
echo "test_shm_validation_lib passed"
