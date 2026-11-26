"""Drift guard: the C emit code, the Python reader and the docs must describe the same format.

Fails when the daemon starts printing a line kind, banner property, mark, tier token, schema
option or sentinel code that ``hpcperfstats_raw`` and ``docs/RAW_DATA_FORMAT.md`` do not know,
or when the per-metric reference (``docs/METRICS_REFERENCE.md``) is stale. The fix is always
to update the reader, the doc and (for keys) ``docs/metric_descriptions.tsv`` in the same
change; see ``cursor-rules/monitor-raw-data-format-docs.mdc``.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MONITOR = HERE.parents[1]
SRC = MONITOR / "src"
DOC = MONITOR / "docs" / "RAW_DATA_FORMAT.md"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(MONITOR / "scripts"))  # lib.keys_catalog, gen_metrics_reference

import hpcperfstats_raw as hr  # noqa: E402
from hpcperfstats_raw import reader, wire  # noqa: E402
from lib.keys_catalog import load_catalog  # noqa: E402

# Files that call stats_buffer_mark(), and the mark kinds each one prints.
MARK_SITES = {"monitor_daemon.c": {"begin", "end"}, "job_shared_emit.c": {"jobs", "affinity"}}
# Banner properties the reader interprets (the first banner line is `$<program> <version>`).
BANNER_PROPERTIES = {"hostname", "uname", "uptime", "build", "sentinels"}
SCHEMA_OPTIONS = {"C", "E", "U", "W", "R"}


def _src(name):
    return (SRC / name).read_text(encoding="utf-8")


def _doc():
    return DOC.read_text(encoding="utf-8")


def _function_body(text, name):
    start = text.index(name + "(")
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[brace:i]
    raise AssertionError("unbalanced " + name)


class LinePrefixDrift(unittest.TestCase):
    def test_prefix_characters(self):
        chars = dict(re.findall(r"#define SF_(\w+)_CHAR '(.)'", _src("stats_buffer.c")))
        self.assertEqual(chars["SCHEMA"], wire.SCHEMA_CHAR)
        self.assertEqual(chars["PROPERTY"], wire.PROPERTY_CHAR)
        self.assertEqual(chars["MARK"], wire.MARK_CHAR)
        self.assertEqual(set(chars), {"SCHEMA", "PROPERTY", "MARK", "DEVICES", "COMMENT"},
                         "new line prefix: teach hpcperfstats_raw.reader and RAW_DATA_FORMAT.md")

    def test_devices_and_comment_chars_unused(self):
        # The doc says no line starts with `@` or `#`; that holds while nothing prints them.
        uses = [
            (p.name, m)
            for p in SRC.glob("*.c")
            for m in re.findall(r"SF_(?:DEVICES|COMMENT)_CHAR", p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(len(uses), 2, "SF_DEVICES_CHAR / SF_COMMENT_CHAR now used: %r" % uses)

    def test_sample_header_and_row_formats(self):
        self.assertIn('"\\n%f %s %s\\n"', _src("stats_buffer.c"))
        fmt = _src("stats_text_format.c")
        self.assertIn('"%s %s%s", type->st_name, stats->s_dev', fmt)
        self.assertIn('" -%u", code', fmt)
        self.assertIn('" %llu"', fmt)

    def test_tier_tokens(self):
        tokens = set(re.findall(r'return " (@\w+)";', _src("stats_text_format.c")))
        self.assertEqual(tokens, set(wire.TIER_TOKENS))
        for t in wire.TIER_TOKENS:
            self.assertIn("`%s`" % t, _doc())


class HeaderDrift(unittest.TestCase):
    def test_banner_properties(self):
        body = _function_body(_src("stats_text_format.c"), "void stats_format_emit_property_banner")
        names = set(re.findall(r'"%c(\w+) ', body))
        self.assertEqual(names, BANNER_PROPERTIES, "banner changed: update reader._apply_property and the doc")
        self.assertIn('"%c%s %s\\n", prop_char, prog', body)
        doc = _doc()
        for n in names:
            self.assertIn("`$%s" % n, doc)
        h = reader.iter_records(["$x 1", "$build b", "$sentinels 2", "!t a"])
        hdr = next(h)
        self.assertEqual((hdr.program, hdr.build, hdr.sentinels_version), ("x", "b", 2))

    def test_schema_options(self):
        cases = set(re.findall(r"case '(\w)':", _src("schema_entry_parse.c")))
        self.assertEqual(cases, SCHEMA_OPTIONS, "schema option added: teach schema.parse_schema_entry")
        suffix = _function_body(_src("stats_text_format.c"), "size_t stats_format_schema_entry_suffix")
        emitted = set(re.findall(r'",([A-Z])', suffix)) | set(re.findall(r'"(,[A-Z])"', _src("stats_text_format.c")))
        self.assertTrue({"U", "W", "R"} <= {e.lstrip(",") for e in emitted})
        for opt in ("`E`", "`C`", "`U=<unit>`", "`W=<bits>`", "`R=S`"):
            self.assertIn(opt, _doc())


class MarkDrift(unittest.TestCase):
    def test_mark_call_sites(self):
        callers = {
            p.name for p in SRC.glob("*.c")
            if p.name != "stats_buffer.c" and "stats_buffer_mark(" in p.read_text(encoding="utf-8")
        }
        self.assertEqual(callers, set(MARK_SITES), "new %-mark call site: document it and update MARK_SITES")

    def test_mark_kinds_documented(self):
        doc = _doc()
        for name, kinds in MARK_SITES.items():
            text = _src(name)
            found = set(re.findall(r'(?:strf|snprintf)\([^;]*?"(\w+) (?:%s|\w+=)', text, re.S))
            self.assertEqual(found, kinds, "%s mark kinds changed" % name)
            for k in kinds:
                self.assertIn("`%%%s" % k, doc)


class SentinelDrift(unittest.TestCase):
    def test_catalog_matches_header(self):
        text = _src("stats_sentinel.h")
        entries = re.findall(r'X\((\d+),\s*([A-Z_]+),\s*"([^"]*)"\)', text)
        version = int(re.search(r"#define\s+STATS_SENTINEL_CATALOG_VERSION\s+(\d+)", text).group(1))
        self.assertEqual(version, wire.SENTINEL_CATALOG_VERSION)
        self.assertEqual(
            [(int(n), s, d) for n, s, d in entries],
            [(c.code, c.slug, c.description) for c in wire.SENTINELS.values()],
        )

    def test_bridgeable_matches_validators_and_doc(self):
        from lib.sentinel_catalog import BRIDGEABLE_SLUGS

        ours = {c.slug for c in wire.SENTINELS.values() if c.bridgeable}
        self.assertEqual(ours, set(BRIDGEABLE_SLUGS))
        doc = _doc()
        for c in wire.SENTINELS.values():
            row = re.search(r"\| `-%d` \| `%s` \|[^\n]*\| (bridgeable|breaks) \|" % (c.code, c.slug), doc)
            self.assertIsNotNone(row, "sentinel -%d missing from RAW_DATA_FORMAT.md" % c.code)
            self.assertEqual(row.group(1) == "bridgeable", c.bridgeable)


class KeysCatalogDrift(unittest.TestCase):
    def test_static_catalog_matches_live_daemon_schema(self):
        """The source-derived catalog equals what a real daemon printed (x86_64 dev host)."""
        catalog = load_catalog(SRC)
        live = hr.read_file(HERE / "fixtures" / "live_schema_x86_64.txt")
        header = hr.iter_records((HERE / "fixtures" / "live_schema_x86_64.txt").read_text().splitlines())
        schemas = next(header).schemas
        self.assertFalse(live)
        self.assertGreaterEqual(len(schemas), 15)
        for typ, ts in schemas.items():
            self.assertIn(typ, catalog)
            self.assertEqual(ts.keys, list(catalog[typ]["keys"]), typ)
            for e in ts.entries:
                k = catalog[typ]["keys"][e.key]
                ref = hr.parse_schema_entry(",".join([e.key, k["options"]]))
                self.assertEqual((ref.is_event, ref.unit, ref.multiplier, ref.width),
                                 (e.is_event, e.unit, e.multiplier, e.width), (typ, e.key))
                self.assertEqual(k["slow"], e.slow, (typ, e.key))

    def test_generator_flags_new_and_removed_keys(self):
        import gen_metrics_reference as gen

        catalog = load_catalog(SRC)
        desc = gen.load_descriptions(gen.TSV)
        self.assertEqual(gen.problems(catalog, desc), [])
        added = dict(catalog)
        added["host_ps"] = {"sources": ["ps.c"], "keys": dict(catalog["host_ps"]["keys"])}
        added["host_ps"]["keys"]["brand_new"] = {"options": "E", "description": "", "conditions": [], "slow": False}
        self.assertIn("missing description: host_ps brand_new", gen.problems(added, desc))
        stale = dict(desc)
        stale[("host_ps", "retired_key")] = "gone"
        stale[("host_ps", "ctxt")] = "café"
        errs = gen.problems(catalog, stale)
        self.assertIn("TSV describes a key the sources do not emit: host_ps retired_key", errs)
        self.assertIn("non-ASCII description: host_ps ctxt", errs)

    def test_metrics_reference_up_to_date(self):
        r = subprocess.run(
            [sys.executable, str(MONITOR / "scripts" / "gen_metrics_reference.py"), "--check"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
