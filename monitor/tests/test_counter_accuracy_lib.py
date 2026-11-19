#!/usr/bin/env python3
"""Unit tests for scripts/lib/counter_accuracy.py (the counter-accuracy layer of
rpm_debug_shm_verify.sh): payload parsing, wrap-aware deltas, the tolerance table, and the
memory / FLOP / instruction / energy verdicts on Intel, AMD and Grace payload shapes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MONITOR / "scripts"))

from lib import counter_accuracy as ca  # noqa: E402

LIB = MONITOR / "scripts" / "lib" / "counter_accuracy.py"


def snap(rows, widths=None, ts=100.0, powercap=None):
    """Snapshot dict in read_snapshot() shape; widths default to 48 for every key."""
    if widths is None:
        widths = {}
        for t, devs in rows.items():
            for vals in devs.values():
                for k in vals:
                    widths.setdefault(t, {})[k] = 48
    return {"ts": ts, "wall": ts, "rows": rows, "widths": widths, "powercap": powercap}


def by_name(results):
    return {r["name"]: r for r in results}


class ToleranceTable(unittest.TestCase):
    def test_table_matches_discovery_tolerances(self):
        self.assertEqual(ca.TOLERANCES["flops"], (0.90, 1.10))
        self.assertEqual(ca.TOLERANCES["memory"], (0.75, 1.50))
        self.assertEqual(ca.TOLERANCES["energy"], (0.95, 1.05))
        self.assertEqual(ca.TOLERANCES["instructions"], (0.95, 1.05))
        self.assertEqual(ca.GRACE_FLOPS_PER_OP_CEILING, {"dp": 4, "sp": 8, "hp": 16})

    def test_within_is_inclusive_and_rejects_none(self):
        self.assertTrue(ca.within("memory", 0.75))
        self.assertTrue(ca.within("memory", 1.5))
        self.assertFalse(ca.within("memory", 1.51))
        self.assertFalse(ca.within("energy", None))

    def test_every_window_check_is_a_counter_check(self):
        listed = [c for checks in ca.WINDOW_CHECKS.values() for c in checks]
        self.assertEqual(sorted(listed), sorted(ca.COUNTER_CHECKS))

    def test_intel_lane_weights(self):
        w = ca.INTEL_FP_WEIGHTS
        widths = ("scalar", "128b_packed", "256b_packed", "512b_packed")
        self.assertEqual([w["dp"]["fp_arith_inst_retired_%s_double" % s] for s in widths],
                         [1, 2, 4, 8])
        self.assertEqual([w["sp"]["fp_arith_inst_retired_%s_single" % s] for s in widths],
                         [1, 4, 8, 16])


class Parsing(unittest.TestCase):
    SCHEMA = (
        "$x\n!host_cpu_hw instr_retired,E,W=48 cpu_util_total_accum_us,E,W=64,R=S "
        "fp_ops_retired,E,W=64,U=FLOP\n!intel_x86_rapl pkg_energy,E,W=32,U=mJ\n"
    )

    def test_schema_widths_and_slow_flag(self):
        s = ca.parse_schema(self.SCHEMA)
        self.assertEqual(s["host_cpu_hw"][0], ("instr_retired", 48, False))
        self.assertEqual(s["host_cpu_hw"][1], ("cpu_util_total_accum_us", 64, True))
        self.assertEqual(s["intel_x86_rapl"], [("pkg_energy", 32, False)])

    def test_fast_rows_skip_slow_keys_and_keep_sentinels(self):
        s = ca.parse_schema(self.SCHEMA)
        body = "\n12.5 1234 hostA\nhost_cpu_hw 0 @fast 10 -2\nintel_x86_rapl 0 @fast 7\n"
        ts, rows = ca.parse_sample(body, s)
        self.assertEqual(ts, 12.5)
        self.assertEqual(rows["host_cpu_hw"]["0"], {"instr_retired": 10, "fp_ops_retired": -2})
        self.assertEqual(rows["intel_x86_rapl"]["0"], {"pkg_energy": 7})

    def test_untiered_rows_map_every_key(self):
        s = ca.parse_schema(self.SCHEMA)
        _, rows = ca.parse_sample("1.0 - h\nhost_cpu_hw 3 1 2 3\n", s)
        self.assertEqual(rows["host_cpu_hw"]["3"]["fp_ops_retired"], 3)

    def test_unknown_type_and_garbage_are_ignored(self):
        s = ca.parse_schema(self.SCHEMA)
        _, rows = ca.parse_sample("1.0 - h\nother 0 1\nhost_cpu_hw 0 @fast x 4\n", s)
        self.assertNotIn("other", rows)
        self.assertEqual(rows["host_cpu_hw"]["0"], {"fp_ops_retired": 4})

    def test_empty_body_has_no_timestamp(self):
        self.assertEqual(ca.parse_sample("\n\n", {}), (None, {}))

    def test_read_snapshot_from_shm_dir(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "schema").write_text(self.SCHEMA)
            Path(d, "fast").write_text("\n5.0 - h\nintel_x86_rapl 0 @fast 9\n")
            got = ca.read_snapshot(d)
            self.assertEqual(got["ts"], 5.0)
            self.assertEqual(got["widths"]["intel_x86_rapl"]["pkg_energy"], 32)
            self.assertIsNone(ca.read_snapshot(os.path.join(d, "missing")))


class Deltas(unittest.TestCase):
    def test_wrap_mod_width(self):
        a = snap({"t": {"0": {"k": 2**48 - 10}}})
        b = snap({"t": {"0": {"k": 5}}})
        self.assertEqual(ca.sum_delta(a, b, "t", "k"), (15, None))

    def test_sentinel_is_a_problem_not_a_value(self):
        a = snap({"t": {"0": {"k": 1}, "1": {"k": 1}}})
        b = snap({"t": {"0": {"k": 3}, "1": {"k": -6}}})
        total, problem = ca.sum_delta(a, b, "t", "k")
        self.assertIsNone(total)
        self.assertIn("-6", problem)

    def test_missing_type_or_row(self):
        self.assertIn("no t rows", ca.sum_delta(snap({}), snap({}), "t", "k")[1])
        a = snap({"t": {"0": {"k": 1}}})
        b = snap({"t": {"0": {"k": 2}, "1": {"k": 2}}})
        self.assertIn("first snapshot", ca.sum_delta(a, b, "t", "k")[1])


def intel_mem(reads, writes, hbm_reads=0):
    return {"intel_x86_uncore_imc_spr": {
        "mbox0": {"dram_cas_reads": reads, "dram_cas_writes": writes,
                  "hbm_cas_reads": 0, "hbm_cas_writes": 0},
        "hbm0": {"dram_cas_reads": 0, "dram_cas_writes": 0,
                 "hbm_cas_reads": hbm_reads, "hbm_cas_writes": 0}}}


class Memory(unittest.TestCase):
    def test_intel_read_sums_dram_64_and_hbm_32(self):
        a, b = snap(intel_mem(0, 0)), snap(intel_mem(1000, 10, 2000))
        r = by_name(ca.check_memory(a, b, "read", 1000 * 64 + 2000 * 32))
        self.assertEqual(r["mem_read"]["status"], "PASS")
        self.assertAlmostEqual(r["mem_read"]["ratio"], 1.0)
        self.assertEqual(r["mem_socket0"]["status"], "NOT_RUN")
        self.assertIn("every socket", r["mem_socket0"]["reason"])

    def test_intel_read_low_fails(self):
        a, b = snap(intel_mem(0, 0)), snap(intel_mem(500, 0))
        r = by_name(ca.check_memory(a, b, "read", 1000 * 64))
        self.assertEqual(r["mem_read"]["status"], "FAIL")
        self.assertAlmostEqual(r["mem_read"]["ratio"], 0.5)

    def test_intel_write(self):
        a, b = snap(intel_mem(0, 0)), snap(intel_mem(900, 1000))
        r = by_name(ca.check_memory(a, b, "write", 1000 * 64))
        self.assertEqual(r["mem_write"]["status"], "PASS")
        self.assertEqual(set(r), {"mem_write"})

    def test_amd_df_read_pass_and_write_not_run(self):
        def df(v):
            return {"amd_x86_uncore_df_turin": {"df": {
                "dram_chan0_bytes": v, "dram_chan1_bytes": v,
                "dram_chan2_bytes": v, "dram_chan3_bytes": v}}}
        r = by_name(ca.check_memory(snap(df(0)), snap(df(100)), "read", 400 * 64))
        self.assertEqual(r["mem_read"]["status"], "PASS")
        w = by_name(ca.check_memory(snap(df(0)), snap(df(100)), "write", 400 * 64))
        self.assertEqual(w["mem_write"]["status"], "NOT_RUN")
        self.assertIn("read + write", w["mem_write"]["reason"])

    def test_not_built_everywhere_is_excluded(self):
        def df(v):
            return {"amd_x86_uncore_df_rome": {"df": {
                "dram_chan0_bytes": v, "dram_chan1_bytes": v,
                "dram_chan2_bytes": -10, "dram_chan3_bytes": -10}}}
        r = by_name(ca.check_memory(snap(df(0)), snap(df(100)), "read", 200 * 64))
        self.assertEqual(r["mem_read"]["status"], "PASS")
        self.assertIn("NOT_BUILT", r["mem_read"]["reason"])

    def test_grace_scf_socket0_share(self):
        def scf(r0, r1):
            return {"arm_aarch64_imc": {"scf0": {"dram_cas_reads": r0, "dram_cas_writes": 0},
                                        "scf1": {"dram_cas_reads": r1, "dram_cas_writes": 0}}}
        r = by_name(ca.check_memory(snap(scf(0, 0)), snap(scf(1000, 10)), "read", 1010 * 64))
        self.assertEqual(r["mem_read"]["status"], "PASS")
        self.assertEqual(r["mem_socket0"]["status"], "PASS")
        self.assertGreater(r["mem_socket0"]["ratio"], 0.98)
        r = by_name(ca.check_memory(snap(scf(0, 0)), snap(scf(500, 500)), "read", 1000 * 64))
        self.assertEqual(r["mem_socket0"]["status"], "FAIL")

    def test_no_memory_type_and_sentinel_row_are_not_run(self):
        r = by_name(ca.check_memory(snap({}), snap({}), "read", 10))
        self.assertEqual(r["mem_read"]["status"], "NOT_RUN")
        a, b = snap(intel_mem(0, 0)), snap(intel_mem(-5, 0))
        r = by_name(ca.check_memory(a, b, "read", 10))
        self.assertEqual(r["mem_read"]["status"], "NOT_RUN")
        self.assertIn("-5", r["mem_read"]["reason"])

    def test_missing_oracle_is_not_run(self):
        a, b = snap(intel_mem(0, 0)), snap(intel_mem(10, 0))
        r = by_name(ca.check_memory(a, b, "read", None))
        self.assertEqual(r["mem_read"]["status"], "NOT_RUN")


def cpus(**per_cpu):
    return {"host_cpu_hw": {"0": dict(per_cpu), "1": dict(per_cpu)}}


class Flops(unittest.TestCase):
    def test_intel_dp_lane_weighted(self):
        keys = {k: 0 for k in list(ca.INTEL_FP_WEIGHTS["dp"]) + list(ca.INTEL_FP_WEIGHTS["sp"])}
        a = snap(cpus(**keys))
        later = dict(keys)
        later["fp_arith_inst_retired_256b_packed_double"] = 1000
        later["fp_arith_inst_retired_scalar_double"] = 100
        later["fp_arith_inst_retired_256b_packed_single"] = 7
        b = snap(cpus(**later))
        r = ca.check_flops(a, b, "dp", 2 * (4000 + 100), "intel")
        self.assertEqual((r["name"], r["status"]), ("flops_dp", "PASS"))
        self.assertAlmostEqual(r["ratio"], 1.0)
        r = ca.check_flops(a, b, "dp", 2 * 4100 * 1.25, "intel")
        self.assertEqual(r["status"], "FAIL")

    def test_intel_sentinel_is_not_run(self):
        keys = {k: 0 for k in ca.INTEL_FP_WEIGHTS["dp"]}
        bad = dict(keys, fp_arith_inst_retired_scalar_double=-2)
        r = ca.check_flops(snap(cpus(**keys)), snap(cpus(**bad)), "dp", 100, "intel")
        self.assertEqual(r["status"], "NOT_RUN")

    def test_amd_zen5_split(self):
        widths = {"host_cpu_hw": {"fp_ops_retired": 64, "amd_fp_dp_ops": 64}}
        a = snap(cpus(fp_ops_retired=0, amd_fp_dp_ops=0), widths=widths)
        b = snap(cpus(fp_ops_retired=9000, amd_fp_dp_ops=6000), widths=widths)
        dp = ca.check_flops(a, b, "dp", 12000, "amd")
        sp = ca.check_flops(a, b, "sp", 6000, "amd")
        self.assertEqual((dp["status"], sp["status"]), ("PASS", "PASS"))
        self.assertIn("amd_fp_dp_ops", dp["reason"])

    def test_amd_zen3_total_only(self):
        a = snap(cpus(fp_ops_retired=0, amd_fp_dp_ops=-2))
        b = snap(cpus(fp_ops_retired=5000, amd_fp_dp_ops=-2))
        r = ca.check_flops(a, b, "sp", 10000, "amd")
        self.assertEqual(r["status"], "PASS")
        self.assertIn("no precision split", r["reason"])
        self.assertEqual(ca.check_flops(a, b, "sp", 20000, "amd")["status"], "FAIL")

    def test_grace_flops_per_op_ceiling(self):
        a = snap(cpus(arm_fp_dp_spec=0))
        b = snap(cpus(arm_fp_dp_spec=1000))
        ok = ca.check_flops(a, b, "dp", 8000, "grace")
        self.assertEqual(ok["status"], "PASS")
        self.assertAlmostEqual(ok["ratio"], 4.0)
        low = ca.check_flops(a, b, "dp", 10000, "grace")
        self.assertEqual(low["status"], "FAIL")
        self.assertIn("ceiling 4", low["reason"])
        self.assertEqual(ca.check_flops(a, a, "dp", 10000, "grace")["status"], "FAIL")

    def test_other_arch_and_missing_oracle(self):
        self.assertEqual(ca.check_flops(snap({}), snap({}), "dp", 1, "other")["status"], "NOT_RUN")
        a = snap(cpus(arm_fp_dp_spec=0))
        self.assertEqual(ca.check_flops(a, a, "dp", None, "grace")["status"], "NOT_RUN")


class Instructions(unittest.TestCase):
    A = snap(cpus(instr_retired=0, instr_retired_any=0))
    B = snap(cpus(instr_retired=5000, instr_retired_any=5000))

    def test_pass_and_fail(self):
        r = ca.check_instructions(self.A, self.B, {"output": "10000,,instructions,1,100.00,,\n"})
        self.assertEqual(r["status"], "PASS")
        r = ca.check_instructions(self.A, self.B, {"output": "# c\n12000,,instructions,1,100.00\n"})
        self.assertEqual(r["status"], "FAIL")

    def test_instr_retired_any_fallback(self):
        a = snap(cpus(instr_retired=-2, instr_retired_any=0))
        b = snap(cpus(instr_retired=-2, instr_retired_any=5000))
        r = ca.check_instructions(a, b, {"output": "10000,,instructions,1,100.00,,\n"})
        self.assertEqual(r["status"], "PASS")
        self.assertIn("instr_retired_any", r["reason"])

    def test_missing_perf_or_not_counted(self):
        self.assertEqual(ca.check_instructions(self.A, self.B, None)["status"], "NOT_RUN")
        r = ca.check_instructions(self.A, self.B, {"output": "", "error": "perf not found (x)"})
        self.assertIn("perf not found", r["reason"])
        r = ca.check_instructions(self.A, self.B, {"output": "<not counted>,,instructions,0,0,,"})
        self.assertEqual(r["status"], "NOT_RUN")

    def test_no_pmu_daemon_is_not_run(self):
        a = snap(cpus(instr_retired=-2, instr_retired_any=-2))
        r = ca.check_instructions(a, a, {"output": "10000,,instructions,1,100.00,,\n"})
        self.assertEqual(r["status"], "NOT_RUN")


def rapl(pkg, dram=None, vendor="intel"):
    row = {"pkg_energy": pkg}
    if dram is not None:
        row["dram_energy"] = dram
    t = "%s_x86_rapl" % vendor
    return {t: {"0": dict(row), "1": dict(row)}}, {t: {k: 32 for k in row}}


def zones(pkg_uj, dram_uj=None, max_uj=1000000000):
    z = {}
    for n in (0, 1):
        z["intel-rapl:%d" % n] = {"name": "package-%d" % n, "energy_uj": pkg_uj, "max_uj": max_uj}
        if dram_uj is not None:
            z["intel-rapl:%d:0" % n] = {"name": "dram", "energy_uj": dram_uj, "max_uj": max_uj}
    return z


class Energy(unittest.TestCase):
    def test_wrap_on_both_sides_passes(self):
        # Daemon: 1500 / 300 mJ per socket across the 2^32 wrap; powercap: 1.5 / 0.3 J per
        # zone across max_energy_range_uj (range is max + 1).
        rows_a, w = rapl(2**32 - 500, 2**32 - 100)
        rows_b, _ = rapl(1000, 200)
        a = snap(rows_a, w, powercap=zones(999999000, 999999900))
        b = snap(rows_b, w, powercap=zones(1498999, 299899))
        r = by_name(ca.check_energy(a, b))
        self.assertEqual(r["energy_pkg"]["status"], "PASS", r)
        self.assertAlmostEqual(r["energy_pkg"]["ratio"], 1.0)
        self.assertEqual(r["energy_dram"]["status"], "PASS", r)

    def test_mismatch_fails(self):
        rows_a, w = rapl(0, 0)
        rows_b, _ = rapl(1000, 100)
        a = snap(rows_a, w, powercap=zones(0, 0))
        b = snap(rows_b, w, powercap=zones(1100000, 100000))
        r = by_name(ca.check_energy(a, b))
        self.assertEqual(r["energy_pkg"]["status"], "FAIL")
        self.assertEqual(r["energy_dram"]["status"], "PASS")

    def test_amd_has_no_dram(self):
        rows_a, w = rapl(0, vendor="amd")
        rows_b, _ = rapl(1000, vendor="amd")
        a = snap(rows_a, w, powercap=zones(0))
        b = snap(rows_b, w, powercap=zones(1000000))
        r = by_name(ca.check_energy(a, b))
        self.assertEqual(r["energy_pkg"]["status"], "PASS")
        self.assertEqual(r["energy_dram"]["status"], "NOT_RUN")

    def test_missing_sources_are_not_run(self):
        rows_a, w = rapl(0, 0)
        r = by_name(ca.check_energy(snap(rows_a, w), snap(rows_a, w)))
        self.assertEqual(r["energy_pkg"]["status"], "NOT_RUN")
        self.assertIn("powercap", r["energy_pkg"]["reason"])
        r = by_name(ca.check_energy(snap({}, powercap=zones(0)), snap({}, powercap=zones(1))))
        self.assertIn("RAPL", r["energy_pkg"]["reason"])

    def test_read_powercap_tree(self):
        with tempfile.TemporaryDirectory() as d:
            def zone(name, label, uj, mx="1000"):
                p = Path(d, name)
                p.mkdir()
                (p / "name").write_text(label + "\n")
                (p / "energy_uj").write_text(str(uj) + "\n")
                (p / "max_energy_range_uj").write_text(mx + "\n")
            zone("intel-rapl:0", "package-0", 10)
            zone("intel-rapl:0:0", "dram", 3)
            zone("intel-rapl-mmio:0", "package-0", 999)
            zone("intel-rapl:1", "psys", 5)
            got = ca.read_powercap(d)
            self.assertEqual(sorted(got), ["intel-rapl:0", "intel-rapl:0:0", "intel-rapl:1"])
            self.assertEqual(got["intel-rapl:0"],
                             {"name": "package-0", "energy_uj": 10, "max_uj": 1000})
            self.assertIsNone(ca.read_powercap(os.path.join(d, "none")))


class Window(unittest.TestCase):
    def test_evaluate_missing_window_is_not_run(self):
        res = ca.evaluate_window(None, "gemm-dp", "intel")
        self.assertEqual([r["name"] for r in res], list(ca.WINDOW_CHECKS["gemm-dp"]))
        self.assertTrue(all(r["status"] == "NOT_RUN" for r in res))

    def test_evaluate_stale_daemon_fails(self):
        w = {"error": "fast payload did not advance within 5 s", "error_status": "FAIL"}
        res = ca.evaluate_window(w, "memory-write", "intel")
        self.assertEqual(res[0]["status"], "FAIL")

    def test_evaluate_without_oracle_keeps_energy(self):
        rows_a, wd = rapl(0, 0)
        rows_b, _ = rapl(1000, 100)
        w = {"a": snap(rows_a, wd, powercap=zones(0, 0)),
             "b": snap(rows_b, wd, powercap=zones(1000000, 100000)),
             "load": {"rc": 2, "timed_out": False, "log_tail": "canary_gemm_load: bad\n"},
             "perf": None}
        r = by_name(ca.evaluate_window(w, "gemm-dp", "intel"))
        self.assertEqual(r["flops_dp"]["status"], "NOT_RUN")
        self.assertIn("total_flops", r["flops_dp"]["reason"])
        self.assertEqual(r["energy_pkg"]["status"], "PASS")

    def test_oracle_parsing(self):
        self.assertEqual(ca.oracle_from_log(
            "read_load done total_bytes_read=4096 passes=2 elapsed=1s", "memory-read"), 4096)
        self.assertEqual(ca.oracle_from_log(
            "write_load done total_bytes_written=77 passes=1 elapsed=1s", "memory-write"), 77)
        self.assertEqual(ca.oracle_from_log(
            "gemm_load dp done total_flops=2147483648 elapsed=1s", "gemm-sp"), 2147483648)
        self.assertIsNone(ca.oracle_from_log("nothing", "gemm-dp"))

    def test_wait_new_snapshot_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "schema").write_text("!t k,E\n")
            Path(d, "fast").write_text("\n1.0 - h\nt 0 @fast 1\n")
            self.assertIsNone(ca.wait_new_snapshot(d, 0.5, poll=0.1))


class Arch(unittest.TestCase):
    def arch(self, text, pmu="skylake"):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "cpuinfo").write_text(text)
            Path(d, "pmu").write_text(pmu + "\n")
            return ca.arch_info(os.path.join(d, "cpuinfo"), os.path.join(d, "pmu"))

    def test_classes(self):
        intel = self.arch("vendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 143\n",
                          "sapphire_rapids")
        self.assertEqual(intel, {"class": "intel", "vendor": "GenuineIntel", "family": "6",
                                 "model": "143", "pmu_name": "sapphire_rapids"})
        amd = self.arch("vendor_id : AuthenticAMD\ncpu family : 26\nmodel : 2\n")
        self.assertEqual(amd["class"], "amd")
        grace = self.arch("processor : 0\nCPU implementer : 0x41\nCPU part : 0xd4f\n")
        self.assertEqual((grace["class"], grace["family"], grace["model"]),
                         ("grace", "0x41", "0xd4f"))
        self.assertEqual(self.arch("CPU implementer : 0x41\nCPU part : 0xd0c\n")["class"], "other")
        self.assertEqual(ca.arch_info("/nonexistent", "/nonexistent")["class"], "other")

    def test_socket0_cpus(self):
        with tempfile.TemporaryDirectory() as d:
            for cpu, pkg, online in ((0, 0, None), (1, 0, "1"), (2, 1, "1"), (3, 0, "1"),
                                     (5, 0, "0")):
                top = Path(d, "cpu%d" % cpu, "topology")
                top.mkdir(parents=True)
                (top / "physical_package_id").write_text("%d\n" % pkg)
                if online is not None:
                    Path(d, "cpu%d" % cpu, "online").write_text(online + "\n")
            Path(d, "cpufreq").mkdir()
            self.assertEqual(ca.socket0_cpus(d), ("0-1,3", 3))
            self.assertEqual(ca.socket0_cpus(os.path.join(d, "none")), ("", 0))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(LIB)] + list(args), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)

    def test_record_and_summarize(self):
        with tempfile.TemporaryDirectory() as d:
            res = os.path.join(d, "r.tsv")
            p = self.run_cli("record", "--results", res, "--status", "SKIP",
                             "--reason", "COUNTER_CHECK=0", "--all")
            self.assertEqual(p.returncode, 0, p.stdout)
            lines = Path(res).read_text().splitlines()
            self.assertEqual([ln.split("\t")[0] for ln in lines], list(ca.COUNTER_CHECKS))
            p = self.run_cli("summarize", "--results", res)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertIn("RESULT: PASS", p.stdout)
            with open(res, "a") as f:
                f.write("flops_dp\tFAIL\t0.5000\tlow\n")
            p = self.run_cli("summarize", "--results", res)
            self.assertEqual(p.returncode, 1)
            self.assertIn("RESULT: FAIL", p.stdout)

    def test_window_runs_command_between_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            shm = Path(d, "shm")
            shm.mkdir()
            (shm / "schema").write_text("!t k,E\n")
            (shm / "fast").write_text("\n1.0 - h\nt 0 @fast 1\n")
            # The fake daemon advances once before the window's first snapshot (1.5) and the
            # "load" makes it advance again after the load ended (3.0).
            load = Path(d, "load.sh")
            load.write_text(
                "#!/bin/sh\nprintf '\\n2.0 - h\\nt 0 @fast 5\\n' > %s/fast\n"
                "echo 'read_load done total_bytes_read=64 passes=1 elapsed=1s'\n"
                "(sleep 1; printf '\\n3.0 - h\\nt 0 @fast 9\\n' > %s/fast) &\n" % (shm, shm))
            load.chmod(0o755)
            out = os.path.join(d, "w.json")
            writer = subprocess.Popen(
                ["sh", "-c", "sleep 0.5; printf '\\n1.5 - h\\nt 0 @fast 2\\n' > %s/fast" % shm])
            p = self.run_cli("window", "--shm", str(shm), "--out", out, "--wait-max", "5",
                             "--load-timeout", "10", "--log", os.path.join(d, "load.log"),
                             "--", str(load))
            writer.wait(timeout=10)
            self.assertEqual(p.returncode, 0, p.stdout)
            w = json.loads(Path(out).read_text())
            self.assertEqual(w["a"]["ts"], 1.5)
            self.assertEqual(w["b"]["ts"], 3.0)
            self.assertEqual(w["load"]["rc"], 0)
            self.assertIn("total_bytes_read=64", w["load"]["log_tail"])
            self.assertIsNone(w["perf"])

    def test_window_stale_daemon_fails_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            shm = Path(d, "shm")
            shm.mkdir()
            (shm / "schema").write_text("!t k,E\n")
            (shm / "fast").write_text("\n1.0 - h\nt 0 @fast 1\n")
            out = os.path.join(d, "w.json")
            p = self.run_cli("window", "--shm", str(shm), "--out", out, "--wait-max", "1",
                             "--load-timeout", "5", "--log", os.path.join(d, "l"), "--", "true")
            self.assertEqual(p.returncode, 3)
            w = json.loads(Path(out).read_text())
            self.assertEqual(w["error_status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
