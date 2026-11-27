#!/usr/bin/env bash
# Feed-only microbench: IncrementalStatsParser (CPython) vs FeedKernel (CPython).
# Optional Codon driver when codon run succeeds.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
GIT_ROOT="$(cd "$ROOT/../../.." && pwd)"
VENV="${HPCPERFSTATS_VENV:-/data/HPCPerfStats/.venv/bin/python3}"
CODON="${CODON_BIN:-/data/user/${USER}/codon/bin/codon}"
LOG_DIR="${HPCPERFSTATS_TEST_RUNS:-/data/HPCPerfStats/test_runs}"
STAMP="$(date +%Y%m%dT%H%M%S)"
LOG="${LOG_DIR}/codon_bench_${STAMP}.log"
mkdir -p "$LOG_DIR"

{
  echo "codon_bench stamp=$STAMP"
  echo "venv=$("$VENV" -V 2>&1)"
  echo "codon=$("$CODON" --version 2>&1 || echo missing)"
  if [ -x /opt/python3.14t/bin/python ]; then
    echo "ft314t=$(/opt/python3.14t/bin/python -V 2>&1)"
  else
    echo "ft314t=unavailable_on_host"
  fi

  cd "$GIT_ROOT"
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  "$VENV" "$ROOT/bench_cpython.py"

  echo "=== codon run attempt ==="
  if (cd "$ROOT" && "$CODON" run -release bench_driver.py); then
    echo "codon_run=pass"
  else
    echo "codon_run=fail"
  fi
} 2>&1 | tee "$LOG"

echo "bench_log=$LOG"
