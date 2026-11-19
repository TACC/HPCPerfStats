#!/usr/bin/env python3
"""Campaign matrix: aggregate the per-node correctness JSON into one class x check table.

Reads the ``correctness_*.json`` files that ``rpm_debug_shm_verify.sh`` writes
(``scripts/lib/correctness_summary.py``, schema version 1) from the given files and
directories (searched recursively). Only files with ``schema_version`` 1 and ``debug_build``
true count; the others are listed as ignored. The newest file per short host name wins
(``generated_utc``; on a tie, the later path). ``scripts/lib/correctness_classes.tsv`` maps
each host to its campaign class (``--class-map HOST=CLASS`` adds or overrides a host); the
hosts of one class merge and the worst status wins (FAIL > NOT_RUN > SKIP > PASS).

Prints a matrix (rows = classes, columns = ``correctness_summary.CHECKS``; cells ``P 1.00`` /
``F 0.62`` / ``N`` / ``S``, ``!`` after a PASS whose ratio fails the re-check, ``-`` for a class
with no file, ``?<host>`` rows for hosts outside the class map) and the deviations from
``scripts/lib/correctness_expected.tsv``. A PASS ratio of a counter-accuracy check is
re-checked against the ``counter_accuracy.py`` tolerances (Grace FLOP checks: the flops-per-op
ceiling) instead of trusting the status.

Exit 0 when clean; 1 on any FAIL, unexpected NOT_RUN / SKIP, missing class, ratio outside the
tolerance or arch mismatch; 2 on unreadable or invalid input. An unexpected PASS is listed but
does not fail the run. Python 3.6 compatible.
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.correctness_summary import CHECKS, SCHEMA_VERSION  # noqa: E402
from lib.counter_accuracy import (  # noqa: E402
    GRACE_CEILING_SLACK, GRACE_FLOPS_PER_OP_CEILING, STATUSES, TOLERANCES, within)

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
CLASSES_TSV = os.path.join(LIB, "correctness_classes.tsv")
EXPECTED_TSV = os.path.join(LIB, "correctness_expected.tsv")
CHECK_NAMES = tuple(name for name, _, _ in CHECKS)
# Expected code -> accepted statuses (the first is the plain expectation).
EXPECTED_CODES = {"P": ("PASS",), "N": ("NOT_RUN",), "S": ("SKIP",), "P/N": ("PASS", "NOT_RUN")}
CELL = {"PASS": "P", "FAIL": "F", "NOT_RUN": "N", "SKIP": "S"}
RANK = {"PASS": 0, "SKIP": 1, "NOT_RUN": 2, "FAIL": 3}
# counter_accuracy.TOLERANCES family of each check whose PASS carries a ratio.
RATIO_FAMILY = {
    "mem_read": "memory", "mem_write": "memory", "mem_socket0": "socket0_share",
    "flops_dp": "flops", "flops_sp": "flops", "instructions": "instructions",
    "energy_pkg": "energy", "energy_dram": "energy",
}
REASON_MAX = 100
_UTC_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


class InputError(Exception):
    """Unreadable or invalid input (exit 2)."""


def short_host(host):
    return host.split(".")[0]


def _rows(path):
    """(line number, tab-separated fields) of the non-comment, non-blank lines of path."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError as exc:
        raise InputError("%s: %s" % (path, exc)) from exc
    for n, line in enumerate(lines, 1):
        if line.strip() and not line.startswith("#"):
            yield n, line.split("\t")


def load_classes(path, overrides=()):
    """(classes, hosts): {class: {cluster, arch}} in file order and {short host: class}."""
    classes, hosts = collections.OrderedDict(), {}
    for n, f in _rows(path):
        if len(f) != 4:
            raise InputError("%s:%d: want host, class, cluster, arch" % (path, n))
        host, cls, cluster, arch = f
        if classes.setdefault(cls, {"cluster": cluster, "arch": arch}) != {
                "cluster": cluster, "arch": arch}:
            raise InputError("%s:%d: class %s with a second cluster / arch" % (path, n, cls))
        if host != "-":
            if host in hosts:
                raise InputError("%s:%d: host %s listed twice" % (path, n, host))
            hosts[host] = cls
    for item in overrides:
        host, _, cls = item.partition("=")
        if not host or cls not in classes:
            raise InputError("--class-map %s: want HOST=CLASS, CLASS one of %s"
                             % (item, ", ".join(classes)))
        hosts[short_host(host)] = cls
    return classes, hosts


