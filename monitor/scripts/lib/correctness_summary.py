#!/usr/bin/env python3
"""Per-node correctness summary JSON for the cross-architecture campaign.

``rpm_debug_shm_verify.sh`` calls ``write`` once per run; the file lands next to the text reports
(``test_runs/monitor/correctness_<host>_<slug>_<UTC>.json``). Schema version 1 (documented in
monitor/docs/how-to/run-correctness-campaign.md, locked by tests/test_correctness_summary.py):

  schema_version   int, bumped only on a breaking change
  generated_utc    ISO-8601 UTC, second resolution
  host             node hostname
  arch             {class: intel|amd|grace|other, vendor, family, model, pmu_name}
  capability_slug  slug of the installed build ("unknown" before capabilities resolved)
  rpm_build        `rpm -q hpcperfstatsd` output
  debug_build      configure.debug from the capabilities JSON (bool or null)
  overall          FAIL when any check FAILs, else PASS
  counts           {PASS, FAIL, NOT_RUN, SKIP: int}
  checks           one entry per name in CHECKS, in that order:
                   {name, layer, status: PASS|FAIL|NOT_RUN|SKIP, reason, ratio: float|null}

Inputs: the results TSV (name, status, ratio, reason; last line per name wins) written by the
verify script and ``counter_accuracy.py``, and the validate_shm_messages report, whose
``PASS|FAIL|SKIP <layer>`` lines map to the validate checks (FAIL wins over PASS over SKIP).
A check with no result is NOT_RUN ("not reached"), or SKIP when ``--skip NAME=REASON`` names it
or its layer. Python 3.6 compatible.
"""

import argparse
import json
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.counter_accuracy import COUNTER_CHECKS, STATUSES, arch_info, load_results  # noqa: E402

SCHEMA_VERSION = 1

# (name, layer, what a PASS proves)
CHECKS = (
    ("preflight", "preflight", "RPM installed, service active, first full payload, slug resolved"),
    ("message_structure", "validate", "schema / fast / full rows parse against the manifest"),
    ("sentinels", "validate", "sentinel banner, allowed codes, NOT_PRESENT probes, no stuck -1"),
    ("plausibility", "validate", "values inside plausible ranges"),
    ("cross_sample", "validate", "cadence, E-counter monotonicity, bridge validity"),
    ("live_spot", "validate", "values match live /proc and /sys reads"),
    ("independent_read", "validate", "a live source read lies between two emitted values"),
    ("consumer_parse", "validate", "the consumer parser reads the capture (sentinels, deltas)"),
    ("golden", "validate", "byte diff against a slug golden (opt-in)"),
) + tuple((n, "counter_accuracy", "daemon counter vs known load") for n in COUNTER_CHECKS) + (
    ("perf_raw_flops", "canary", "x86 perf FLOP events vs exact GEMM FLOPs (no daemon)"),
    ("scf_perf", "canary", "Grace SCF dram_cas_* vs perf nvidia_scf_pmu within 5%"),
    ("pmu_sharing", "canary", "PMU sharing keys: running share <= 100% on a quiet node"),
    ("fp_slices", "canary", "Grace precision slices, monitor vs perf, flops-per-op ceiling"),
    ("fault_inject", "fault_inject", "forced failures map to codes, recover, every site fires"),
)
_LAYERS = {layer for _, layer, _ in CHECKS}
_NAMES = [name for name, _, _ in CHECKS]
_LAYER_OF = {name: layer for name, layer, _ in CHECKS}

# First word of a validate_shm_messages report line -> check name.
VALIDATE_PREFIX = {
    "schema": "message_structure", "fast": "message_structure", "full": "message_structure",
    "wait_shm": "message_structure", "profile": "message_structure",
    "system": "message_structure", "papi": "message_structure",
    "sentinel": "sentinels", "plausibility": "plausibility", "cross_sample": "cross_sample",
    "live_spot": "live_spot", "independent_read": "independent_read",
    "consumer_parse": "consumer_parse", "golden": "golden",
}
_REPORT_RE = re.compile(r"^(PASS|FAIL|SKIP) ([a-z_]+)")
_RANK = {"FAIL": 3, "PASS": 2, "SKIP": 1}


def _check(name, status, reason="", ratio=None):
    return {"name": name, "layer": _LAYER_OF[name], "status": status, "reason": reason,
            "ratio": ratio}


