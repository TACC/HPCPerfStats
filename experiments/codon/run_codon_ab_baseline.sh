#!/usr/bin/env bash
# A/B: Arm A = 3.14t Incremental+collapse; Arm B = image-baked Codon pyext.
# Production pyext lives at /opt/hpcperfstats/codon_parse (Dockerfile codon-build).
# Optional: HPCPERFSTATS_CODON_PYEXT_DIR mounts a host override at /pyext.
set -euo pipefail

GIT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORKSPACE_ROOT="$(cd "$GIT_ROOT/.." && pwd)"
CODON_HOME="${CODON_HOME:-/data/user/${USER}/codon-0.20.6}"
LOG_DIR="${HPCPERFSTATS_TEST_RUNS:-${WORKSPACE_ROOT}/test_runs}"
STAMP="$(date +%Y%m%dT%H%M%S)"
B_ONLY="${HPCPERFSTATS_CODON_AB_B_ONLY:-0}"
if [[ "$B_ONLY" == "1" || "$B_ONLY" == "true" || "$B_ONLY" == "yes" ]]; then
  LOG="${LOG_DIR}/codon_ab_${STAMP}_nogil.log"
else
  LOG="${LOG_DIR}/codon_ab_${STAMP}.log"
fi
IMAGE="${HPCPERFSTATS_IMAGE:-localhost/hpcperfstats:latest}"
REPS="${HPCPERFSTATS_CODON_BENCH_REPS:-40}"
SAMPLES="${HPCPERFSTATS_CODON_BENCH_SAMPLES:-200}"
# Frozen Arm A from test_runs/codon_ab_20261021T185743.log (B-only remeasure).
FROZEN_A="${HPCPERFSTATS_CODON_AB_FROZEN_A:-2.144}"

mkdir -p "$LOG_DIR"

PYEXT_DIR="${HPCPERFSTATS_CODON_PYEXT_DIR:-}"
PODMAN_PYEXT_ARGS=(-e HPCPERFSTATS_CODON_ARM_B_MODE=pyext)
if [[ -n "${PYEXT_DIR}" \
  && -f "${PYEXT_DIR}/hpcperfstats_codon_parse.so" \
  && -f "${PYEXT_DIR}/libhpcperfstats_codon_kernel.so" ]]; then
  PODMAN_PYEXT_ARGS+=(-v "${PYEXT_DIR}:/pyext:ro" -e HPCPERFSTATS_CODON_PYEXT_MOUNT=1)
  PYEXT_SRC="host_mount:${PYEXT_DIR}"
else
  PYEXT_SRC="image:/opt/hpcperfstats/codon_parse"
fi

{
  echo "codon_ab stamp=$STAMP image=$IMAGE"
  echo "codon_home=$CODON_HOME"
  echo "reps=$REPS samples=$SAMPLES corpus_id=e6_dual_dev_v1"
  echo "arm_b_mode=pyext pyext_src=${PYEXT_SRC}"
  echo "b_only=${B_ONLY} frozen_a=${FROZEN_A}"

  podman run --rm --user root \
    -e DEBIAN_FRONTEND=noninteractive \
    -e HPCPERFSTATS_CODON_BENCH_REPS="$REPS" \
    -e HPCPERFSTATS_CODON_BENCH_SAMPLES="$SAMPLES" \
    -e "HPCPERFSTATS_CODON_AB_B_ONLY=${B_ONLY}" \
    -e "HPCPERFSTATS_CODON_AB_FROZEN_A=${FROZEN_A}" \
    "${PODMAN_PYEXT_ARGS[@]}" \
    -v "${GIT_ROOT}/experiments/codon:/work/experiments/codon:ro" \
    -v "${GIT_ROOT}/hpcperfstats:/work/hpcperfstats:ro" \
    "$IMAGE" \
    bash -lc '
set -euo pipefail
echo "ft314t_version=$(/opt/python3.14t/bin/python -V)"
echo "arm_b_mode=${HPCPERFSTATS_CODON_ARM_B_MODE}"

REPS="${HPCPERFSTATS_CODON_BENCH_REPS}"
SAMPLES="${HPCPERFSTATS_CODON_BENCH_SAMPLES}"

echo "=== arm_b pyext ==="
if [[ "${HPCPERFSTATS_CODON_PYEXT_MOUNT:-0}" == "1" && -f /pyext/hpcperfstats_codon_parse.so ]]; then
  PYEXT_ROOT=/pyext
elif [[ -f /opt/hpcperfstats/codon_parse/hpcperfstats_codon_parse.so ]]; then
  PYEXT_ROOT=/opt/hpcperfstats/codon_parse
else
  echo "missing_pyext_in_image_or_mount" >&2
  exit 1
fi
export PYTHONPATH="${PYEXT_ROOT}:/work/hpcperfstats/dbload/codon:/work/experiments/codon:/work:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="/opt/zlib-ng/lib:/opt/python3.14t/lib"
echo "pyext_root=${PYEXT_ROOT}"
/opt/python3.14t/bin/python - <<'\''PY'\''
import os, sys, time
sys.path.insert(0, os.environ["PYTHONPATH"].split(":")[0])
sys.path.insert(0, "/work/hpcperfstats/dbload/codon")
import hpcperfstats_codon_parse as m
from feed_collapse import build_e6_payload_lines

reps = int(os.environ["HPCPERFSTATS_CODON_BENCH_REPS"])
n = int(os.environ["HPCPERFSTATS_CODON_BENCH_SAMPLES"])
lines = build_e6_payload_lines(n)
warm = m.feed_and_collapse_fingerprint(lines)
print(
  "codon_feed_collapse_fingerprint raw_stats="
  + str(warm[0])
  + " collapsed_rows="
  + str(warm[1])
  + " collapsed_value_sum="
  + str(warm[2])
  + " proc_rows="
  + str(warm[3])
)
t0 = time.perf_counter()
for _ in range(reps):
  _ = m.feed_and_collapse_fingerprint(lines)
elapsed = time.perf_counter() - t0
print(
  "codon_feed_collapse_elapsed_s="
  + str(elapsed)
  + " reps="
  + str(reps)
  + " samples="
  + str(n)
)
print("arm_b_mode=pyext")
PY

if [[ "${HPCPERFSTATS_CODON_AB_B_ONLY:-0}" == "1" ]]; then
  echo "=== arm_a frozen (B-only) ==="
  echo "arm_a_elapsed_s=${HPCPERFSTATS_CODON_AB_FROZEN_A}"
  echo "arm_a_source=frozen_codon_ab_20261021T185743"
  echo "arm_a_collapsed_rows=frozen"
else
echo "=== arm_a 3.14t incremental+collapse ==="
export PYTHONPATH="/work/experiments/codon:/work:${PYTHONPATH:-}"
/opt/python3.14t/bin/python - <<'\''PY'\''
import os, sys, time
from pathlib import Path
sys.path.insert(0, "/work/experiments/codon")
sys.path.insert(0, "/work")
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  IncrementalStatsParser,
  build_stats_dataframes,
  compute_deltas_and_arc,
  stats_payload_to_records,
)