def load_expected(path, classes):
    """{class: {check: (code, note)}}; a ``*`` row applies to every class without its own."""
    default, own = {}, {}
    for n, f in _rows(path):
        if len(f) not in (3, 4):
            raise InputError("%s:%d: want class, check, expected, note" % (path, n))
        cls, check, code = f[:3]
        if cls != "*" and cls not in classes:
            raise InputError("%s:%d: unknown class %s" % (path, n, cls))
        if check not in CHECK_NAMES:
            raise InputError("%s:%d: unknown check %s" % (path, n, check))
        if code not in EXPECTED_CODES:
            raise InputError("%s:%d: expected %s is not one of %s"
                             % (path, n, code, " ".join(sorted(EXPECTED_CODES))))
        table = default if cls == "*" else own.setdefault(cls, {})
        if check in table:
            raise InputError("%s:%d: duplicate row %s %s" % (path, n, cls, check))
        table[check] = (code, f[3] if len(f) == 4 else "")
    expected = {}
    for cls in classes:
        row = dict(default, **own.get(cls, {}))
        missing = [c for c in CHECK_NAMES if c not in row]
        if missing:
            raise InputError("%s: class %s has no expectation for %s"
                             % (path, cls, ", ".join(missing)))
        expected[cls] = row
    return expected


def find_inputs(paths):
    files = set()
    for p in paths:
        if os.path.isdir(p):
            files.update(glob.glob(os.path.join(p, "**", "correctness_*.json"), recursive=True))
        elif os.path.exists(p):
            files.add(p)
        else:
            raise InputError("%s: no such file or directory" % p)
    return sorted(os.path.abspath(f) for f in files)


def _schema_problem(doc):
    """Why a schema-v1 document breaks the contract, or None."""
    if not isinstance(doc.get("host"), str) or not doc["host"]:
        return "host is not a non-empty string"
    if not isinstance(doc.get("generated_utc"), str) or not _UTC_RE.match(doc["generated_utc"]):
        return "generated_utc is not YYYY-MM-DDTHH:MM:SSZ"
    if not isinstance(doc.get("arch"), dict):
        return "arch is not an object"
    checks = doc.get("checks")
    if not isinstance(checks, list):
        return "checks is not a list"
    for c in checks:
        if not isinstance(c, dict) or c.get("status") not in STATUSES:
            return "check entry %s has no valid status" % json.dumps(c)[:80]
        ratio = c.get("ratio")
        if ratio is not None and (isinstance(ratio, bool) or not isinstance(ratio, (int, float))):
            return "check %s ratio is not a number" % c.get("name")
        if not isinstance(c.get("reason", ""), str):
            return "check %s reason is not a string" % c.get("name")
    if [c.get("name") for c in checks] != list(CHECK_NAMES):
        return "check names differ from correctness_summary.CHECKS"
    return None


