#!/usr/bin/env bash
# Run Codon-native E6 feed bench + /opt/python3.14t IncrementalStatsParser
# inside the pipeline image. Codon hardcodes invoking ``g++`` for the link
# step; we install Clang and PATH-shim ``g++`` -> ``clang++`` (no g++ package).
set -euo pipefail

GIT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WORKSPACE_ROOT="$(cd "$GIT_ROOT/.." && pwd)"
CODON_HOME="${CODON_HOME:-/data/user/${USER}/codon}"
LOG_DIR="${HPCPERFSTATS_TEST_RUNS:-${WORKSPACE_ROOT}/test_runs}"
STAMP="$(date +%Y%m%dT%H%M%S)"
LOG="${LOG_DIR}/codon_vs_314t_${STAMP}.log"
IMAGE="${HPCPERFSTATS_IMAGE:-localhost/hpcperfstats:latest}"
REPS="${HPCPERFSTATS_CODON_BENCH_REPS:-80}"
SAMPLES="${HPCPERFSTATS_CODON_BENCH_SAMPLES:-400}"

mkdir -p "$LOG_DIR"

if [[ ! -x "${CODON_HOME}/bin/codon" ]]; then
  echo "missing_codon=${CODON_HOME}/bin/codon" >&2
  exit 1
fi

{
  echo "codon_vs_314t stamp=$STAMP image=$IMAGE"
  echo "codon_home=$CODON_HOME"
  echo "reps=$REPS samples=$SAMPLES"

  podman run --rm --user root \
    -e DEBIAN_FRONTEND=noninteractive \
    -e HPCPERFSTATS_CODON_BENCH_REPS="$REPS" \
    -e HPCPERFSTATS_CODON_BENCH_SAMPLES="$SAMPLES" \
    -v "${CODON_HOME}:/opt/codon:ro" \
    -v "${GIT_ROOT}/tests/experiments/codon:/work/tests/experiments/codon:ro" \
    -v "${GIT_ROOT}/hpcperfstats:/work/hpcperfstats:ro" \
    "$IMAGE" \
    bash -lc '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Runtime image has no C++ toolchain; prefer Clang (pipeline bake uses Clang).
# Codon still execs argv "g++" - shim that name to clang++.
apt-get install -y -qq clang >/tmp/apt-clang.log
CLANGXX="$(command -v clang++-19 || command -v clang++ || true)"
if [[ -z "$CLANGXX" ]]; then
  echo "clangxx=missing" >&2
  exit 1
fi
mkdir -p /opt/codon-shim
ln -sfn "$CLANGXX" /opt/codon-shim/g++
export PATH="/opt/codon-shim:${PATH}"
echo "clangxx=$("$CLANGXX" --version | head -1)"
echo "gpp_shim=$(readlink -f /opt/codon-shim/g++)"
echo "codon=$(/opt/codon/bin/codon --version)"
echo "ft314t=$(/opt/python3.14t/bin/python -V)"

# --- Codon native arm ---
echo "=== codon native ==="
if /opt/codon/bin/codon run -release /work/tests/experiments/codon/codon_native_e6_bench.py; then
  echo "codon_run=pass"
else
  echo "codon_run=fail"
  exit 1
fi

# --- 3.14t production IncrementalStatsParser + CPython FeedKernel ---
echo "=== cpython 3.14t ==="
export PYTHONPATH="/work/tests/experiments/codon:/work:${PYTHONPATH:-}"
# Prefer image-installed package; overlay live experiments + optional live package
/opt/python3.14t/bin/python - << "PY"
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/work/tests/experiments/codon")
sys.path.insert(0, "/work")

from e6_corpus import build_e6_payload_lines
from parse_kernel import FeedKernel
from hpcperfstats.dbload.lib.sync_timedb_parsing import IncrementalStatsParser

reps = int(os.environ.get("HPCPERFSTATS_CODON_BENCH_REPS", "80"))
n = int(os.environ.get("HPCPERFSTATS_CODON_BENCH_SAMPLES", "400"))
lines = build_e6_payload_lines(n)

w = IncrementalStatsParser(0)
w.feed_lines(lines)
w.finish()
t0 = time.perf_counter()
for _ in range(reps):
    p = IncrementalStatsParser(0)
    p.feed_lines(lines)
    p.finish()
inc_s = time.perf_counter() - t0

wk = FeedKernel(0)
wk.feed_lines(lines)
t1 = time.perf_counter()
for _ in range(reps):
    k = FeedKernel(0)
    k.feed_lines(lines)
    _ = k.stats_len
ker_s = time.perf_counter() - t1

print(
    "ft314t_incremental_s=%.6f ft314t_kernel_s=%.6f "
    "reps=%d samples=%d"
    % (inc_s, ker_s, reps, n)
)
Path("/tmp/ft314t_inc_s").write_text("%.9f" % inc_s)
PY

# Gate: Codon vs 3.14t IncrementalStatsParser
python3 - << "PY"
import pathlib
import re
import sys

# The real gate runs host-side over the teed log after podman exits.
print("gate_compute=deferred_to_host_parser")
PY
'
} 2>&1 | tee "$LOG"

# The gate passes when Codon is >= 1.25x faster than 3.14t IncrementalStatsParser.
/data/HPCPerfStats/.venv/bin/python3 - << PY
import pathlib
import re
import sys

log = pathlib.Path("$LOG")
text = log.read_text()
m_codon = re.search(r"codon_native_elapsed_s=([0-9.eE+-]+)", text)
m_ft = re.search(r"ft314t_incremental_s=([0-9.eE+-]+)", text)
if not m_codon or not m_ft:
    print("gate_parse=fail codon=%s ft=%s" % (bool(m_codon), bool(m_ft)))
    print("bench_log=$LOG")
    sys.exit(1)
codon_s = float(m_codon.group(1))
ft_s = float(m_ft.group(1))
ratio = ft_s / codon_s if codon_s > 0 else 0.0
gate = ratio >= 1.25
line = (
    "codon_vs_314t_ratio=%.3f codon_s=%.6f ft314t_incremental_s=%.6f "
    "gate_1p25x=%s"
    % (ratio, codon_s, ft_s, gate)
)
print(line)
with log.open("a") as fh:
    fh.write(line + "\n")
print("bench_log=$LOG")
sys.exit(0 if m_codon and m_ft else 1)
PY

echo "bench_log=$LOG"
