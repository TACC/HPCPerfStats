#!/usr/bin/env bash
# 1h @ width-48 loaded soak A/B: ORM bulk_create (baseline) vs COPY insert (candidate).
# Detach-friendly: intended to be launched via nohup/setsid.
# Does not change production INI.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${ROOT_DIR}/test_runs/sync_timedb_bench"
WS_LOG_DIR="${ROOT_DIR}/../test_runs"
mkdir -p "$OUT_DIR" "$WS_LOG_DIR"

HOURS="${HPCPERFSTATS_LOADED48_HOURS:-1}"
WIDTH="${HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH:-48}"
SKIP_BUILD_FLAG=()
if [[ "${SKIP_BUILD:-1}" == "1" ]]; then
  SKIP_BUILD_FLAG=(--skip-build)
fi

ts() { date +%Y%m%dT%H%M%S; }

run_arm() {
  local arm="$1"
  local log="${WS_LOG_DIR}/loaded48-insert-${arm}-w${WIDTH}-${HOURS}h-$(ts).log"
  echo "=== loaded48 insert-arm=${arm} width=${WIDTH} hours=${HOURS} log=${log} ==="
  set +e
  HPCPERFSTATS_LOADED48_HOURS="$HOURS" \
  HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH="$WIDTH" \
  HPCPERFSTATS_HOST_INSERT_ARM="$arm" \
  HPCPERFSTATS_PROC_INSERT_ARM="$arm" \
    tests/run_sync_timedb_benchmark_workflow.sh --loaded48 --keep-env \
      "${SKIP_BUILD_FLAG[@]}" \
      >"$log" 2>&1
  local rc=$?
  set -u
  echo "=== loaded48 insert-arm=${arm} exit=${rc} log=${log} ==="
  echo "$log"
  return "$rc"
}

latest_arm() {
  local arm="$1"
  ls -t "$OUT_DIR"/loaded48_arm_"${arm}"_*.json 2>/dev/null | head -1 || true
}

echo "Starting insert-path loaded48 A/B width=${WIDTH} hours=${HOURS}"
run_arm baseline || true
BASE_JSON="$(latest_arm baseline)"
echo "baseline artifact: ${BASE_JSON:-MISSING}"

run_arm candidate || true
CAND_JSON="$(latest_arm candidate)"
echo "candidate artifact: ${CAND_JSON:-MISSING}"

if [[ -z "${BASE_JSON}" || -z "${CAND_JSON}" ]]; then
  echo "missing arm artifact(s); abort compare" >&2
  exit 1
fi

/data/HPCPerfStats/.venv/bin/python3 - "$BASE_JSON" "$CAND_JSON" "$OUT_DIR" <<'PY'
import json, sys, uuid
from pathlib import Path
base_p, cand_p, out_dir = sys.argv[1:4]
base = json.loads(Path(base_p).read_text(encoding="utf-8"))
cand = json.loads(Path(cand_p).read_text(encoding="utf-8"))
base_mean = float(base["mean_files_per_s"])
base_lo = float(base["lower_ci_files_per_s"])
cand_lo = float(cand["lower_ci_files_per_s"])
retain = cand_lo >= base_mean and cand_lo >= base_lo * 1.05
if cand.get("occupancy_ok") is False:
  retain = False
run_id = uuid.uuid4().hex
payload = {
    "kind": "loaded48_insert_ab",
    "wave": "host_proc_copy",
    "hours": base.get("hours"),
    "ingest_width": base.get("ingest_width"),
    "retain": retain,
    "gate": "throughput",
    "baseline": base,
    "candidate": cand,
    "baseline_path": base_p,
    "candidate_path": cand_p,
    "run_id": run_id,
    "note": (
        "1h@48 loaded soak: bulk_create (baseline) vs COPY insert (candidate); "
        "E6 retain gate on files/s; occupancy_ok required on candidate"
    ),
}
out = Path(out_dir) / ("loaded48_insert_ab_%s.json" % run_id)
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("compare", out, "retain=%s" % retain)
print(
    "baseline_mean=%.6f candidate_mean=%.6f"
    % (base_mean, float(cand["mean_files_per_s"]))
)
PY

echo "loaded48 insert A/B complete."
