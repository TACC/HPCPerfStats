#!/bin/sh
# Contract: Valgrind Memcheck / cpp-linter gate scripts exist and encode required policies.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x scripts/run_valgrind_check.sh \
  || { echo "scripts/run_valgrind_check.sh must be executable" >&2; exit 1; }
test -x scripts/run_cpp_linter.sh \
  || { echo "scripts/run_cpp_linter.sh must be executable" >&2; exit 1; }
test -f scripts/valgrind.supp \
  || { echo "missing scripts/valgrind.supp" >&2; exit 1; }
test -f .clang-format || { echo "missing .clang-format" >&2; exit 1; }
test -f .clang-tidy || { echo "missing .clang-tidy" >&2; exit 1; }
test -f tests/requirements-cpp-linter.txt \
  || { echo "missing tests/requirements-cpp-linter.txt" >&2; exit 1; }

grep -q '\.build-valgrind' scripts/run_valgrind_check.sh \
  || { echo "run_valgrind_check.sh must use .build-valgrind" >&2; exit 1; }
grep -q 'LOG_COMPILER=valgrind' scripts/run_valgrind_check.sh \
  || { echo "run_valgrind_check.sh must set LOG_COMPILER=valgrind" >&2; exit 1; }
grep -q -- '--tool=memcheck' scripts/run_valgrind_check.sh \
  || { echo "run_valgrind_check.sh must use --tool=memcheck" >&2; exit 1; }
grep -q -- '--error-exitcode=1' scripts/run_valgrind_check.sh \
  || { echo "run_valgrind_check.sh must use --error-exitcode=1" >&2; exit 1; }
grep -q 'valgrind.supp' scripts/run_valgrind_check.sh \
  || { echo "run_valgrind_check.sh must reference valgrind.supp" >&2; exit 1; }
grep -q -- '--enable-all-static' scripts/run_valgrind_check.sh \
  || { echo "run_valgrind_check.sh must configure --enable-all-static" >&2; exit 1; }

# ASan must be gone from this gate.
if test -e scripts/run_asan_check.sh; then
  echo "scripts/run_asan_check.sh must be removed" >&2
  exit 1
fi
if grep -q 'fsanitize=address' scripts/run_valgrind_check.sh; then
  echo "run_valgrind_check.sh must not use -fsanitize=address" >&2
  exit 1
fi
if grep -q 'overcommit_memory' scripts/run_valgrind_check.sh; then
  echo "run_valgrind_check.sh must not use ASan overcommit preflight" >&2
  exit 1
fi

# Must not call pre-commit as a command (comments mentioning it are OK).
if grep -E '^[^#]*\bpre-commit\b' scripts/run_cpp_linter.sh >/dev/null 2>&1; then
  echo "run_cpp_linter.sh must not invoke pre-commit" >&2
  exit 1
fi
grep -q 'MONITOR_DIR}/src' scripts/run_cpp_linter.sh \
  || { echo "run_cpp_linter.sh must scan src/" >&2; exit 1; }
grep -q 'MONITOR_DIR}/tests' scripts/run_cpp_linter.sh \
  || { echo "run_cpp_linter.sh must scan tests/" >&2; exit 1; }

echo "test_valgrind_cpp_linter_scripts passed"