def parse_validate_report(text):
    """{check: (status, reason)} from validate_shm_messages report lines."""
    got = {}
    for line in text.splitlines():
        m = _REPORT_RE.match(line.strip())
        if not m or m.group(2) not in VALIDATE_PREFIX:
            continue
        name, status = VALIDATE_PREFIX[m.group(2)], m.group(1)
        old = got.get(name)
        if old is None or _RANK[status] > _RANK[old[0]]:
            got[name] = (status, line.strip()[:300])
    return got


def _caps(path):
    try:
        with open(path) as f:
            caps = json.load(f)
    except (OSError, ValueError, TypeError):
        return "unknown", None
    debug = caps.get("configure", {}).get("debug")
    return caps.get("capability_slug", "unknown"), None if debug is None else bool(debug)


def build_summary(results, validate_report, validate_rc, caps, rpm_build, host, cpuinfo,
                  pmu_name, skips):
    skip_map = {}
    for item in skips:
        key, _, reason = item.partition("=")
        if key not in _LAYERS and key not in _LAYER_OF:
            raise ValueError("--skip %s: not a check or layer name" % key)
        skip_map[key] = reason
    got = {}
    for name, r in load_results(results).items():
        if name in _LAYER_OF:
            got[name] = _check(name, r["status"], r["reason"], r["ratio"])
    report_text = None
    if validate_report and os.path.isfile(validate_report):
        with open(validate_report) as f:
            report_text = f.read()
        for name, (status, reason) in parse_validate_report(report_text).items():
            got[name] = _check(name, status, reason)
    if validate_rc not in (None, 0) and not any(
            c["status"] == "FAIL" for c in got.values() if c["layer"] == "validate"):
        got["message_structure"] = _check(
            "message_structure", "FAIL",
            "validate_shm_messages exit %s%s" % (validate_rc, "" if report_text else
                                                 " (no report; see its stderr)"))
    if "preflight" not in got:
        got["preflight"] = _check("preflight", "FAIL", "verify stopped before validation "
                                  "(install, service, full payload or capabilities; see log)")
    checks = []
    for name in _NAMES:
        if name in got:
            checks.append(got[name])
            continue
        reason = skip_map.get(name, skip_map.get(_LAYER_OF[name]))
        checks.append(_check(name, "SKIP", reason) if reason is not None else
                      _check(name, "NOT_RUN", "not reached (an earlier step failed or the layer "
                             "produced no result)"))
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in STATUSES}
    slug, debug = _caps(caps)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": host,
        "arch": arch_info(cpuinfo, pmu_name),
        "capability_slug": slug,
        "rpm_build": rpm_build,
        "debug_build": debug,
        "overall": "FAIL" if counts["FAIL"] else "PASS",
        "counts": counts,
        "checks": checks,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    w = sub.add_parser("write")
    w.add_argument("--out", required=True)
    w.add_argument("--results", required=True)
    w.add_argument("--validate-report")
    w.add_argument("--validate-rc", type=int)
    w.add_argument("--caps")
    w.add_argument("--rpm-build", default="unknown")
    w.add_argument("--host", default=socket.gethostname())
    w.add_argument("--cpuinfo", default="/proc/cpuinfo")
    w.add_argument("--pmu-name", default="/sys/bus/event_source/devices/cpu/caps/pmu_name")
    w.add_argument("--skip", action="append", default=[])
    args = p.parse_args(argv)
    if args.cmd != "write":
        p.print_help()
        return 2
    try:
        summary = build_summary(args.results, args.validate_report, args.validate_rc, args.caps,
                                args.rpm_build, args.host, args.cpuinfo, args.pmu_name, args.skip)
    except ValueError as exc:
        p.error(str(exc))
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(args.out, "w") as f:
        json.dump(summary, f, indent=1, sort_keys=True)
        f.write("\n")
    c = summary["counts"]
    print("correctness summary: %s overall=%s (%d PASS, %d FAIL, %d NOT_RUN, %d SKIP)" % (
        args.out, summary["overall"], c["PASS"], c["FAIL"], c["NOT_RUN"], c["SKIP"]))
    for check in summary["checks"]:
        if check["status"] == "FAIL":
            print("  FAIL %s: %s" % (check["name"], check["reason"]))
    return 1 if summary["overall"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
