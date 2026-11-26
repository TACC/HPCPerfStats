"""Unit tests for the hpcperfstats_raw reader (run: python3 -m unittest discover monitor/python/tests)."""

import gzip
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import hpcperfstats_raw as hr  # noqa: E402

ARCHIVE = HERE / "fixtures" / "archive_sample.txt"
SHARED = HERE / "fixtures" / "shared_node_sample.txt"


class SchemaTest(unittest.TestCase):
    def test_entry_options(self):
        e = hr.parse_schema_entry("rd_sectors,E,U=512B,R=S")
        self.assertEqual((e.key, e.is_event, e.unit, e.multiplier, e.width, e.slow),
                         ("rd_sectors", True, "B", 512, 64, True))

    def test_lowercase_flag_and_rate_unit(self):
        # host_block declares `e`; the daemon upper-cases option letters.
        self.assertTrue(hr.parse_schema_entry("rd_ios,e").is_event)
        e = hr.parse_schema_entry("gpu_peak,U=FLOP/s")
        self.assertEqual((e.unit, e.multiplier, e.is_gauge), ("FLOP/s", 1, True))

    def test_width_and_fast_tier_r(self):
        e = hr.parse_schema_entry("pkg_energy,E,W=32,U=mJ,R=F")
        self.assertEqual((e.width, e.slow, e.unit), (32, False, "mJ"))

    def test_unknown_option_and_trailing_comma_ignored(self):
        e = hr.parse_schema_entry("load_1,Z=9,")
        self.assertEqual((e.key, e.is_event), ("load_1", False))

    def test_fast_subset(self):
        ts = hr.parse_schema_line("!host_mem a,U=KB b,U=KB,R=S c")
        self.assertEqual([e.key for e in ts.fast_entries], ["a", "c"])


class ValueTest(unittest.TestCase):
    def test_reading_and_sentinel(self):
        self.assertEqual(hr.parse_value("0"), 0)
        s = hr.parse_value("-3")
        self.assertTrue(hr.is_sentinel(s))
        self.assertEqual((s.slug, s.bridgeable), ("READ_FAILED", True))

    def test_unknown_code_never_bridges(self):
        s = hr.parse_value("-200")
        self.assertEqual((s.slug, s.bridgeable), ("UNKNOWN", False))

    def test_garbage_rejected(self):
        for tok in ("1.5", "abc", "-", "--1"):
            with self.assertRaises(hr.FormatError):
                hr.parse_value(tok)

    def test_counter_delta(self):
        self.assertEqual(hr.counter_delta(10, 25), 15)
        self.assertEqual(hr.counter_delta(2**32 - 5, 3, 32), 8)
        self.assertIsNone(hr.counter_delta(1000, 10))
        self.assertIsNone(hr.counter_delta(2**47, 5, 64))


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.samples = hr.read_file(ARCHIVE, strict=True)

    def test_header(self):
        h = self.samples[0].header
        self.assertEqual((h.program, h.version, h.hostname, h.machine), (
            "hpcperfstats", "3.1", "c401-001.example.org", "x86_64"))
        self.assertTrue(h.kernel_version.startswith("#1 SMP"))
        self.assertEqual((h.uptime, h.sentinels_version), (86400, 1))
        self.assertIn("x86perf", h.build_features)
        self.assertEqual(sorted(h.schemas)[:2], ["host_block", "host_cpu"])

    def test_samples_tiers_jobs(self):
        self.assertEqual([s.tier for s in self.samples],
                         ["@full", "@fast", "@fast", "@fast", "@fast", "@full"])
        self.assertEqual([s.jobid for s in self.samples], ["-", "-", "12345", "12345", "12345", "12345"])
        self.assertEqual([m.text for m in self.samples[2].marks], ["begin 12345"])
        self.assertEqual(self.samples[5].marks[0].jids, ["12345"])

    def test_fast_row_omits_slow_keys(self):
        full = self.samples[0].row("host_mem", "0")
        fast = self.samples[1].row("host_mem", "0")
        self.assertIn("dirty", full.values)
        self.assertNotIn("dirty", fast.values)
        self.assertIsNone(fast.get("dirty"))
        self.assertEqual(fast.values["mem_used"], 64000000)

    def test_device_with_space(self):
        row = self.samples[0].row("host_proc", "Web Content/4200/0-127/0")
        self.assertIsNotNone(row)
        self.assertEqual(row.values, {"uid": 812345, "vm_rss": 204800, "threads": 30})
        p = hr.parse_proc_dev(row.dev)
        self.assertEqual((p.name, p.pid, p.cpus_allowed, p.mems_allowed), ("Web Content", 4200, "0-127", "0"))

    def test_proc_name_with_slash(self):
        self.assertEqual(hr.parse_proc_dev("kworker/u8:2/77/0-3/0").name, "kworker/u8:2")

    def test_scaled(self):
        row = self.samples[0].row("host_block", "nvme0n1")
        self.assertEqual(row.scaled("rd_sectors"), 81000 * 512)

    def test_series_wrap_and_bridge(self):
        pts = list(hr.iter_series(self.samples, "intel_x86_rapl", "0", "pkg_energy"))
        self.assertEqual([p.delta for p in pts], [None, 496, 1000, 1000, None, 2000])
        self.assertEqual(pts[4].value.slug, "READ_FAILED")
        # The bridged delta spans the 60 s gap around the sentinel.
        self.assertAlmostEqual(pts[5].seconds, 60.0, places=2)

    def test_series_scaled_counter(self):
        pts = list(hr.iter_series(self.samples, "host_ib", "mlx5_0.1", "port_rcv_data"))
        self.assertEqual(pts[3].delta, (2500000 - 1000000) * 4)
        self.assertEqual(pts[5].delta, (3700000 - 2500000) * 4)

    def test_non_bridgeable_sentinel_breaks_chain(self):
        text = ARCHIVE.read_text().replace("intel_x86_rapl 0 @fast -3", "intel_x86_rapl 0 @fast -2")
        pts = list(hr.iter_series(hr.iter_samples(text), "intel_x86_rapl", "0", "pkg_energy"))
        self.assertIsNone(pts[5].delta)

    def test_header_restart_mid_file(self):
        headers = {id(s.header) for s in self.samples}
        self.assertEqual(len(headers), 2)


