#!/bin/sh
# Build-time Python scripts must parse and run on Python 3.6 (EL8 system python3).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${VENV_PY}" || ! "${VENV_PY}" -c "import sys" >/dev/null 2>&1; then
  VENV_PY="$(command -v python3)"
fi
# The checker itself needs ast feature_version (3.8+); the scripts it checks do not.
if ! "${VENV_PY}" -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
  echo "SKIP: test_build_scripts_py36 needs Python >= 3.8 to run the checker"
  exit 77
fi

"${VENV_PY}" "${ROOT}/tests/test_build_scripts_py36.py"
echo "test_build_scripts_py36 passed"
