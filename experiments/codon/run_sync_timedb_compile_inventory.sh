#!/usr/bin/env bash
# Attempt Codon build against every sync_timedb*.py; log first error per file.
set -euo pipefail

GIT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKSPACE_ROOT="$(cd "$GIT_ROOT/.." && pwd)"
CODON_HOME="${CODON_HOME:-/data/user/${USER}/codon-0.20.6}"
LOG_DIR="${HPCPERFSTATS_TEST_RUNS:-${WORKSPACE_ROOT}/test_runs}"
STAMP="$(date +%Y%m%dT%H%M%S)"
LOG="${LOG_DIR}/codon_sync_timedb_inventory_${STAMP}.log"
IMAGE="${HPCPERFSTATS_IMAGE:-localhost/hpcperfstats:latest}"
OUT_DIR="${LOG_DIR}/codon_inventory_build_${STAMP}"

mkdir -p "$LOG_DIR" "$OUT_DIR"

if [[ ! -x "${CODON_HOME}/bin/codon" ]]; then
  echo "missing_codon=${CODON_HOME}/bin/codon" >&2
  exit 1
fi

{
  echo "codon_sync_timedb_inventory stamp=$STAMP"
  echo "codon_home=$CODON_HOME"

  podman run --rm --user root \
    -e DEBIAN_FRONTEND=noninteractive \
    -v "${CODON_HOME}:/opt/codon:ro" \
    -v "${GIT_ROOT}/hpcperfstats:/work/hpcperfstats:ro" \
    -v "${OUT_DIR}:/out:rw" \
    "$IMAGE" \
    bash -lc '
set -euo pipefail
apt-get update -qq
apt-get install -y -qq clang >/tmp/apt-clang.log
CLANGXX="$(command -v clang++-19 || command -v clang++ || true)"
mkdir -p /opt/codon-shim
ln -sfn "$CLANGXX" /opt/codon-shim/g++
export PATH="/opt/codon-shim:/opt/codon/bin:${PATH}"
echo "codon=$(/opt/codon/bin/codon --version)"

pass=0
fail=0
mapfile -d "" -t files < <(
  find /work/hpcperfstats/dbload -maxdepth 1 -name "sync_timedb.py" -print0
  find /work/hpcperfstats/dbload/lib -maxdepth 1 -name "sync_timedb*.py" -print0 | sort -z
)
echo "target_count=${#files[@]}"
for f in "${files[@]}"; do
  [[ -z "$f" ]] && continue
  rel="${f#/work/}"
  base="$(basename "$f" .py)"
  echo "=== try $rel ==="
  if /opt/codon/bin/codon build -release -o "/out/${base}.o" "$f" >"/out/${base}.build.log" 2>&1; then
    echo "status=ok path=$rel"
    pass=$((pass + 1))
  else
    err="$(head -n 5 "/out/${base}.build.log" | tr "\n" " " | head -c 400)"
    echo "status=fail path=$rel err=${err}"
    fail=$((fail + 1))
  fi
done

echo "inventory_pass=$pass inventory_fail=$fail"
echo "inventory_complete=true"
'
} 2>&1 | tee "$LOG"

echo "inventory_log=$LOG"
rg -q 'inventory_complete=true' "$LOG"