class RobustnessTest(unittest.TestCase):
    def test_bad_lines_reported(self):
        problems = []
        lines = ["!host_cpu a,E b,E", "", "100.0 - h", "host_cpu 0 1", "host_cpu 1 1 x", "?junk", "host_cpu 2 3 4"]
        samples = list(hr.iter_samples(lines, problems=problems))
        self.assertEqual(len(samples[0].rows), 1)
        self.assertEqual(len(problems), 3)
        with self.assertRaises(hr.FormatError):
            list(hr.iter_samples(lines, strict=True))

    def test_rows_before_header_use_given_header(self):
        header = hr.read_file(ARCHIVE)[0].header
        tail = ARCHIVE.read_text().split("\n\n", 2)[2]  # drops the header block and first sample
        samples = hr.read_file(tail.splitlines(), header=header, strict=True)
        row = samples[0].row("host_proc", "python3/4121/0-127/0")
        self.assertEqual(row.values, {"vm_rss": 1048576, "threads": 8})

    def test_no_schema_uses_tier_token_for_device(self):
        row = hr.parse_row("host_proc gunicorn: worker/1/0-3/0 @fast 1 2", None)
        self.assertEqual((row.dev, row.tier, row.raw), ("gunicorn: worker/1/0-3/0", "@fast", ("1", "2")))

    def test_legacy_rows_without_tier(self):
        text = "!t a,E b\n\n1.0 - h\nt dev 5 -1\n"
        row = hr.read_file(text, strict=True)[0].rows[0]
        self.assertEqual((row.tier, row.values["a"]), (None, 5))
        self.assertEqual(row.values["b"].slug, "NOT_UPDATED")

    def test_unknown_property_kept(self):
        h = hr.read_file("$hpcperfstats 9.9\n$newthing a b\n!t a\n\n1.0 - h\nt - 1\n")[0].header
        self.assertEqual(h.properties["newthing"], "a b")
        self.assertIsNone(h.build)

    def test_gzip_and_host_archive(self):
        with tempfile.TemporaryDirectory() as d:
            data = ARCHIVE.read_bytes()
            with gzip.open(os.path.join(d, "1767225600.gz"), "wb") as fh:
                fh.write(data)
            shared = os.path.join(d, "1767300000")
            with open(shared, "wb") as fh:
                fh.write(data)
            os.link(shared, os.path.join(d, "current"))
            open(os.path.join(d, "current.lock"), "w").close()
            samples = list(hr.iter_host_archive(d))
        self.assertEqual(len(samples), 12)


class SharedNodeTest(unittest.TestCase):
    def test_per_job_split(self):
        samples = hr.read_file(SHARED, strict=True)
        self.assertEqual([s.time for s in samples], [samples[0].time] * 3)
        self.assertEqual([s.jobid for s in samples], ["100", "200", "-"])
        aff = [m for m in samples[0].marks if m.kind == "affinity"][0]
        self.assertEqual((aff.fields["jid"], aff.fields["cpus"], aff.fields["gpus"], aff.fields["cg"]),
                         ("100", "0-1", "0", "sched"))
        self.assertEqual(samples[0].marks[0].jids, ["100", "200"])
        self.assertEqual(samples[1].row("host_mem_job", "j:200").values["mem_peak"].slug, "NOT_PRESENT")


if __name__ == "__main__":
    unittest.main()
