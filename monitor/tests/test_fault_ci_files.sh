#!/bin/sh
# scripts/check_fault_ci_files.py: the CI-covered list matches the fault-coverage targets in
# tests/Makefile.am and every src file with a failure site; a missing or extra entry FAILs.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python3"
if test ! -x "${PY}"; then
  PY="$(command -v python3)"
fi

"${PY}" -I "${ROOT}/scripts/check_fault_ci_files.py"
"${PY}" -I - "${ROOT}/scripts" <<'PYEOF'
import io
import sys
from contextlib import redirect_stdout

sys.path.insert(0, sys.argv[1])
import check_fault_ci_files as c

full = c.CI_COVERED_FILES
for changed, want in ((full - {"tmpfs.c"}, "tmpfs.c: has failure sites but no fault-coverage target"),
                      (full | {"bogus.c"}, "bogus.c: in CI_COVERED_FILES but no fault target links it")):
    c.CI_COVERED_FILES = changed
    out = io.StringIO()
    with redirect_stdout(out):
        rc = c.main()
    assert rc == 1 and want in out.getvalue(), out.getvalue()
c.CI_COVERED_FILES = full
print("negative cases OK")
PYEOF
echo "test_fault_ci_files passed"
