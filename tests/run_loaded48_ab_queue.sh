#!/usr/bin/env bash
# Serial loaded-48 A/B queue driver (see .cursor/plans/loaded48-ab-retest-queue.plan.md).
# Runs H0/H1 floor soaks and candidate --loaded48 arms after applying campaign patches.
# Does not change production INI. Rootless Podman via run_sync_timedb_benchmark_workflow.sh.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${ROOT_DIR}/test_runs/sync_timedb_bench"
PATCH_DIR="${ROOT_DIR}/test_runs/campaign_patches"
QUEUE_LOG_DIR="${ROOT_DIR}/../test_runs"
mkdir -p "$OUT_DIR" "$QUEUE_LOG_DIR"

HOURS="${HPCPERFSTATS_LOADED48_HOURS:-6}"
SMOKE_HOURS="${HPCPERFSTATS_LOADED48_SMOKE_HOURS:-0.1}"
SKIP_BUILD_FLAG=()
if [[ "${SKIP_BUILD:-0}" == "1" ]]; then
  SKIP_BUILD_FLAG=(--skip-build)
fi

ts() { date +%Y%m%dT%H%M%S; }

run_loaded48() {
  local label="$1"
  local hours="$2"
  local log="${QUEUE_LOG_DIR}/loaded48-queue-${label}-$(ts).log"
  echo "=== loaded48 ${label} hours=${hours} log=${log} ==="
  set +e
  HPCPERFSTATS_LOADED48_HOURS="$hours" \
    tests/run_sync_timedb_benchmark_workflow.sh --loaded48 "${SKIP_BUILD_FLAG[@]}" \
    >"$log" 2>&1
  local rc=$?
  set -u
  echo "=== loaded48 ${label} exit=${rc} log=${log} ==="
  return "$rc"
}

latest_baseline() {
  ls -t "$OUT_DIR"/loaded48_arm_baseline_*.json 2>/dev/null | head -1 || true
}

baseline_occupancy_ok() {
  local json="$1"
  /data/HPCPerfStats/.venv/bin/python3 -c \
    "import json,sys; print(json.load(open(sys.argv[1])).get('occupancy_ok'))" \
    "$json"
}

write_ab_from_baselines() {
  local wave="$1"
  local base_json="$2"
  local cand_json="$3"
  local prefix="$4"
  /data/HPCPerfStats/.venv/bin/python3 - "$wave" "$base_json" "$cand_json" "$prefix" "$OUT_DIR" <<'PY'
import json, sys, uuid
from pathlib import Path
wave, base_p, cand_p, prefix, out_dir = sys.argv[1:6]
base = json.loads(Path(base_p).read_text(encoding="utf-8"))
cand = json.loads(Path(cand_p).read_text(encoding="utf-8"))
base_mean = float(base["mean_files_per_s"])
base_lo = float(base["lower_ci_files_per_s"])
cand_lo = float(cand["lower_ci_files_per_s"])
retain = cand_lo >= base_mean and cand_lo >= base_lo * 1.05
# Also require candidate occupancy when present.
if cand.get("occupancy_ok") is False:
  retain = False
run_id = uuid.uuid4().hex
payload = {
    "kind": "loaded48_ab",
    "wave": wave,
    "retain": retain,
    "gate": "throughput",
    "baseline": base,
    "candidate": cand,
    "run_id": run_id,
    "note": "loaded-48 floor vs candidate; same E6 retain gate; not a production INI change",
}
out = Path(out_dir) / ("%s_%s.json" % (prefix, run_id))
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(out)
print("retain=%s" % retain)
PY
}

apply_patch() {
  local patch="$1"
  echo "=== apply $(basename "$patch") ==="
  if git apply --check "$patch" 2>/dev/null; then
    git apply "$patch"
  elif git apply --3way --check "$patch" 2>/dev/null; then
    git apply --3way "$patch"
  else
    echo "WARN: git apply failed for $patch — see NOTES; skipping arm" >&2
    return 1
  fi
}

