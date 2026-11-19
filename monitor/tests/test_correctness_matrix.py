#!/usr/bin/env python3
"""Tests for scripts/correctness_matrix.py, the campaign aggregator over the per-node
correctness JSON. Every fixture is written by scripts/lib/correctness_summary.py itself from a
fake results TSV, so the aggregator always reads the real schema."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

import correctness_matrix as cm  # noqa: E402
from lib import correctness_summary as cs  # noqa: E402

SCRIPT = MONITOR / "scripts" / "correctness_matrix.py"
CHECK_NAMES = [name for name, _, _ in cs.CHECKS]
CPUINFO = {
    "intel": "vendor_id : GenuineIntel\ncpu family : 6\nmodel : 143\n",
    "amd": "vendor_id : AuthenticAMD\ncpu family : 25\nmodel : 1\n",
    "grace": "CPU implementer : 0x41\nCPU part : 0xd4f\n",
}
MILAN_CPU_HOST = "c317-001"
UNKNOWN_HOST = "c999-999"
MAP_MILAN_CPU = ("--class-map", MILAN_CPU_HOST + "=milan-cpu")


class Campaign(object):
    """A tmp dir of correctness_*.json files, one per class, that meet the expected table."""

    def __init__(self, d):
        self.d = Path(d)
        self.out = self.d / "campaign"
        self.classes, self.hosts = cm.load_classes(cm.CLASSES_TSV)
        self.expected = cm.load_expected(cm.EXPECTED_TSV, self.classes)
        self.host_of = {"milan-cpu": MILAN_CPU_HOST}
        for host, cls in sorted(self.hosts.items()):
            self.host_of.setdefault(cls, host)

    def clean(self, cls):
        """{check: (status, ratio, reason)} that meets the expected table of cls."""
        got = {}
        for check, (code, note) in self.expected[cls].items():
            status = cm.EXPECTED_CODES[code][0]
            ratio = 1.0 if status == "PASS" and check in cm.RATIO_FAMILY else None
            got[check] = (status, ratio, note or "ok")
        return got

    def write(self, host, arch, checks, utc=None, debug=True, name=None):
        work = self.d / "work" / (name or host)
        work.mkdir(parents=True, exist_ok=True)
        results = work / "results.tsv"
        results.write_text("".join(
            "%s\t%s\t%s\t%s\n" % (n, s, "-" if r is None else repr(r), why)
            for n, (s, r, why) in sorted(checks.items())))
        caps = work / "caps.json"
        caps.write_text(json.dumps({"capability_slug": "slug-" + arch,
                                    "configure": {"debug": debug}}))
        cpuinfo = work / "cpuinfo"
        cpuinfo.write_text(CPUINFO[arch])
        pmu = work / "pmu"
        pmu.write_text("fake\n")
        out = self.out / ("correctness_%s.json" % (name or host))
        with contextlib.redirect_stdout(io.StringIO()):
            cs.main(["write", "--out", str(out), "--results", str(results), "--caps", str(caps),
                     "--host", host, "--cpuinfo", str(cpuinfo), "--pmu-name", str(pmu),
                     "--rpm-build", "hpcperfstatsd-debug"])
        if utc is not None:
            doc = json.loads(out.read_text())
            doc["generated_utc"] = utc
            out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return out

    def write_class(self, cls, changes=None, **kw):
        checks = self.clean(cls)
        checks.update(changes or {})
        return self.write(self.host_of[cls], self.classes[cls]["arch"], checks, **kw)

    def write_all(self, skip=()):
        for cls in self.classes:
            if cls not in skip:
                self.write_class(cls)


def run(*argv):
    """(exit code, stdout) of correctness_matrix.main."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = cm.main([str(a) for a in argv])
    return rc, buf.getvalue()


def run_json(*argv):
    rc, out = run("--json", *argv)
    return rc, json.loads(out)


def row(doc, cls):
    return next(r for r in doc["rows"] if r["class"] == cls)


def deviations(doc, cls=None, check=None):
    return [d for d in doc["deviations"]
            if (cls is None or d["class"] == cls) and (check is None or d["check"] == check)]


