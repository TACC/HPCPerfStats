#!/bin/sh
# Contract: ASan / cpp-linter gate scripts exist and encode required policies.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x scripts/run_asan_check.sh \
  || { echo "scripts/run_asan_check.sh must be executable" >&2; exit 1; }
test -x scripts/run_cpp_linter.sh \
  || { echo "scripts/run_cpp_linter.sh must be executable" >&2; exit 1; }
test -f .clang-format || { echo "missing .clang-format" >&2; exit 1; }
test -f .clang-tidy || { echo "missing .clang-tidy" >&2; exit 1; }
test -f tests/requirements-cpp-linter.txt \
  || { echo "missing tests/requirements-cpp-linter.txt" >&2; exit 1; }

grep -q 'overcommit_memory' scripts/run_asan_check.sh \
  || { echo "run_asan_check.sh must preflight vm.overcommit_memory" >&2; exit 1; }
grep -q -- '--disable-all-static' scripts/run_asan_check.sh \
  || { echo "run_asan_check.sh must configure --disable-all-static" >&2; exit 1; }
grep -q '\.build-asan' scripts/run_asan_check.sh \
  || { echo "run_asan_check.sh must use .build-asan" >&2; exit 1; }

# Must not call pre-commit as a command (comments mentioning it are OK).
if grep -E '^[^#]*\bpre-commit\b' scripts/run_cpp_linter.sh >/dev/null 2>&1; then
  echo "run_cpp_linter.sh must not invoke pre-commit" >&2
  exit 1
fi
grep -q 'MONITOR_DIR}/src' scripts/run_cpp_linter.sh \
  || { echo "run_cpp_linter.sh must scan src/" >&2; exit 1; }
grep -q 'MONITOR_DIR}/tests' scripts/run_cpp_linter.sh \
  || { echo "run_cpp_linter.sh must scan tests/" >&2; exit 1; }

echo "test_asan_cpp_linter_scripts passed"
