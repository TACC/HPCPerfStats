"""Command line: ``python -m hpcperfstats_raw {summary,schema,series,csv,check} FILE ...``."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from typing import List, Optional

from .counters import iter_series
from .frame import COLUMNS, iter_long_rows
from .reader import Problem, iter_samples


def _summary(paths: List[str]) -> int:
    for path in paths:
        problems: List[Problem] = []
        n = 0
        first = last = None
        jobs: Counter = Counter()
        tiers: Counter = Counter()
        types: Counter = Counter()
        sentinels: Counter = Counter()
        header = None
        for s in iter_samples(path, problems=problems):
            n += 1
            first = s.time if first is None else first
            last = s.time
            jobs[s.jobid] += 1
            tiers[s.tier or "legacy"] += 1
            header = s.header or header
            for r in s.rows:
                types[r.type] += 1
                for v in r.sentinels().values():
                    sentinels[v.slug] += 1
        print("== %s" % path)
        if header is not None:
            print("daemon   %s %s  build %s" % (header.program, header.version, header.build))
            print("host     %s  (%s %s)" % (header.hostname, header.machine, header.release))
        print("samples  %d  from %s to %s" % (n, first, last))
        print("jobs     %s" % ", ".join("%s(%d)" % kv for kv in jobs.most_common()))
        print("tiers    %s" % ", ".join("%s(%d)" % kv for kv in tiers.most_common()))
        print("rows     %s" % ", ".join("%s(%d)" % kv for kv in sorted(types.items())))
        print("sentinel %s" % (", ".join("%s(%d)" % kv for kv in sentinels.most_common()) or "none"))
        print("problems %d" % len(problems))
        for p in problems[:10]:
            print("  line %d: %s" % (p.line_no, p.message))
    return 0


def _schema(path: str) -> int:
    for s in iter_samples(path):
        if s.header is None:
            continue
        for ts in s.header.schemas.values():
            print(ts.type)
            for e in ts.entries:
                kind = "counter" if e.is_event else "gauge"
                unit = ("%sx %s" % (e.multiplier, e.unit) if e.multiplier != 1 else e.unit) or "-"
                print("  %-40s %-8s unit=%-10s W=%-3d%s" % (e.key, kind, unit, e.width,
                                                           "  slow" if e.slow else ""))
        return 0
    print("no header in %s" % path, file=sys.stderr)
    return 1


def _series(path: str, type_name: str, dev: str, key: str) -> int:
    w = csv.writer(sys.stdout)
    w.writerow(["time", "jobid", "value", "delta", "rate_per_s"])
    for p in iter_series(iter_samples(path), type_name, dev, key):
        w.writerow([p.time, p.jobid, p.value, p.delta, p.rate])
    return 0


def _csv(paths: List[str], types: Optional[List[str]]) -> int:
    w = csv.writer(sys.stdout)
    w.writerow(COLUMNS)
    for path in paths:
        for row in iter_long_rows(iter_samples(path), types):
            w.writerow(row)
    return 0


def _check(paths: List[str]) -> int:
    bad = 0
    for path in paths:
        problems: List[Problem] = []
        for _ in iter_samples(path, problems=problems):
            pass
        for p in problems:
            print("%s:%d: %s" % (path, p.line_no, p.message))
        bad += len(problems)
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hpcperfstats_raw", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("summary", help="what is in each file")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("schema", help="types and keys of the first header")
    p.add_argument("file")
    p = sub.add_parser("series", help="one key as CSV with deltas and rates")
    p.add_argument("file")
    p.add_argument("type")
    p.add_argument("dev")
    p.add_argument("key")
    p = sub.add_parser("csv", help="long-form CSV of every value")
    p.add_argument("files", nargs="+")
    p.add_argument("--type", action="append", dest="types")
    p = sub.add_parser("check", help="report malformed lines; exit 1 if any")
    p.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        return _summary(a.files)
    if a.cmd == "schema":
        return _schema(a.file)
    if a.cmd == "series":
        return _series(a.file, a.type, a.dev, a.key)
    if a.cmd == "csv":
        return _csv(a.files, a.types)
    return _check(a.files)


if __name__ == "__main__":
    sys.exit(main())