reps = int(os.environ["HPCPERFSTATS_CODON_BENCH_REPS"])
n = int(os.environ["HPCPERFSTATS_CODON_BENCH_SAMPLES"])

def build_lines(n_samples: int):
  lines = []
  lines.append(
    "!cpu user,W=64,U=1 nice,W=64,U=1 system,W=64,U=1 idle,W=64,U=1 "
    "iowait,W=64,U=1 irq,W=64,U=1 softirq,W=64,U=1"
  )
  lines.append(
    "!host_proc uid,W=32 vm_peak,W=64 vm_size,W=64 vm_lck,W=64 "
    "vm_hwm,W=64 vm_rss,W=64 vm_data,W=64 vm_stk,W=64 vm_exe,W=64 "
    "vm_lib,W=64 vm_pte,W=64 vm_swap,W=64 threads,W=32"
  )
  t0 = 1_700_000_000.0
  for i in range(n_samples):
    t = t0 + float(i)
    lines.append(f"{t} JID1 hostA")
    vals = " ".join(str(float(i * 7 + k)) for k in range(7))
    lines.append(f"cpu 0 @full {vals}")
    vals2 = " ".join(str(float(i * 7 + k + 0.5)) for k in range(7))
    lines.append(f"cpu 1 @full {vals2}")
    lines.append(
      "host_proc 1234 @full "
      + " ".join(
        str(x)
        for x in [
          1000 + i, 2000 + i, 3000 + i, 0, 4000 + i, 5000 + i,
          6000 + i, 7000 + i, 8000 + i, 9000 + i, 10000 + i, 0,
          2 + (i % 4),
        ]
      )
    )
  return lines

def arm_a_once(lines):
  p = IncrementalStatsParser(0)
  p.feed_lines(lines)
  stats, procs = p.finish()
  stats = stats_payload_to_records(stats)
  stats_df, _proc_df = build_stats_dataframes(stats, procs)
  return compute_deltas_and_arc(stats_df)

lines = build_lines(n)
_ = arm_a_once(lines)

t0 = time.perf_counter()
collapsed = None
for _ in range(reps):
  collapsed = arm_a_once(lines)
arm_a = time.perf_counter() - t0
print(f"arm_a_elapsed_s={arm_a:.9f}")
print(f"arm_a_collapsed_rows={0 if collapsed is None else len(collapsed)}")
vsum = 0.0
if collapsed is not None and len(collapsed) and "value" in collapsed.columns:
  vsum = float(collapsed["value"].sum())
print(f"arm_a_value_sum={vsum}")
Path("/tmp/arm_a_elapsed_s").write_text(f"{arm_a:.12f}")
PY
fi
'

} 2>&1 | tee "$LOG"

export HPCPERFSTATS_CODON_AB_LOG="$LOG"
/data/HPCPerfStats/.venv/bin/python3 - <<'PY'
import os
import pathlib
import re
import sys

log = pathlib.Path(os.environ["HPCPERFSTATS_CODON_AB_LOG"])
text = log.read_text()
m_a = re.search(r"arm_a_elapsed_s=([0-9.eE+-]+)", text)
m_b = re.search(r"codon_feed_collapse_elapsed_s=([0-9.eE+-]+)", text)
if not m_a or not m_b:
  print("gate_parse=fail", bool(m_a), bool(m_b))
  sys.exit(1)
a = float(m_a.group(1))
b = float(m_b.group(1))
ratio = a / b if b > 0 else 0.0
gate = ratio >= 1.25
fp_ok = (
  "codon_feed_collapse_fingerprint" in text and "arm_a_collapsed_rows=" in text
)
mode = "pyext" if "arm_b_mode=pyext" in text else "codon_run"
line = (
  f"arm_a_elapsed_s={a:.9f} arm_b_elapsed_s={b:.9f} "
  f"ratio_a_over_b={ratio:.3f} gate_1p25x={gate} fingerprint_ok={fp_ok} "
  f"arm_b_mode={mode}"
)
print(line)
with log.open("a") as fh:
  fh.write(line + "\n")
  fh.write(f"bench_log={log}\n")
sys.exit(0 if gate and fp_ok else 1)
PY

echo "ab_log=$LOG"
