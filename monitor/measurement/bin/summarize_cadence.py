#!/usr/bin/env python3
"""Summarize site conf cadence keys, optionally with pidstat burst spacing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cadence import UPSTREAM_DEFAULTS, parse_monitor_conf  # noqa: E402
from lib.pidstat import parse_pidstat_text, summary_to_dict  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", required=True)
    parser.add_argument("--host", default="")
    parser.add_argument("--pidstat")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    conf_path = Path(args.conf)
    text = conf_path.read_text(encoding="utf-8") if conf_path.is_file() else ""
    payload = parse_monitor_conf(text)
    payload["host"] = args.host
    payload["conf_path"] = str(conf_path)
    payload["conf_readable"] = bool(text.strip())
    payload["upstream_defaults"] = UPSTREAM_DEFAULTS
    payload["pidstat_busy_spacing_seconds"] = None
    payload["do_not_equate_pidstat_spacing_with_sample_freq"] = True
    if args.pidstat:
        pid_path = Path(args.pidstat)
        if pid_path.is_file():
            summary = summary_to_dict(parse_pidstat_text(pid_path.read_text(encoding="utf-8")))
            payload["pidstat"] = summary
            payload["pidstat_busy_spacing_seconds"] = summary.get(
                "median_busy_spacing_seconds"
            )
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