revert_patch() {
  local patch="$1"
  echo "=== revert $(basename "$patch") ==="
  git apply -R "$patch" 2>/dev/null || true
}

PHASE="${1:-all}"

case "$PHASE" in
  h0)
    run_loaded48 h0 "$SMOKE_HOURS"
    ;;
  h1)
    run_loaded48 h1 "$HOURS"
    ;;
  all|queue)
    if [[ "${RUN_H0:-0}" == "1" ]]; then
      run_loaded48 h0 "$SMOKE_HOURS" || echo "WARN: H0 failed; continuing to H1" >&2
    fi
    if ! run_loaded48 h1 "$HOURS"; then
      echo "ERROR: H1 soak failed (workflow non-zero); see loaded48-queue-h1-*.log" >&2
      # Still capture artifact if written, but do not run A/B without a green floor.
      H1_BASE="$(latest_baseline)"
      if [[ -n "$H1_BASE" ]] && [[ "$(baseline_occupancy_ok "$H1_BASE")" == "True" ]]; then
        echo "H1 artifact occupancy_ok despite workflow rc; continuing"
      else
        echo "H1 occupancy_ok missing/false — aborting candidate queue" >&2
        exit 1
      fi
    fi
    H1_BASE="$(latest_baseline)"
    echo "H1_BASE=$H1_BASE"
    [[ -n "$H1_BASE" ]] || { echo "missing H1 baseline artifact" >&2; exit 1; }
    if [[ "$(baseline_occupancy_ok "$H1_BASE")" != "True" ]]; then
      echo "H1 occupancy_ok is false — aborting candidate queue" >&2
      exit 1
    fi
    cp -a "$H1_BASE" "$OUT_DIR/loaded48_floor_h1_$(basename "$H1_BASE")"

    /data/HPCPerfStats/.venv/bin/python3 - "$H1_BASE" "$OUT_DIR" <<'PY'
import json, sys, uuid
from pathlib import Path
base = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out_dir = Path(sys.argv[2])
run_id = uuid.uuid4().hex
payload = {
    "kind": "loaded48_e7_record",
    "retain": "operator-override",
    "floor": base,
    "run_id": run_id,
    "note": "E7 already in tree (user keep); floor soak is E7-on",
}
out = out_dir / ("loaded48_e7_ab_%s.json" % run_id)
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(out)
PY

    run_candidate_arm() {
      local wave="$1"
      local patch="$2"
      local ab_prefix="$3"
      echo "=== candidate arm ${wave} ==="
      apply_patch "$patch" || return 0
      local before
      before="$(latest_baseline)"
      if ! run_loaded48 "cand_${wave}" "$HOURS"; then
        echo "candidate soak failed for $wave" >&2
        revert_patch "$patch"
        return 0
      fi
      local after
      after="$(latest_baseline)"
      if [[ -z "$after" || "$after" == "$before" ]]; then
        echo "WARN: no new baseline artifact for $wave" >&2
        revert_patch "$patch"
        return 0
      fi
      write_ab_from_baselines "$wave" "$H1_BASE" "$after" "$ab_prefix"
      local ab
      ab="$(ls -t "$OUT_DIR"/${ab_prefix}_*.json | head -1)"
      local retain
      retain="$(/data/HPCPerfStats/.venv/bin/python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['retain'])" "$ab")"
      if [[ "$retain" != "True" ]]; then
        revert_patch "$patch"
      else
        echo "RETAIN $wave — leaving product patch applied"
      fi
    }

    run_candidate_arm e6 "$PATCH_DIR/e6_feed_line.patch" e6_parse_feed_ab || true
    for wave in park_resume manifest_io members_shard claim_heap tar_ex pool_split log_drain discover telem_tls; do
      run_candidate_arm "$wave" "$PATCH_DIR/contention_${wave}.patch" "contention_${wave}_ab" || true
    done
    echo "=== queue complete ==="
    ;;
  *)
    echo "usage: $0 [h0|h1|all]" >&2
    exit 2
    ;;
esac
