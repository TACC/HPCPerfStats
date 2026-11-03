#!/usr/bin/env python3
"""Compare cyclictest or sleep-loop jitter with daemon on vs off."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_MAX_RE = re.compile(r"Max:\s+(\d+)", re.IGNORECASE)
# cyclictest -h prints "# Max Latencies: ..." (with the comment marker) after the histogram.
_CYCLIC_MAX = re.compile(r"^\s*#?\s*Max Latencies:\s*(.+)$", re.IGNORECASE)
_HIST_ROW = re.compile(r"^(\d+)((?:\s+\d+)+)\s*$")
PERCENTILES = (50, 99, 99.9)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_max_us(capture_dir: Path) -> tuple[float | None, str]:
    """Return (max_us, source) from a jitter capture directory."""
    sleep = _load_json(capture_dir / "sleep_loop.txt")
    if sleep and sleep.get("max_us") is not None:
        return float(sleep["max_us"]), "userspace_sleep_loop"
    cyclic = capture_dir / "cyclictest.txt"
    if cyclic.is_file():
        text = cyclic.read_text(encoding="utf-8", errors="replace")
        for raw in text.splitlines():
            match = _CYCLIC_MAX.match(raw)
            if match:
                nums = [int(x) for x in re.findall(r"\d+", match.group(1))]
                if nums:
                    return float(max(nums)), "cyclictest_max_latencies"
            match = _MAX_RE.search(raw)
            if match:
                return float(match.group(1)), "cyclictest_max"
    return None, "missing"


def _pct_key(p: float) -> str:
    return "p" + (f"{p:g}".replace(".", ""))


def extract_percentiles_us(capture_dir: Path) -> dict[str, float] | None:
    """p50/p99/p99.9 wakeup latency from the cyclictest -h histogram or the sleep loop."""
    sleep = _load_json(capture_dir / "sleep_loop.txt")
    if sleep and sleep.get("p99_us") is not None:
        return {"p99": float(sleep["p99_us"])}
    cyclic = capture_dir / "cyclictest.txt"
    if not cyclic.is_file():
        return None
    hist: list[tuple[int, int]] = []
    for raw in cyclic.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _HIST_ROW.match(raw.strip())
        if m:
            hist.append((int(m.group(1)), sum(int(x) for x in m.group(2).split())))
    total = sum(c for _, c in hist)
    if not total:
        return None
    out: dict[str, float] = {}
    for p in PERCENTILES:
        need = total * p / 100.0
        acc = 0
        for bucket, count in hist:
            acc += count
            if acc >= need:
                out[_pct_key(p)] = bucket
                break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--on", required=True, help="capture dir with daemon running")
    parser.add_argument("--off", required=True, help="capture dir with daemon stopped")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    on_us, on_src = extract_max_us(Path(args.on))
    off_us, off_src = extract_max_us(Path(args.off))
    delta = None
    if on_us is not None and off_us is not None:
        delta = round(on_us - off_us, 3)
    on_pct = extract_percentiles_us(Path(args.on))
    off_pct = extract_percentiles_us(Path(args.off))
    p99_delta = None
    if on_pct and off_pct and "p99" in on_pct and "p99" in off_pct:
        p99_delta = on_pct["p99"] - off_pct["p99"]
    payload = {
        "daemon_on_percentiles_us": on_pct,
        "daemon_off_percentiles_us": off_pct,
        "p99_delta_us": p99_delta,
        "daemon_on_max_us": on_us,
        "daemon_off_max_us": off_us,
        "max_delta_us": delta,
        "on_source": on_src,
        "off_source": off_src,
        "caveat": (
            "Positive delta means higher wakeup latency with the daemon on. A max delta is one "
            "outlier per leg; prefer p99_delta_us. FWQ (run_overhead_profile.sh) is the primary "
            "noise metric. This is not STREAM/IOR interference."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