class Matrix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Campaign(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_campaign_exits_0(self):
        self.c.write_all()
        rc, out = run(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 0, out)
        self.assertIn("deviations: none", out)
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual((rc, doc["exit"], doc["deviations"]), (0, 0, []))
        self.assertEqual(row(doc, "milan-cpu")["hosts"], [MILAN_CPU_HOST])

    def test_matrix_worst_status_wins(self):
        a = self.c.clean("skx")
        b = self.c.clean("skx")
        pairs = {"mem_read": ("PASS", "SKIP", "SKIP"),
                 "mem_write": ("SKIP", "NOT_RUN", "NOT_RUN"),
                 "flops_dp": ("NOT_RUN", "FAIL", "FAIL"),
                 "flops_sp": ("FAIL", "PASS", "FAIL"),
                 "perf_raw_flops": ("PASS", "NOT_RUN", "NOT_RUN")}
        for check, (sa, sb, _) in pairs.items():
            a[check] = (sa, 1.0 if sa == "PASS" else None, "a")
            b[check] = (sb, 1.0 if sb == "PASS" else None, "b")
        self.c.write("c492-081", "intel", a)
        self.c.write("c492-082", "intel", b)
        rc, doc = run_json(self.c.out, "--class-map", "c492-082=skx")
        self.assertEqual(rc, 1)
        cells = row(doc, "skx")["cells"]
        self.assertEqual(row(doc, "skx")["hosts"], ["c492-081", "c492-082"])
        for check, (_, _, worst) in pairs.items():
            self.assertEqual(cells[check]["status"], worst, check)
        self.assertEqual(cells["preflight"]["status"], "PASS")
        rc, out = run(self.c.out, "--class-map", "c492-082=skx")
        line = next(ln for ln in out.splitlines() if ln.startswith("skx "))
        self.assertIn(" S ", line)
        self.assertIn(" F ", line)

    def test_matrix_unexpected_not_run_exits_1(self):
        self.c.write_all(skip=("skx",))
        self.c.write_class("skx", {"mem_write": ("NOT_RUN", None, "no write kernel")})
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        [dev] = doc["deviations"]
        self.assertEqual((dev["class"], dev["host"], dev["check"], dev["status"], dev["expected"]),
                         ("skx", "c492-081", "mem_write", "NOT_RUN", "P"))
        self.assertTrue(dev["blocking"])
        rc, out = run(self.c.out, *MAP_MILAN_CPU)
        self.assertIn("unexpected NOT_RUN", out)
        self.assertIn("no write kernel", out)

    def test_unexpected_skip_exits_1_and_unexpected_pass_is_informational(self):
        self.c.write_all(skip=("gg", "skx"))
        self.c.write_class("gg", {"fault_inject": ("SKIP", None, "FAULT_INJECT=0")})
        self.c.write_class("skx", {"golden": ("PASS", None, "golden matched")})
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        [skip] = deviations(doc, "gg")
        self.assertEqual((skip["check"], skip["status"], skip["blocking"]),
                         ("fault_inject", "SKIP", True))
        [extra] = deviations(doc, "skx")
        self.assertEqual((extra["check"], extra["status"], extra["blocking"]),
                         ("golden", "PASS", False))

    def test_p_or_n_accepts_both(self):
        self.c.write_all(skip=("gh",))
        self.c.write_class("gh", {"mem_read": ("NOT_RUN", None, "SCF unbound"),
                                  "scf_perf": ("NOT_RUN", None, "no SCF PMU"),
                                  "mem_write": ("PASS", 1.1, "ok")})
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual((rc, doc["deviations"]), (0, []))
        self.c.write_class("gh", {"mem_read": ("SKIP", None, "COUNTER_CHECK=0")})
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        self.assertEqual([d["check"] for d in doc["deviations"]], ["mem_read"])

    def test_matrix_ratio_outside_tolerance(self):
        lo, hi = cm.TOLERANCES["memory"]
        self.c.write_all(skip=("skx", "gg"))
        self.c.write_class("skx", {"mem_read": ("PASS", hi + 0.12, "trusting status"),
                                   "mem_write": ("PASS", lo, "edge"),
                                   "flops_dp": ("PASS", None, "no ratio")})
        dp_ceiling = cm.GRACE_FLOPS_PER_OP_CEILING["dp"]
        self.c.write_class("gg", {"flops_dp": ("PASS", dp_ceiling * 0.98, "under ceiling"),
                                  "flops_sp": ("PASS", 0.0, "zero flops per op")})
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        flagged = sorted((d["class"], d["check"]) for d in doc["deviations"])
        self.assertEqual(flagged, [("gg", "flops_sp"), ("skx", "flops_dp"), ("skx", "mem_read")])
        self.assertTrue(all(d["blocking"] for d in doc["deviations"]))
        self.assertIsNone(row(doc, "skx")["cells"]["mem_write"]["flag"])
        self.assertIsNone(row(doc, "gg")["cells"]["flops_dp"]["flag"])
        rc, out = run(self.c.out, *MAP_MILAN_CPU)
        self.assertIn("P %.2f!" % (hi + 0.12), out)
        self.assertIn("PASS without a ratio", out)

    def test_matrix_unknown_host_and_bad_json(self):
        self.c.write_all()
        self.c.write(UNKNOWN_HOST, "intel", self.c.clean("skx"))
        rc, out = run(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 0, out)
        self.assertIn("?" + UNKNOWN_HOST, out)
        self.c.write(UNKNOWN_HOST, "intel", dict(self.c.clean("skx"),
                                                 preflight=("FAIL", None, "service down")))
        rc, out = run(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1, out)
        (self.c.out / "correctness_broken.json").write_text("{not json")
        rc, _ = run(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 2)
        (self.c.out / "correctness_broken.json").write_text(json.dumps(
            {"schema_version": 1, "host": "x", "generated_utc": "2026-11-18T00:00:00Z",
             "debug_build": True, "checks": [{"name": "preflight", "status": "MAYBE"}]}))
        rc, _ = run(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 2)
        doc = json.loads(self.c.write_class("skx").read_text())
        doc["arch"] = "intel"
        (self.c.out / "correctness_broken.json").write_text(json.dumps(doc))
        rc, _ = run(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 2)
        rc, _ = run(self.c.out / "no_such_dir")
        self.assertEqual(rc, 2)

    def test_matrix_columns_follow_checks(self):
        self.c.write_all()
        _, out = run(self.c.out, *MAP_MILAN_CPU)
        header = out.splitlines()[0].split()
        self.assertEqual(header, ["class", "hosts"] + CHECK_NAMES)
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(doc["checks"], CHECK_NAMES)
        self.assertEqual(list(row(doc, "skx")["cells"]), CHECK_NAMES)
        self.assertEqual(list(cm.CHECK_NAMES), CHECK_NAMES)

    def test_missing_class_exits_1(self):
        self.c.write_all(skip=("genoa",))
        self.c.write_class("genoa", debug=False)
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        [dev] = doc["deviations"]
        self.assertEqual((dev["class"], dev["check"], dev["blocking"]), ("genoa", None, True))
        self.assertEqual(len(doc["ignored"]), 1)
        self.assertIn("debug_build", doc["ignored"][0]["why"])
        self.assertEqual({c["status"] for c in row(doc, "genoa")["cells"].values()}, {None})
        rc, doc = run_json(self.c.out)
        self.assertEqual(sorted(d["class"] for d in doc["deviations"]), ["genoa", "milan-cpu"])
        rc, out = run(self.c.out)
        self.assertIn("missing class", out)

    def test_newest_file_per_host(self):
        self.c.write_all(skip=("icx",))
        self.c.write_class("icx", {"preflight": ("FAIL", None, "old run")}, name="icx-old",
                           utc="2026-11-01T00:00:00Z")
        self.c.write_class("icx", name="icx-new", utc="2026-11-02T00:00:00Z")
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual((rc, doc["deviations"]), (0, []))
        self.c.write_class("icx", {"preflight": ("FAIL", None, "tie, later path")},
                           name="icx-z", utc="2026-11-02T00:00:00Z")
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        [dev] = doc["deviations"]
        self.assertEqual((dev["class"], dev["check"], dev["reason"]),
                         ("icx", "preflight", "tie, later path"))
        self.assertTrue(dev["path"].endswith("correctness_icx-z.json"))

    def test_fqdn_host_and_arch_mismatch(self):
        self.c.write_all(skip=("skx", "genoa"))
        self.c.write("c492-081.stampede3.tacc.utexas.edu", "intel", self.c.clean("skx"))
        self.c.write("c318-005", "intel", self.c.clean("genoa"))
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        self.assertEqual(row(doc, "skx")["hosts"], ["c492-081"])
        [dev] = doc["deviations"]
        self.assertEqual((dev["class"], dev["check"], dev["blocking"]), ("genoa", None, True))
        self.assertIn("arch intel", dev["what"])

    def test_reason_cut_to_100(self):
        self.c.write_all(skip=("spr",))
        self.c.write_class("spr", {"sentinels": ("FAIL", None, "x" * 300)})
        rc, doc = run_json(self.c.out, *MAP_MILAN_CPU)
        self.assertEqual(rc, 1)
        self.assertEqual(len(deviations(doc, "spr", "sentinels")[0]["reason"]), cm.REASON_MAX)
        self.assertEqual(cm.REASON_MAX, 100)

    def test_class_map_rejects_unknown_class(self):
        self.c.write_all()
        rc, _ = run(self.c.out, "--class-map", "c1=nope")
        self.assertEqual(rc, 2)

    def test_expected_table_is_complete_and_strict(self):
        for cls in self.c.classes:
            self.assertEqual(sorted(self.c.expected[cls]), sorted(CHECK_NAMES), cls)
        self.assertEqual(self.c.expected["skx"]["golden"][0], "S")
        self.assertEqual(self.c.expected["gh"]["mem_read"][0], "P/N")
        bad = self.c.d / "bad.tsv"
        good = Path(cm.EXPECTED_TSV).read_text()
        for extra in ("*\tnot_a_check\tP\tx\n", "skx\tmem_read\tQ\tx\n", "nope\tmem_read\tP\t\n",
                      "skx\tmem_read\tP\tduplicate\n"):
            bad.write_text(good + extra)
            with self.assertRaises(cm.InputError, msg=extra):
                cm.load_expected(str(bad), self.c.classes)
        bad.write_text("\n".join(ln for ln in good.splitlines()
                                 if not ln.startswith("*\tpreflight")) + "\n")
        with self.assertRaises(cm.InputError):
            cm.load_expected(str(bad), self.c.classes)
        self.c.write_all()
        rc, _ = run(self.c.out, "--expected", bad)
        self.assertEqual(rc, 2)

    def test_cli_exit_status(self):
        self.c.write_all()
        cmd = [sys.executable, str(SCRIPT), str(self.c.out)] + list(MAP_MILAN_CPU)
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout)
        p = subprocess.run(cmd[:3], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 1, p.stdout)


if __name__ == "__main__":
    unittest.main()