def load_summary(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise InputError("%s: %s" % (path, exc)) from exc
    if not isinstance(doc, dict):
        raise InputError("%s: not a JSON object" % path)
    problem = _schema_problem(doc) if doc.get("schema_version") == SCHEMA_VERSION else None
    if problem:
        raise InputError("%s: %s" % (path, problem))
    return doc


def ratio_problem(check, ratio, arch_class):
    """Why a PASS ratio fails the counter_accuracy re-check, or None."""
    family = RATIO_FAMILY.get(check)
    if family is None:
        return None
    if ratio is None:
        return "PASS without a ratio"
    if family == "flops" and arch_class == "grace":
        ceiling = GRACE_FLOPS_PER_OP_CEILING[check[-2:]] * GRACE_CEILING_SLACK
        if 0 < ratio <= ceiling:
            return None
        return "flops per op %.4g outside (0, %g]" % (ratio, ceiling)
    if within(family, ratio):
        return None
    return "ratio %.4f outside %g-%g" % ((ratio,) + TOLERANCES[family])


def _deviation(cls, host, path, check, status, expected, blocking, what, reason=""):
    return {"class": cls, "host": host, "path": path, "check": check, "status": status,
            "expected": expected, "blocking": blocking, "what": what,
            "reason": (reason or "")[:REASON_MAX]}


def _compare(cls, host, path, check, status, flag, reason, expected):
    """Deviations of one host's check against its class expectation (None: unknown host)."""
    out = []
    code, note = expected if expected else (None, "")
    want = "%s%s" % (code, " (%s)" % note if note else "") if code else None
    if status == "FAIL":
        out.append(_deviation(cls, host, path, check, status, code, True, "FAIL", reason))
    elif code and status not in EXPECTED_CODES[code]:
        out.append(_deviation(cls, host, path, check, status, code, status != "PASS",
                              "unexpected %s, expected %s" % (status, want), reason))
    if flag:
        out.append(_deviation(cls, host, path, check, status, code, True, flag, reason))
    return out


def _hosts_of(cls, hosts):
    names = sorted(h for h, c in hosts.items() if c == cls)
    return ", ".join(names) if names else "any host (assign one with --class-map HOST=%s)" % cls


def aggregate(docs, classes, hosts, expected):
    """Matrix rows, deviations, ignored files and exit status from (path, doc) pairs."""
    ignored, newest = [], {}
    for path, doc in docs:
        if doc.get("schema_version") != SCHEMA_VERSION:
            ignored.append({"path": path, "why": "schema_version %r" % doc.get("schema_version")})
        elif doc.get("debug_build") is not True:
            ignored.append({"path": path, "why": "debug_build %r, needs true"
                            % doc.get("debug_build")})
        else:
            host = short_host(doc["host"])
            if host not in newest or (doc["generated_utc"], path) > newest[host][:2]:
                newest[host] = (doc["generated_utc"], path, doc)
    rows = collections.OrderedDict(
        (cls, {"class": cls, "cluster": info["cluster"], "hosts": [], "cells": {}})
        for cls, info in classes.items())
    deviations = []
    for host in sorted(newest):
        _, path, doc = newest[host]
        cls = hosts.get(host)
        row = rows.setdefault(cls or "?" + host, {"class": cls or "?" + host, "cluster": "?",
                                                  "hosts": [], "cells": {}})
        row["hosts"].append(host)
        arch = doc["arch"].get("class", "?")
        if cls and arch != classes[cls]["arch"]:
            deviations.append(_deviation(cls, host, path, None, None, None, True,
                                         "arch %s, class %s needs %s"
                                         % (arch, cls, classes[cls]["arch"])))
        for c in doc["checks"]:
            name, status, ratio = c["name"], c["status"], c.get("ratio")
            flag = ratio_problem(name, ratio, arch) if status == "PASS" else None
            deviations.extend(_compare(row["class"], host, path, name, status, flag,
                                       c.get("reason"), expected[cls][name] if cls else None))
            old = row["cells"].get(name)
            if old is None or (RANK[status], bool(flag)) > (RANK[old["status"]], bool(old["flag"])):
                row["cells"][name] = {"status": status, "ratio": ratio, "flag": flag,
                                      "host": host, "reason": c.get("reason", "")}
    for cls, row in rows.items():
        if cls in classes and not row["hosts"]:
            deviations.append(_deviation(cls, None, None, None, None, None, True,
                                         "missing class: no schema v%d DEBUG JSON from %s"
                                         % (SCHEMA_VERSION, _hosts_of(cls, hosts))))
        for name in CHECK_NAMES:
            cell = row["cells"].setdefault(name, {"status": None, "ratio": None, "flag": None,
                                                  "host": None, "reason": ""})
            cell["expected"] = expected[cls][name][0] if cls in classes else None
        row["cells"] = collections.OrderedDict((n, row["cells"][n]) for n in CHECK_NAMES)
    return {"checks": list(CHECK_NAMES), "rows": list(rows.values()), "deviations": deviations,
            "ignored": ignored, "exit": 1 if any(d["blocking"] for d in deviations) else 0}


def _cell_text(cell):
    if cell["status"] is None:
        return "-"
    ratio = "" if cell["ratio"] is None else " %.2f" % cell["ratio"]
    return CELL[cell["status"]] + ratio + ("!" if cell["flag"] else "")


def render_text(result):
    table = [["class", "hosts"] + result["checks"]]
    for row in result["rows"]:
        table.append([row["class"], ",".join(row["hosts"]) or "-"]
                     + [_cell_text(c) for c in row["cells"].values()])
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in table]
    devs = result["deviations"]
    blocking = sum(1 for d in devs if d["blocking"])
    lines.append("")
    lines.append("deviations: none" if not devs else
                 "deviations: %d (%d blocking)" % (len(devs), blocking))
    for d in devs:
        where = " ".join(x for x in (d["class"], d["host"], d["check"]) if x)
        lines.append("  %s %s: %s%s" % ("BLOCK" if d["blocking"] else "info ", where, d["what"],
                                        ": " + d["reason"] if d["reason"] else ""))
    for item in result["ignored"]:
        lines.append("ignored %s: %s" % (item["path"], item["why"]))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("inputs", nargs="+",
                   help="correctness_*.json files or directories (searched recursively)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--expected", default=EXPECTED_TSV,
                   help="class x check expectations (default: %(default)s)")
    p.add_argument("--class-map", action="append", default=[], metavar="HOST=CLASS",
                   help="map a host to a class (repeatable; needed for milan-cpu)")
    args = p.parse_args(argv)
    errors, docs = [], []
    try:
        classes, hosts = load_classes(CLASSES_TSV, args.class_map)
        expected = load_expected(args.expected, classes)
        files = find_inputs(args.inputs)
    except InputError as exc:
        errors.append(str(exc))
        files = []
    for path in files:
        try:
            docs.append((path, load_summary(path)))
        except InputError as exc:
            errors.append(str(exc))
    if errors:
        for e in errors:
            print("correctness_matrix: %s" % e, file=sys.stderr)
        return 2
    result = aggregate(docs, classes, hosts, expected)
    print(json.dumps(result, indent=1) if args.json else render_text(result))
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
