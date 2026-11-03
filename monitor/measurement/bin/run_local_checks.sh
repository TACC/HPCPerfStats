#!/usr/bin/env bash
# Syntax-check monitor capture scripts and run parser unit tests.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

WORKSPACE_ROOT="$(cd "${ROOT}/../../.." && pwd)"
if [[ -n "${HPCPERFSTATS_PYTHON:-}" ]]; then
  PYTHON="${HPCPERFSTATS_PYTHON}"
elif [[ -x "${WORKSPACE_ROOT}/.venv/bin/python3" ]]; then
  PYTHON="${WORKSPACE_ROOT}/.venv/bin/python3"
else
  PYTHON="python3"
fi

echo "Using ${PYTHON}"

fail=0
while IFS= read -r script; do
  bash -n "${script}" || fail=1
done < <(find "${ROOT}/bin" -name '*.sh' -print)

"${PYTHON}" -m py_compile \
  "${ROOT}"/lib/*.py \
  "${ROOT}"/bin/*.py \
  "${ROOT}"/tests/*.py

"${PYTHON}" -m pytest -p no:django --rootdir="${ROOT}" --confcutdir="${ROOT}" \
  "${ROOT}/tests" -q
exit "${fail}"
