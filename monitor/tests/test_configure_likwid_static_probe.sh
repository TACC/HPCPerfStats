#!/bin/sh
# Regression: static liblikwid probe (-pthread -ldl, AS_UNSET cache); prepare exports same libs.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

ac="${ROOT}/configure.ac"
prepare="${ROOT}/scripts/prepare_rpmbuild_dirs.sh"

grep -q 'perfmon_init' "${ac}" \
  || { echo "configure.ac must AC_SEARCH_LIBS perfmon_init" >&2; exit 1; }

# Static-bundle OTHER-LIBRARIES must include pthread/dl (Lonestar6).
awk '
  /AC_SEARCH_LIBS\(\[perfmon_init\]/ { in_likwid=1 }
  in_likwid && /-llikwid-hwloc -llikwid-lua -lm -lrt -pthread -ldl/ { found=1; exit }
  in_likwid && /AC_SEARCH_LIBS\(/ && !/perfmon_init/ { in_likwid=0 }
  END { exit(found ? 0 : 1) }
' "${ac}" \
  || { echo "configure.ac: perfmon_init probe must try -llikwid-hwloc -llikwid-lua -lm -lrt -pthread -ldl" >&2; exit 1; }

grep -q 'AS_UNSET(\[ac_cv_search_perfmon_init\])' "${ac}" \
  || { echo "configure.ac must AS_UNSET ac_cv_search_perfmon_init before plain retry" >&2; exit 1; }

# prepare: exported LIBS for configure must include -lpthread -ldl (not probe-only).
grep -q -- '-lpthread -ldl' "${prepare}" \
  || { echo "prepare_rpmbuild_dirs.sh must export LIBS with -lpthread -ldl for LIKWID configure" >&2; exit 1; }
# Probe must not be the only place that adds -lpthread -ldl after ${LIBS}.
if grep -q 'LIBS:-} -lpthread -ldl' "${prepare}"; then
  echo "prepare verify_likwid_static_link_probe must not hide -lpthread -ldl from exported LIBS" >&2
  exit 1
fi

echo "test_configure_likwid_static_probe passed"
