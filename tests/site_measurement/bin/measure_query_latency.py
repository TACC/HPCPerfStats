#!/usr/bin/env python3
"""Time Extended Search and Job Detail API GETs under named scenarios."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.query_latency import (  # noqa: E402
    default_placeholders,
    load_scenarios,
    run_scenarios,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HPCPERFSTATS_API_URL", ""),
        help="API root ending in /api/ (or set HPCPERFSTATS_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("HPCPERFSTATS_API_KEY", ""),
        help="Staff/user API key (or HPCPERFSTATS_API_KEY). Never logged.",
    )
    parser.add_argument(
        "--scenarios",
        default=str(ROOT / "scenarios" / "query_latency.json"),
    )
    parser.add_argument("--jid", default=os.environ.get("HPCPERFSTATS_MEASURE_JID", ""))
    parser.add_argument(
        "--username", default=os.environ.get("HPCPERFSTATS_MEASURE_USERNAME", "")
    )
    parser.add_argument(
        "--queue", default=os.environ.get("HPCPERFSTATS_MEASURE_QUEUE", "")
    )
    parser.add_argument(
        "--host", default=os.environ.get("HPCPERFSTATS_MEASURE_HOST", "")
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not args.base_url:
        print("ERROR: pass --base-url or HPCPERFSTATS_API_URL", file=sys.stderr)
        return 2
    placeholders = default_placeholders(
        jid=args.jid,
        username=args.username,
        queue=args.queue,
        host=args.host,
        today=date.today(),
    )
    scenarios = load_scenarios(args.scenarios, placeholders)
    if args.dry_run:
        from lib.query_latency import build_url

        for scenario in scenarios:
            name = str(scenario.get("name") or "unnamed")
            path = str(scenario.get("path") or "")
            query = scenario.get("query") or {}
            skip_if_empty = scenario.get("skip_if_empty") or []
            if "/jobs//" in path:
                print(f"{name}\tSKIP\tmissing job id")
                continue
            if any(not query.get(key) for key in skip_if_empty):
                print(f"{name}\tSKIP\tmissing {skip_if_empty}")
                continue
            url = build_url(args.base_url, path, query)
            print(f"{name}\t{url}")
        return 0
    report = run_scenarios(
        scenarios,
        base_url=args.base_url,
        api_key=args.api_key or None,
        repeats=args.repeats,
        warmup=args.warmup,
        timeout_s=args.timeout,
        verify_tls=not args.insecure,
    )
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(str(out))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
