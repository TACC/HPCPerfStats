#!/usr/bin/env python3
"""Schema test for scripts/lib/correctness_summary.py: the per-node campaign JSON that
rpm_debug_shm_verify.sh writes next to its text reports. The campaign aggregates these files
across architectures, so the field set, check names and status vocabulary are a contract."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib import correctness_summary as cs  # noqa: E402
from lib import counter_accuracy as ca  # noqa: E402

LIB = MONITOR / "scripts" / "lib" / "correctness_summary.py"
TOP_KEYS = {"schema_version", "generated_utc", "host", "arch", "capability_slug", "rpm_build",
            "debug_build", "overall", "counts", "checks"}
ARCH_KEYS = {"class", "vendor", "family", "model", "pmu_name"}
CHECK_KEYS = {"name", "layer", "status", "reason", "ratio"}

REPORT = """PASS schema
PASS fast
PASS full
PASS sentinel banner
NOTE sentinel NOT_PRESENT x3
PASS plausibility
WARN cross_sample cadence
PASS cross_sample monotonic
PASS live_spot
FAIL independent_read: host_cpu 3 user 10 outside [11, 12]
SKIP consumer_parse: no repo .venv (/x)
"""


class Fixture(object):
    def __init__(self, d):
        self.d = Path(d)
        self.caps = self.d / "caps.json"
        self.caps.write_text(json.dumps({"capability_slug": "x86_64-ver3.1-x86perf-sent1",
                                         "configure": {"debug": True}}))
        self.cpuinfo = self.d / "cpuinfo"
        self.cpuinfo.write_text("vendor_id : GenuineIntel\ncpu family : 6\nmodel : 143\n")
        self.pmu = self.d / "pmu"
        self.pmu.write_text("sapphire_rapids\n")
        self.report = self.d / "validate.txt"
        self.report.write_text(REPORT)
        self.results = self.d / "results.tsv"
        self.results.write_text("preflight\tPASS\t-\tservice active\n"
                                "flops_dp\tPASS\t1.0100\tok\n"
                                "fault_inject\tSKIP\t-\tSKIP: fault injection (FAULT_INJECT=0)\n")

    def build(self, **kw):
        args = dict(results=str(self.results), validate_report=str(self.report), validate_rc=1,
                    caps=str(self.caps), rpm_build="hpcperfstatsd-3.1-1.el9.x86_64",
                    host="c001-001", cpuinfo=str(self.cpuinfo), pmu_name=str(self.pmu),
                    skips=[])
        args.update(kw)
        return cs.build_summary(**args)


class Schema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_top_level_fields_and_types(self):
        s = self.fx.build()
        self.assertEqual(set(s), TOP_KEYS)
        self.assertEqual(s["schema_version"], cs.SCHEMA_VERSION)
        self.assertEqual(s["schema_version"], 1)
        self.assertEqual(set(s["arch"]), ARCH_KEYS)
        self.assertEqual(s["arch"]["class"], "intel")
        self.assertEqual(s["capability_slug"], "x86_64-ver3.1-x86perf-sent1")
        self.assertIs(s["debug_build"], True)
        self.assertEqual(s["host"], "c001-001")
        self.assertRegex(s["generated_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(set(s["counts"]), set(cs.STATUSES))
        self.assertEqual(sum(s["counts"].values()), len(cs.CHECKS))

    def test_every_check_exactly_once_in_stable_order(self):
        s = self.fx.build()
        self.assertEqual([c["name"] for c in s["checks"]], [c[0] for c in cs.CHECKS])
        for c in s["checks"]:
            self.assertEqual(set(c), CHECK_KEYS)
            self.assertIn(c["status"], cs.STATUSES)
            self.assertIsInstance(c["reason"], str)
            self.assertTrue(c["ratio"] is None or isinstance(c["ratio"], float))

    def test_counter_checks_are_listed(self):
        names = [c[0] for c in cs.CHECKS if c[1] == "counter_accuracy"]
        self.assertEqual(names, list(ca.COUNTER_CHECKS))

    def test_validate_report_mapping(self):
        got = {c["name"]: c for c in self.fx.build()["checks"]}
        self.assertEqual(got["message_structure"]["status"], "PASS")
        self.assertEqual(got["sentinels"]["status"], "PASS")
        self.assertEqual(got["cross_sample"]["status"], "PASS")
        self.assertEqual(got["independent_read"]["status"], "FAIL")
        self.assertIn("outside [11, 12]", got["independent_read"]["reason"])
        self.assertEqual(got["consumer_parse"]["status"], "SKIP")
        self.assertEqual(got["golden"]["status"], "NOT_RUN")
        self.assertEqual(got["flops_dp"]["ratio"], 1.01)
        self.assertEqual(got["fault_inject"]["status"], "SKIP")

    def test_fault_inject_not_run_keeps_its_reason_and_does_not_fail(self):
        # rpm_debug_shm_verify.sh records NOT_RUN when no planned fault point was reached.
        reason = "fault_inject: NOT_RUN (no planned fault point was reached on this node)"
        self.fx.results.write_text("preflight\tPASS\t-\tok\nfault_inject\tNOT_RUN\t-\t%s\n" % reason)
        self.fx.report.write_text(REPORT.replace("FAIL independent_read", "PASS independent_read"))
        s = self.fx.build(validate_rc=0)
        got = {c["name"]: c for c in s["checks"]}
        self.assertEqual((got["fault_inject"]["status"], got["fault_inject"]["reason"]),
                         ("NOT_RUN", reason))
        self.assertEqual(s["overall"], "PASS")

    def test_overall_fail_on_any_fail_else_pass(self):
        self.assertEqual(self.fx.build()["overall"], "FAIL")
        self.fx.report.write_text(REPORT.replace("FAIL independent_read", "PASS independent_read"))
        s = self.fx.build(validate_rc=0)
        self.assertEqual(s["overall"], "PASS")
        self.assertGreater(s["counts"]["NOT_RUN"], 0)

    def test_validate_exit_without_report_fails_message_structure(self):
        s = self.fx.build(validate_report=str(self.fx.d / "missing.txt"), validate_rc=1)
        got = {c["name"]: c for c in s["checks"]}
        self.assertEqual(got["message_structure"]["status"], "FAIL")
        self.assertIn("exit 1", got["message_structure"]["reason"])
        self.assertEqual(got["sentinels"]["status"], "NOT_RUN")

    def test_skips_apply_to_layers_and_checks_without_result(self):
        s = self.fx.build(skips=["golden=opt-in (GOLDEN_CHECK=1)",
                                 "counter_accuracy=COUNTER_CHECK=0",
                                 "independent_read=INDEPENDENT_READ_CHECK=0"])
        got = {c["name"]: c for c in s["checks"]}
        self.assertEqual(got["golden"], {"name": "golden", "layer": "validate", "status": "SKIP",
                                         "reason": "opt-in (GOLDEN_CHECK=1)", "ratio": None})
        self.assertEqual(got["mem_read"]["status"], "SKIP")
        self.assertEqual(got["flops_dp"]["status"], "PASS")
        self.assertEqual(got["independent_read"]["status"], "FAIL")

    def test_unknown_skip_is_rejected(self):
        with self.assertRaises(ValueError):
            self.fx.build(skips=["nope=x"])

    def test_missing_preflight_fails(self):
        self.fx.results.write_text("")
        got = {c["name"]: c for c in self.fx.build(validate_report=None, validate_rc=None)["checks"]}
        self.assertEqual(got["preflight"]["status"], "FAIL")
        self.assertEqual(got["message_structure"]["status"], "NOT_RUN")

    def test_readme_documents_schema(self):
        readme = (MONITOR / "README.md").read_text(encoding="utf-8")
        for name, _, _ in cs.CHECKS:
            self.assertIn("`%s`" % name, readme, name)
        for key in TOP_KEYS | ARCH_KEYS:
            self.assertIn("`%s`" % key, readme, key)


class Cli(unittest.TestCase):
    def test_write_exit_code_and_file(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fixture(d)
            out = os.path.join(d, "sub", "summary.json")
            cmd = [sys.executable, str(LIB), "write", "--out", out, "--results", str(fx.results),
                   "--validate-report", str(fx.report), "--validate-rc", "1", "--caps",
                   str(fx.caps), "--rpm-build", "x", "--cpuinfo", str(fx.cpuinfo),
                   "--pmu-name", str(fx.pmu), "--skip", "golden=opt-in"]
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("overall=FAIL", p.stdout)
            s = json.loads(Path(out).read_text())
            self.assertEqual(s["overall"], "FAIL")
            fx.report.write_text(REPORT.replace("FAIL independent_read", "PASS independent_read"))
            cmd[cmd.index("--validate-rc") + 1] = "0"
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == "__main__":
    unittest.main()
