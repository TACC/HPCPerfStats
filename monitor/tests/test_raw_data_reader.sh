#!/bin/sh
# Raw data format guard: python/hpcperfstats_raw unit tests, the C-vs-reader-vs-doc drift
# tests (python/tests/test_wire_drift.py), the RAW_DATA_FORMAT.md example blocks, and
# scripts/gen_metrics_reference.py --check (docs/METRICS_REFERENCE.md up to date).
# Needs Python >= 3.8 (the reader's floor); exits 77 (SKIP) on an older python3.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
VENV_PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${VENV_PY}" || ! "${VENV_PY}" -c "import sys" >/dev/null 2>&1; then
  VENV_PY="$(command -v python3)"
fi
if ! "${VENV_PY}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "test_raw_data_reader: SKIP (python3 older than 3.8)"
  exit 77
fi

cd "${ROOT}/python"
"${VENV_PY}" -m unittest discover -s tests -t . -v 2>&1
"${VENV_PY}" "${ROOT}/scripts/gen_metrics_reference.py" --check
echo "test_raw_data_reader passed"
