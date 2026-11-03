#!/usr/bin/env bash
# Record idle-node scheduling jitter with and without hpcperfstatsd (cyclictest).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DURATION="${DURATION:-60}"
OUT=""
LABEL="${LABEL:-unknown}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
Usage: capture_jitter.sh --label daemon_on|daemon_off [--duration 60] [--out DIR]

Prefers cyclictest (rt-tests). Falls back to a userspace sleep-loop histogram
if cyclictest is missing. Run on a dedicated idle node; do not mix with a
user job. Capture daemon_on and daemon_off, then:

  python3 bin/summarize_jitter.py --on ON_DIR --off OFF_DIR --out results/jitter.json
EOF
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "${OUT}" ]]; then
  OUT="$(stamp_dir "${ROOT}" "jitter_${LABEL}")"
else
  mkdir -p "${OUT}"
fi

printf '%s\n' "${LABEL}" > "${OUT}/label.txt"
hostname -f > "${OUT}/host.txt" 2>/dev/null || hostname > "${OUT}/host.txt"
pgrep -a hpcperfstatsd > "${OUT}/daemon_ps.txt" || true

if command -v cyclictest >/dev/null 2>&1; then
  log "cyclictest -l $((DURATION * 1000)) (approx ${DURATION}s at 1 ms interval)"
  cyclictest -l "$((DURATION * 1000))" -m -q -h 100 -p 80 \
    > "${OUT}/cyclictest.txt" || cyclictest -l "$((DURATION * 1000))" -q \
    > "${OUT}/cyclictest.txt"
else
  log "WARN: cyclictest not found; using userspace sleep-loop fallback"
  export MT_DURATION="${DURATION}" MT_OUT="${OUT}"
  "${HPCPERFSTATS_PYTHON:-python3}" - <<'PY'
import json
import os
import pathlib
import time

duration = int(os.environ["MT_DURATION"])
end = time.monotonic() + duration
interval = 0.001
errors = []
next_t = time.monotonic()
while time.monotonic() < end:
    next_t += interval
    now = time.monotonic()
    sleep_for = next_t - now
    if sleep_for > 0:
        time.sleep(sleep_for)
    late = (time.monotonic() - next_t) * 1e6
    if late < 0:
        late = 0.0
    errors.append(late)
path = pathlib.Path(os.environ["MT_OUT"])
(path / "sleep_loop.txt").write_text(
    json.dumps({
        "n": len(errors),
        "max_us": max(errors) if errors else None,
        "p99_us": sorted(errors)[int(0.99 * (len(errors) - 1))] if errors else None,
        "mean_us": (sum(errors) / len(errors)) if errors else None,
        "method": "userspace_sleep_loop",
        "caveat": "Not a real-time cyclictest; use only if rt-tests is unavailable.",
    }, indent=2) + "\n",
    encoding="utf-8",
)
PY
fi

log "Wrote ${OUT}"
printf '%s\n' "${OUT}"
