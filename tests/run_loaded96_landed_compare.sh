#!/usr/bin/env bash
# Loaded-96 compare on an already-landed product tree (no patch apply/revert).
# See .cursor/plans/loaded96-strong-ab.plan.md.
# Pins H1@48 baseline; smoke 0.1h then 3h soak at WIDTH=96; writes compare JSON.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${ROOT_DIR}/test_runs/sync_timedb_bench"
QUEUE_LOG_DIR="${ROOT_DIR}/../test_runs"
mkdir -p "$OUT_DIR" "$QUEUE_LOG_DIR"

WIDTH="${HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH:-96}"
HOURS="${HPCPERFSTATS_LOADED48_HOURS:-3}"
SMOKE_HOURS="${HPCPERFSTATS_LOADED48_SMOKE_HOURS:-0.1}"
H1_PIN="${H1_BASE:-$OUT_DIR/loaded48_arm_baseline_a20a234d730c44eab7d5b240ae438d94.json}"

SKIP_BUILD_FLAG=()
if [[ "${SKIP_BUILD:-0}" == "1" ]]; then
  SKIP_BUILD_FLAG=(--skip-build)
fi

ts() { date +%Y%m%dT%H%M%S; }

run_loaded48() {
  local label="$1"
  local hours="$2"
  local log="${QUEUE_LOG_DIR}/loaded96-landed-${label}-$(ts).log"
  echo "=== loaded96 ${label} width=${WIDTH} hours=${hours} log=${log} ==="
  set +e
  HPCPERFSTATS_LOADED48_HOURS="$hours" \
  HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH="$WIDTH" \
    tests/run_sync_timedb_benchmark_workflow.sh --loaded48 "${SKIP_BUILD_FLAG[@]}" \
    >"$log" 2>&1
  local rc=$?
  set -u
  echo "=== loaded96 ${label} exit=${rc} log=${log} ==="
  return "$rc"
}

latest_baseline() {
  ls -t "$OUT_DIR"/loaded48_arm_baseline_*.json 2>/dev/null | head -1 || true
}

baseline_occupancy_ok() {
  local json="$1"
  /data/HPCPerfStats/.venv/bin/python3 -c \
    "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('occupancy_ok'))" \
    "$json"
}

smoke_gate_ok() {
  local json="$1"
  /data/HPCPerfStats/.venv/bin/python3 -c \
    "import json,sys; d=json.load(open(sys.argv[1])); w=int(d.get('ingest_width') or 0); peak=int(d.get('peak_post_warmup') or 0); ok=bool(d.get('occupancy_ok')); gate=str(d.get('occupancy_gate') or ''); print(ok and (gate in ('sustained','smoke_peak','') or peak>=max(1,int(w*0.75))))" \
    "$json"
}

write_compare() {
  local base_json="$1"
  local cand_json="$2"
  /data/HPCPerfStats/.venv/bin/python3 - "$base_json" "$cand_json" "$OUT_DIR" "$WIDTH" "$HOURS" <<'PY'
import json, sys, uuid
from pathlib import Path
base_p, cand_p, out_dir, width, hours = sys.argv[1:6]
base = json.loads(Path(base_p).read_text(encoding="utf-8"))
cand = json.loads(Path(cand_p).read_text(encoding="utf-8"))
base_mean = float(base["mean_files_per_s"])
base_lo = float(base["lower_ci_files_per_s"])
cand_lo = float(cand["lower_ci_files_per_s"])
gate_would_retain = cand_lo >= base_mean and cand_lo >= base_lo * 1.05
if cand.get("occupancy_ok") is False:
  gate_would_retain = False
run_id = uuid.uuid4().hex
payload = {
    "kind": "landed96_vs_h1",
    "retain": "n/a",
    "gate_would_retain": gate_would_retain,
    "gate": "throughput_report_only",
    "baseline_width": 48,
    "candidate_width": int(width),
    "hours": float(hours),
    "landed_waves": [
        "members_shard",
        "log_drain",
        "manifest_io",
        "claim_heap",
        "tar_ex",
    ],
    "baseline": base,
    "candidate": cand,
    "run_id": run_id,
    "note": (
        "landed five contention waves; no revert; compare WIDTH=%s hours=%s "
        "vs pinned H1@48; gate_would_retain is informational only"
        % (width, hours)
    ),
}
out = Path(out_dir) / ("landed96_vs_h1_%s.json" % run_id)
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(out)
print("gate_would_retain=%s" % gate_would_retain)
print(
    "cand_mean=%.6f base_mean=%.6f"
    % (float(cand["mean_files_per_s"]), base_mean)
)
PY
}

[[ -f "$H1_PIN" ]] || { echo "missing pinned H1 baseline: $H1_PIN" >&2; exit 1; }
echo "H1_PIN=$H1_PIN"

before="$(latest_baseline)"
if ! run_loaded48 smoke "$SMOKE_HOURS"; then
  echo "ERROR: smoke@${WIDTH} failed" >&2
  exit 1
fi
smoke="$(latest_baseline)"
[[ -n "$smoke" && "$smoke" != "$before" ]] || {
  echo "ERROR: no new smoke artifact" >&2
  exit 1
}
if [[ "$(smoke_gate_ok "$smoke")" != "True" ]]; then
  echo "ERROR: smoke occupancy gate false — aborting 3h soak" >&2
  exit 1
fi
echo "SMOKE_OK=$smoke"

before3="$(latest_baseline)"
if ! run_loaded48 soak3h "$HOURS"; then
  echo "ERROR: 3h soak@${WIDTH} failed" >&2
  exit 1
fi
after="$(latest_baseline)"
[[ -n "$after" && "$after" != "$before3" ]] || {
  echo "ERROR: no new 3h artifact" >&2
  exit 1
}
write_compare "$H1_PIN" "$after"
echo "=== loaded96 landed compare complete ==="
