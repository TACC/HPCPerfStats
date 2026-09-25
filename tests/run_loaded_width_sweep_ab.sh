#!/usr/bin/env bash
# Loaded width-sweep A/B: 48 → 64 → 96 (0.1h smoke + 3h soak each).
# Baseline = this-run 3h@48; compare 64/96 report-only (never revert).
# See .cursor/plans/width-sweep-ab-3h.plan.md.
# Launch detached: nohup setsid -f bash tests/run_loaded_width_sweep_ab.sh …
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${ROOT_DIR}/test_runs/sync_timedb_bench"
QUEUE_LOG_DIR="${ROOT_DIR}/../test_runs"
mkdir -p "$OUT_DIR" "$QUEUE_LOG_DIR"

HOURS="${HPCPERFSTATS_LOADED48_HOURS:-3}"
SMOKE_HOURS="${HPCPERFSTATS_LOADED48_SMOKE_HOURS:-0.1}"
WIDTHS="${HPCPERFSTATS_WIDTH_SWEEP_WIDTHS:-48 64 96}"
PY="${HPCPERFSTATS_HOST_PYTHON:-/data/HPCPerfStats/.venv/bin/python3}"

SKIP_BUILD_FLAG=()
if [[ "${SKIP_BUILD:-0}" == "1" ]]; then
  SKIP_BUILD_FLAG=(--skip-build)
fi

ts() { date +%Y%m%dT%H%M%S; }

LAST_ARM_PATH=""

run_loaded48() {
  local label="$1"
  local width="$2"
  local hours="$3"
  local log="${QUEUE_LOG_DIR}/loaded-width-sweep-w${width}-${label}-$(ts).log"
  echo "=== width_sweep ${label} width=${width} hours=${hours} log=${log} ==="
  set +e
  HPCPERFSTATS_LOADED48_HOURS="$hours" \
  HPCPERFSTATS_SYNC_TIMEDB_LOADED48_WIDTH="$width" \
    tests/run_sync_timedb_benchmark_workflow.sh --loaded48 "${SKIP_BUILD_FLAG[@]}" \
    >"$log" 2>&1
  local rc=$?
  set -u
  echo "=== width_sweep ${label} width=${width} exit=${rc} log=${log} ==="
  return "$rc"
}

latest_baseline() {
  ls -t "$OUT_DIR"/loaded48_arm_baseline_*.json 2>/dev/null | head -1 || true
}

smoke_gate_ok() {
  local json="$1"
  "$PY" -c \
    "import json,sys; d=json.load(open(sys.argv[1])); w=int(d.get('ingest_width') or 0); peak=int(d.get('peak_post_warmup') or 0); ok=bool(d.get('occupancy_ok')); gate=str(d.get('occupancy_gate') or ''); print(ok and (gate in ('sustained','smoke_peak','') or peak>=max(1,int(w*0.75))))" \
    "$json"
}

run_one_width() {
  local width="$1"
  local before smoke after before3

  LAST_ARM_PATH=""
  before="$(latest_baseline)"
  if ! run_loaded48 smoke "$width" "$SMOKE_HOURS"; then
    echo "ERROR: smoke@${width} workflow failed" >&2
    return 1
  fi
  smoke="$(latest_baseline)"
  [[ -n "$smoke" && "$smoke" != "$before" ]] || {
    echo "ERROR: no new smoke artifact for width=${width}" >&2
    return 1
  }
  if [[ "$(smoke_gate_ok "$smoke")" != "True" ]]; then
    echo "ERROR: smoke occupancy gate false @${width} — aborting 3h soak" >&2
    return 1
  fi
  echo "SMOKE_OK width=${width} path=${smoke}"

  # Rebuild only on the first arm; later widths reuse the image.
  SKIP_BUILD=1
  SKIP_BUILD_FLAG=(--skip-build)

  before3="$(latest_baseline)"
  if ! run_loaded48 soak3h "$width" "$HOURS"; then
    echo "ERROR: ${HOURS}h soak@${width} workflow failed" >&2
    return 1
  fi
  after="$(latest_baseline)"
  [[ -n "$after" && "$after" != "$before3" ]] || {
    echo "ERROR: no new ${HOURS}h artifact for width=${width}" >&2
    return 1
  }
  echo "SOAK_OK width=${width} path=${after}"
  LAST_ARM_PATH="$after"
  return 0
}

BASELINE_PATH=""
declare -A ARM_PATHS=()

echo "=== width_sweep start widths=${WIDTHS} smoke=${SMOKE_HOURS}h soak=${HOURS}h ==="

for W in $WIDTHS; do
  if ! run_one_width "$W"; then
    echo "ERROR: width ${W} failed; stopping sweep" >&2
    exit 1
  fi
  if [[ "$W" == "48" ]]; then
    BASELINE_PATH="$LAST_ARM_PATH"
  else
    ARM_PATHS["$W"]="$LAST_ARM_PATH"
  fi
done

[[ -n "$BASELINE_PATH" ]] || {
  echo "ERROR: missing baseline @48" >&2
  exit 1
}

ARM_ARGS=()
for W in $(printf '%s\n' "${!ARM_PATHS[@]}" | sort -n); do
  ARM_ARGS+=("$W" "${ARM_PATHS[$W]}")
done

"$PY" - "$ROOT_DIR" "$BASELINE_PATH" "$HOURS" "${ARM_ARGS[@]}" <<'PY'
import json, sys, uuid
from pathlib import Path

repo_root = Path(sys.argv[1])
sys.path.insert(0, str(repo_root))

from tests.sync_timedb_benchmark.screening_runner import (
    build_width_sweep_ab_manifest,
    write_screening_artifact,
)

base_p = Path(sys.argv[2])
hours = float(sys.argv[3])
pairs = sys.argv[4:]
arms = {}
for i in range(0, len(pairs), 2):
  w = int(pairs[i])
  arms[w] = json.loads(Path(pairs[i + 1]).read_text(encoding="utf-8"))
baseline = json.loads(base_p.read_text(encoding="utf-8"))
run_id = uuid.uuid4().hex
payload = build_width_sweep_ab_manifest(
    baseline=baseline,
    arms=arms,
    hours=hours,
    run_id=run_id,
)
out = write_screening_artifact(
    payload,
    repo_root=repo_root,
    prefix="width_sweep_ab",
)
print(out)
print("gates=%s" % payload["gates"])
print(
    "baseline_mean=%.6f"
    % float(baseline["mean_files_per_s"])
)
for w, cand in sorted(arms.items()):
  print(
      "width=%s mean=%.6f occ=%s"
      % (w, float(cand["mean_files_per_s"]), cand.get("occupancy_ok"))
  )
PY

echo "=== width_sweep complete baseline=${BASELINE_PATH} ==="
