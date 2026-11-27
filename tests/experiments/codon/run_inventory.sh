#!/usr/bin/env bash
# Tiered codon build inventory for parse-related stubs.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
CODON="${CODON_BIN:-/data/user/${USER}/codon/bin/codon}"
LOG_DIR="${HPCPERFSTATS_TEST_RUNS:-/data/HPCPerfStats/test_runs}"
STAMP="$(date +%Y%m%dT%H%M%S)"
LOG="${LOG_DIR}/codon_inventory_${STAMP}.log"
mkdir -p "$LOG_DIR"

exec > >(tee -a "$LOG") 2>&1
echo "codon_inventory stamp=$STAMP codon=$CODON"
"$CODON" --version || true

try_build() {
  local tier="$1"
  local file="$2"
  echo "=== tier=$tier file=$file ==="
  if (cd "$ROOT" && "$CODON" build -release -o "${LOG_DIR}/codon_tier_${tier}" "$file"); then
    echo "RESULT tier=$tier status=pass"
  else
    echo "RESULT tier=$tier status=fail"
  fi
}

try_build 0 inventory/tier0_stdlib.py
try_build 1 inventory/tier1_schema_helpers.py
try_build 2 inventory/tier2_feed_kernel.py
try_build 3 inventory/tier3_pandas_stub.py

echo "inventory_complete log=$LOG"
