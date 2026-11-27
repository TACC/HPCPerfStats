#!/usr/bin/env python3
"""
Generate docs/reference/monitor-variables-consumer-map.md.

The page maps every monitor key to the consumer code that reads it. Keys come
from the monitor C sources through ``monitor/scripts/lib/keys_catalog.py``
(the same parser ``gen_metrics_reference.py`` uses), sentinel handling from
``hpcperfstats/lib/monitor_sentinel.py``, legacy type names from the packaged
rename map, and the ingest skip list from ``sync_timedb_parsing.py``. Per-key
meanings stay in ``monitor/docs/METRICS_REFERENCE.md``; this page links there.

Run from the checkout root::

  ../.venv/bin/python3 scripts/docs/gen_monitor_variables_consumer_map.py
  ../.venv/bin/python3 scripts/docs/gen_monitor_variables_consumer_map.py \\
    --check

The ``monitor-variables-consumer-map`` pre-commit hook runs ``--fix`` when a
commit touches an input: this script, the page, ``monitor/src/*.[ch]``,
``keys_catalog.py``, the rename map and ``load_map.py``, or a scanned file
under ``SCAN_ROOTS`` (which holds ``monitor_sentinel.py`` and
``sync_timedb_parsing.py``). A stale page is rewritten and the hook fails.
Run this script in the checkout, then commit the page with the other paths:
``git isolated-commit`` runs hooks in a slot worktree, so the rewrite never
reaches the checkout, and a named page that still matches ``HEAD`` is not part
of the commit.
``hpcperfstats/tests/test_doc_generators_fresh.py`` runs ``--check`` for
commits that skip hooks, and pins the hook's ``files:`` pattern to these
inputs.

Attributes:
  REPO (Path): Checkout root.
  OUT (Path): Generated page.
  SCRIPT (str): This script, relative to ``REPO`` (cited in the page).
  MR_LINK (str): Relative link from ``OUT`` to METRICS_REFERENCE.md.
  RAW_LINK (str): Relative link from ``OUT`` to RAW_DATA_FORMAT.md.
  SCAN_ROOTS (tuple[str, ...]): Directories scanned for key references.
  SCAN_SUFFIXES (tuple[str, ...]): File suffixes scanned.
  SCAN_SKIP (frozenset[str]): Generated or key-listing files left out.
  DIAGNOSTIC_NOTES (dict[str, str]): Diagnostic note per monitor type.
  HISTORICAL_KEYS (tuple[tuple[str, str, str], ...]): Keys only older
    archives carry: (key, legacy type, consumer handling or "").
  INGEST_PATH (tuple[str, ...]): Ingest data path steps.
  CONSUMER_STAGES (tuple[tuple[str, str], ...]): Consumer stage and the
    modules (relative to ``hpcperfstats/``) that read ``host_data`` there.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "monitor" / "scripts")]

from lib.keys_catalog import load_catalog  # noqa: E402

from hpcperfstats.dbload.lib.monitor_naming.load_map import (  # noqa: E402
  event_renames,
  type_renames,
)
from hpcperfstats.lib import monitor_sentinel as ms  # noqa: E402

OUT = REPO / "docs" / "reference" / "monitor-variables-consumer-map.md"
SCRIPT = "scripts/docs/gen_monitor_variables_consumer_map.py"
MR_LINK = "../../monitor/docs/METRICS_REFERENCE.md"
RAW_LINK = "../../monitor/docs/RAW_DATA_FORMAT.md"
SCAN_ROOTS = (
  "hpcperfstats/analysis",
  "hpcperfstats/dbload",
  "hpcperfstats/lib",
  "hpcperfstats/site/lib/machine",
  "hpcperfstats/site/frontend/src",
)
SCAN_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")
SCAN_SKIP = frozenset(
  {
    "hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEvents.ts",
    "hpcperfstats/site/frontend/src/utils/"
    "generate-variable-metadata-monitor-events.py",
  }
)

_GPU_NOTE = (
  "Cumulative and model counters show kernel efficiency and memory traffic;"
  " `clocks_event_reasons` bitmasks flag thermal or power throttling."
  " Compare link-byte counters with HBM bandwidth estimates for PCIe-bound"
  " jobs."
)
_LUSTRE_RPC_NOTE = (
  "Metadata and lock counters spike during small-file churn, lock contention"
  " or aggressive stat and cache behavior in one job step."
)
DIAGNOSTIC_NOTES = {
  "amd_gpu": _GPU_NOTE,
  "host_block": (
    "Compare the read and write mix and IOPS with `io_ticks` and queue time"
    " to separate throughput limits from latency or scheduler backlog;"
    " rising `in_flight` often precedes single-device saturation."
  ),
  "host_cpu": (
    "Split non-user time into disk wait (`iowait`), interrupt storms (`irq`,"
    " `softirq`) and low-priority work (`nice`)."
  ),
  "host_ib": (
    "Compare xmit and rcv data and packets for asymmetric communication;"
    " error, discard, `link_downed` and `symbol_error` counters flag link,"
    " credit or cable problems; match link transitions with job start, end"
    " or switch maintenance."
  ),
  "host_lnet": (
    "Message and drop counters isolate router or NIC issues on Lustre"
    " networks; match drops with I/O phases and remote mount health."
  ),
  "host_net": (
    "Errors or drops with flat goodput point to driver, cable or switch"
    " issues; CRC, FIFO and frame errors often mean bad optics or a duplex"
    " or speed mismatch; packet rate against byte rate separates"
    " small-message storms from bulk transfers."
  ),
  "host_nfs": (
    "Mountstats delay and RPC timing fields show client-side NFS latency;"
    " compare timeouts and queue times with server or network events, and"
    " read against write to separate metadata from data-path problems."
  ),
  "host_numa": (
    "Rising `numa_miss` or `other_node` in compute-bound jobs points to"
    " binding or first-touch placement to tune."
  ),
  "host_opa": (
    "Compare xmit and rcv data and packets for asymmetric communication;"
    " error and discard counters flag link, credit or congestion problems on"
    " the HFI."
  ),
  "host_power_socket": (
    "Compare the core rail (`cpu_power_w`) and SoC rail (`sysio_power_w`)"
    " with `socket_power_w` to split socket power into cores and memory"
    " plus I/O; sum by `dev` for a node total."
  ),
  "host_vm": (
    "Rising `allocstall` or kswapd activity with flat RSS means overcommit or"
    " fragmentation; high `thp_fault_fallback` or `thp_split` in"
    " memory-bound jobs means fragmentation or conflicting `madvise`;"
    " compare with `pgmajfault` and `host_proc` RSS."
  ),
  "intel_gpu": _GPU_NOTE,
  "lustre_llite": (
    "High `vfs_getxattr_ops` or `vfs_inode_permission_ops` rates come with"
    " metadata-heavy tools or security modules scanning many files."
  ),
  "lustre_mdc": _LUSTRE_RPC_NOTE,
  "lustre_osc": _LUSTRE_RPC_NOTE,
  "nvidia_gpu": _GPU_NOTE,
}

_MBW = (
  "Fallback after the DF beat counters in"
  " `resolve.amd_df_bw_event_conv_tries`, at 2 bytes per count"
)
_SSE = "Legacy FLOP proxy, weighted by `canonical.INTEL_LEGACY_SSE_FLOP_EVENTS`"
HISTORICAL_KEYS = (
  *((f"MBW_CHANNEL_{i}", "amd64_df", _MBW) for i in range(8)),
  ("READ_ops", "nfs", "NFS read operations in `metrics.py`"),
  *((f"READ_{f}", "nfs", "") for f in ("queue", "rtt", "timeouts")),
  ("WRITE_ops", "nfs", "NFS write operations in `metrics.py`"),
  *((f"WRITE_{f}", "nfs", "") for f in ("queue", "rtt", "timeouts")),
  ("SIMD_DOUBLE_256", "intel_8pmc3", _SSE),
  ("SSE_DOUBLE_PACKED", "intel_8pmc3", _SSE),
  ("SSE_DOUBLE_SCALAR", "intel_8pmc3", _SSE),
)

INGEST_PATH = (
  "The monitor prints sample headers (`<TIME> <JOBID> <HOST>`), data rows"
  " (`<TYPE> <DEV> <VALUES>`) and `!<TYPE>` schema lines"
  f" ([raw data format]({RAW_LINK})).",
  "`hpcperfstats/listend.py` (`on_message`) appends each RabbitMQ payload to"
  " the per-host archive directory.",
  "`hpcperfstats/dbload/sync_timedb.py` (`add_stats_file_to_db`) reads the"
  " archive files.",
  "`hpcperfstats/dbload/lib/sync_timedb_parsing.py` (`parse_stats_lines`,"
  " `compute_deltas_and_arc`, `EVENTMAPS_BY_TYPE`) parses the lines, maps"
  " legacy `CTL*` and `CTR*` schema columns to logical event names, collapses"
  " multi-device rows and computes `delta` and `arc`.",
  "`hpcperfstats/dbload/lib/io_helpers.py`"
  " (`host_data_instance_from_stats_row`) builds the ORM rows.",
  "`hpcperfstats/site/lib/machine/models.py` (`host_data`) stores `time`,"
  " `host`, `type`, `dev`, `event`, `unit`, `value`, `delta` and `arc`.",
  "Analysis (`jid_table`, `metrics`, `plot/*`) and the API read `host_data`"
  " by job window and schema.",
)

CONSUMER_STAGES = (
  ("Job window and schema", "`analysis/metrics/lib/gen/jid_table.py`"),
  (
    "Job metrics",
    "`analysis/metrics/lib/metrics.py` and `analysis/metrics/lib/columnar/`,"
    " stored in `metrics_data`",
  ),
  (
    "Summary plots",
    "`analysis/metrics/lib/plot/summaryplot.py`,"
    " `analysis/metrics/lib/plot/summary_metric_descriptions.py`",
  ),
  (
    "Roofline",
    "`analysis/metrics/lib/plot/roofline.py`,"
    " `analysis/metrics/lib/plot/roofline_peaks.py`",
  ),
  ("Node power estimate", "`analysis/metrics/lib/gen/node_power_est.py`"),
  ("API and Type Detail", "`site/lib/machine/api.py`"),
  (
    "UI tooltips",
    "`site/frontend/src/utils/variableMetadata.ts`"
    " (`getDescriptionForVariable`), `variableMetadataMonitorEvents.ts`",
  ),
)


def _md_cell(paths: list[str]) -> str:
  """
  Format repository paths for one table cell.

  Args:
    paths (list[str]): Checkout-relative paths, already sorted.

  Returns:
    str: Code-spanned paths relative to ``hpcperfstats/``, joined by ``;``,
    or the text ``"None"`` when ``paths`` is empty.

  Examples:
    >>> _md_cell(["hpcperfstats/lib/a.py", "hpcperfstats/b.py"])
    '`lib/a.py`; `b.py`'
    >>> _md_cell([])
    'None'
  """
  if not paths:
    return "None"
  return "; ".join(f"`{p.removeprefix('hpcperfstats/')}`" for p in paths)


def _is_test_path(path: str) -> bool:
  """
  Report whether a scanned file is test code.

  Args:
    path (str): Checkout-relative path.

  Returns:
    bool: True for files under a ``tests``/``test`` directory, ``test_*``
    modules and ``*.test.*`` frontend specs.

  Examples:
    >>> _is_test_path("hpcperfstats/analysis/metrics/tests/test_x.py")
    True
    >>> _is_test_path("hpcperfstats/analysis/metrics/lib/plot/test_y.py")
    True
    >>> _is_test_path("hpcperfstats/site/lib/machine/api.py")
    False
  """
  name = path.rsplit("/", 1)[-1]
  parts = path.split("/")
  return (
    "tests" in parts
    or "test" in parts
    or name.startswith("test_")
    or ".test." in name
  )


def _quoted_words(text: str) -> frozenset[str]:
  """
  Collect every identifier that appears as a quoted literal in ``text``.

  Args:
    text (str): Source file text.

  Returns:
    frozenset[str]: Names written as ``"name"`` or ``'name'``.

  Examples:
    >>> sorted(_quoted_words("x = 'idle'; idle_s = \\"user\\""))
    ['idle', 'user']
  """
  return frozenset(re.findall(r"""["'](\w+)["']""", text))


def load_monitor_catalog() -> dict[str, dict]:
  """
  Load the emitted (type, key) catalog from ``monitor/src``.

  Returns:
    dict[str, dict]: ``keys_catalog.load_catalog`` output: type name ->
    ``{"sources": [...], "keys": {key: {...}}}``, sorted by type.

  Examples:
    >>> "host_cpu" in load_monitor_catalog()  # doctest: +SKIP
    True
  """
  return load_catalog(REPO / "monitor" / "src")


def ingest_skipped_types() -> frozenset[str]:
  """
  Read the ingest skip list from ``sync_timedb_parsing.py`` without importing.

  Returns:
    frozenset[str]: Type names in the module-level ``exclude_types`` set.

  Raises:
    RuntimeError: When the ``exclude_types`` assignment is missing.

  Examples:
    >>> "host_ps" in ingest_skipped_types()
    True
  """
  path = REPO / "hpcperfstats" / "dbload" / "lib" / "sync_timedb_parsing.py"
  for node in ast.parse(path.read_text(encoding="utf-8")).body:
    if (
      isinstance(node, ast.Assign)
      and any(
        isinstance(t, ast.Name) and t.id == "exclude_types"
        for t in node.targets
      )
      and isinstance(node.value, ast.Call)
    ):
      return frozenset(ast.literal_eval(node.value.args[0]))
  raise RuntimeError(f"exclude_types not found in {path}")


def legacy_type_names() -> dict[str, set[str]]:
  """
  Map each canonical type to its legacy names from the packaged rename map.

  Returns:
    dict[str, set[str]]: Canonical ``st_name`` -> legacy names.

  Examples:
    >>> "cpu" in legacy_type_names()["host_cpu"]
    True
  """
  out: dict[str, set[str]] = defaultdict(set)
  for old, new in type_renames().items():
    if old != new:
      out[new].add(old)
  return out


def scanned_files() -> dict[str, str]:
  """
  Read every tracked source file under ``SCAN_ROOTS``.

  Returns:
    dict[str, str]: Checkout-relative path -> file text.

  Examples:
    >>> "hpcperfstats/listend.py" in scanned_files()
    False
  """
  listed = subprocess.run(
    ["git", "ls-files", "--", *SCAN_ROOTS],
    cwd=REPO,
    capture_output=True,
    text=True,
    check=True,
  ).stdout.split()
  return {
    rel: (REPO / rel).read_text(encoding="utf-8", errors="replace")
    for rel in sorted(listed)
    if rel.endswith(SCAN_SUFFIXES)
    and rel not in SCAN_SKIP
    and (REPO / rel).is_file()
  }


def key_references(
  key_types: dict[str, set[str]],
  legacy: dict[str, set[str]],
  files: dict[str, str],
) -> dict[str, tuple[list[str], list[str]]]:
  """
  Find the files that read each key.

  A file counts when it quotes the key and also quotes one of the key's type
  names (canonical or legacy) or mentions ``monitor_naming``. The type gate
  drops generic words (``active``, ``errors``) used for other purposes.

  Args:
    key_types (dict[str, set[str]]): Key -> type names that emit it.
    legacy (dict[str, set[str]]): Canonical type -> legacy names.
    files (dict[str, str]): Path -> text, from ``scanned_files``.

  Returns:
    dict[str, tuple[list[str], list[str]]]: Key -> (application paths, test
    paths), both sorted; keys without references are left out.

  Examples:
    >>> key_references(
    ...   {"idle": {"host_cpu"}}, {}, {"a.py": "'idle' 'host_cpu'"}
    ... )
    {'idle': (['a.py'], [])}
  """
  quoted = {
    p: (_quoted_words(text), "monitor_naming" in text)
    for p, text in files.items()
  }
  out: dict[str, tuple[list[str], list[str]]] = {}
  for key in sorted(key_types):
    names = set()
    for typ in key_types[key]:
      names |= {typ} | legacy.get(typ, set())
    hits = [
      p
      for p, (words, naming) in quoted.items()
      if key in words and (naming or not names.isdisjoint(words))
    ]
    if hits:
      out[key] = (
        [p for p in hits if not _is_test_path(p)],
        [p for p in hits if _is_test_path(p)],
      )
  return out


def sentinel_rows() -> list[str]:
  """
  Render the consumer sentinel table from ``monitor_sentinel.CATALOG``.

  Returns:
    list[str]: Markdown lines: header, one row per code, then the unknown row.

  Examples:
    >>> sentinel_rows()[2].startswith("| -1 | `NOT_UPDATED` |")
    True
  """
  rows = [
    "| Code | Slug | Gap handling | Kind |",
    "| --- | --- | --- | --- |",
  ]
  entries = [ms.CATALOG[c] for c in sorted(ms.CATALOG, reverse=True)]
  entries.append(ms.lookup(-(len(ms.CATALOG) + 1)))
  for e in entries:
    code = str(e.code) if e.slug != "UNKNOWN" else "other below 0"
    gap = "bridgeable" if e.bridge else "chain-breaking"
    rows.append(f"| {code} | `{e.slug}` | {gap} | {e.kind} |")
  return rows


def render() -> str:
  """
  Build the full page text.

  Returns:
    str: Markdown for ``OUT``, ending in a newline.

  Examples:
    >>> render().startswith("# Monitor variables consumer map")
    True
  """
  catalog = load_monitor_catalog()
  skipped = ingest_skipped_types()
  legacy = legacy_type_names()
  key_types: dict[str, set[str]] = defaultdict(set)
  for typ, info in catalog.items():
    for key in info["keys"]:
      key_types[key].add(typ)
  hist = {k: t for k, t, _note in HISTORICAL_KEYS}
  for key, typ in hist.items():
    key_types[key].add(typ)
  refs = key_references(key_types, legacy, scanned_files())
  renames = event_renames()
  pairs = sum(len(v["keys"]) for v in catalog.values())
  unbridged = ", ".join(
    f"`{t}`" for t in sorted(ms._LIKWID_UNPROVEN_BRIDGE_TYPES)
  )
  visible = ", ".join(
    f"`{ms.CATALOG[c].slug}`" for c in sorted(ms.RESEARCHER_VISIBLE_CODES)
  )

  lines = [
    "# Monitor variables consumer map",
    "",
    f"Generated by `{SCRIPT}`; do not edit.",
    "",
    "This page maps each monitor key to the consumer code that reads it, for"
    " developers who change monitor output or the analysis of `host_data`.",
    f"Per-key meaning, unit, width and tier are in [Monitor metrics"
    f" reference]({MR_LINK}); this page adds the consumer side.",
    f"The monitor emits {len(catalog)} types and {pairs} type and key pairs"
    f" ({len(key_types) - len(hist)} distinct keys).",
    "",
    "## Ingest data path",
    "",
    "A monitor sample reaches `host_data` in these steps:",
    "",
  ]
  lines += [f"{i}. {step}" for i, step in enumerate(INGEST_PATH, 1)]
  lines += [
    "",
    "The consumer stages read `host_data` in these modules (paths relative"
    " to `hpcperfstats/`):",
    "",
    "| Stage | Modules |",
    "| --- | --- |",
  ]
  lines += [f"| {stage} | {mods} |" for stage, mods in CONSUMER_STAGES]
  lines += [
    "",
    "## Types",
    "",
    "Each type links to its key table in the metrics reference.",
    "Ingest skips the types marked Skipped, so they never reach `host_data`.",
    "",
    "| Type | Monitor source | Ingest | Diagnostic note |",
    "| --- | --- | --- | --- |",
  ]
  for typ, info in catalog.items():
    srcs = ", ".join(f"`monitor/src/{s}`" for s in info["sources"])
    ingest = "Skipped" if typ in skipped else "Stored"
    note = "None" if typ in skipped else DIAGNOSTIC_NOTES.get(typ, "None")
    lines.append(f"| [{typ}]({MR_LINK}#{typ}) | {srcs} | {ingest} | {note} |")
  lines += [
    "",
    "Archives written before the type renames carry the legacy names in"
    " [Monitor naming tables](monitor-naming-tables.md); ingest maps them"
    " to these names, and `sync_timedb_parsing_legacy.py` decodes legacy"
    " `CTL*` and `CTR*` schemas.",
    "",
    "## Value sentinels",
    "",
    "The wire codes are in [raw data format]"
    f"({RAW_LINK}#sentinels-values-the-monitor-could-not-read);"
    " the consumer catalog is `hpcperfstats/lib/monitor_sentinel.py`.",
    "Ingest stores the code in `host_data.value` with `delta` and `arc` NULL,"
    " and every reader excludes `value < 0`.",
    "",
  ]
  lines += sentinel_rows()
  lines += [
    "",
    "Bridgeable codes mean the source counter kept running, so the next"
    " valid sample's `delta` spans the gap; chain-breaking codes start a new"
    " chain.",
    "Static codes mark a counter absent on that host; transient codes are"
    " gaps.",
    f"On {unbridged}, rows from a `$build` slug with `likwid` and no"
    " `x86perf` (or with no `$build` line) never bridge"
    " (`monitor_sentinel.bridge_proven`).",
    f"Researchers see only {visible} as a no-data reason on Job Detail;"
    " staff views show every code.",
    "",
    "## Code cross-references",
    "",
    "A file counts as reading a key when it quotes the key and also quotes"
    " one of the key's type names (canonical or legacy) or names"
    " `monitor_naming`.",
    "Scanned directories: " + ", ".join(f"`{r}`" for r in SCAN_ROOTS) + ".",
    "Paths are relative to `hpcperfstats/`.",
    "",
    "| Key | Types | Application code | Tests |",
    "| --- | --- | --- | --- |",
  ]
  for key, (app, tests) in refs.items():
    types = ", ".join(f"`{t}`" for t in sorted(key_types[key]))
    lines.append(f"| `{key}` | {types} | {_md_cell(app)} | {_md_cell(tests)} |")
  lines += [
    "",
    "### Keys without a consumer reference",
    "",
    "These keys reach `host_data` (or are skipped with their type) but no"
    " scanned file reads them; Type Detail and ad hoc queries still show"
    " them.",
    "",
  ]
  for typ, info in catalog.items():
    unread = [k for k in info["keys"] if k not in refs]
    if unread:
      keys = ", ".join(f"`{k}`" for k in unread)
      lines.append(f"- `{typ}`: {keys}")
  lines += [
    "",
    "## Historical keys",
    "",
    "Only older archives carry these keys; the current monitor never emits"
    " them, and the consumer still reads them for those jobs.",
    "",
    "| Key | Legacy type | Rename map target | Consumer handling |",
    "| --- | --- | --- | --- |",
  ]
  for key, typ, note in HISTORICAL_KEYS:
    target = f"`{renames[key]}`" if key in renames else "None"
    lines.append(f"| `{key}` | `{typ}` | {target} | {note or 'None'} |")
  return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
  """
  Write the page, or check it with ``--check`` or refresh it with ``--fix``.

  ``--fix`` is the pre-commit hook mode: it rewrites a stale page and fails
  with the command to run and the path to commit, and passes without writing
  when the page is current.

  Args:
    argv (list[str] | None): Command-line arguments (default ``sys.argv``).

  Returns:
    int: 0 on success or when up to date, 1 when ``--check`` finds drift
    or ``--fix`` rewrote the page.

  Examples:
    >>> main(["--check"])  # doctest: +SKIP
    0
  """
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  mode = ap.add_mutually_exclusive_group()
  mode.add_argument(
    "--check", action="store_true", help="fail if the page is stale"
  )
  mode.add_argument(
    "--fix",
    action="store_true",
    help="rewrite a stale page and fail (pre-commit hook mode)",
  )
  args = ap.parse_args(argv)
  text = render()
  rel = OUT.relative_to(REPO)
  current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
  if (args.check or args.fix) and current == text:
    print(f"{rel} up to date")
    return 0
  if args.check:
    print(f"{rel} is stale; run {SCRIPT}")
    return 1
  OUT.write_text(text, encoding="utf-8")
  if args.fix:
    print(
      f"{rel} was stale: run ../.venv/bin/python3 {SCRIPT} in the"
      f" checkout, then commit {rel} with the other paths"
    )
    return 1
  print(f"Wrote {rel}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
