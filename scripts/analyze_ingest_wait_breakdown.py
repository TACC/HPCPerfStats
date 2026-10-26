#!/usr/bin/env python3
"""
Split sync_timedb ingest wall time into CPU, run-queue wait and sleep.

Reads a soak child log (or any pipeline log) that was produced with
``sync_ingest_telemetry`` on, picks the ``ingest file`` outcome lines that
carry the wait-telemetry keys (``thr_cpu_s``, ``thr_runq_s``, ``thr_sleep_s``,
``wq_*``, ``w_*``, per-stage ``*_cpu_s``), and prints per size tier:

* the wall split ``cpu / runq / write_wait / other_sleep`` (shares sum to 1),
* CPU per MiB of raw input (the efficiency meter once threads are CPU bound),
* per-stage CPU-over-wall ratios (which stages are CPU bound vs waiting),
* write-queue residence, join wait and writer execution medians.

It also summarizes the 60 s ``queue_orchestrator census`` lines (PSI, runnable
threads, per-role core use, Python GC pauses, Boehm GC, write-queue depth).

Usage:
  python3 scripts/analyze_ingest_wait_breakdown.py --log-file CHILD.log [--json]

Attributes:
  TIERS: Size-tier names in report order.
  STAGE_PAIRS: Report name -> (wall key, cpu key) for stage CPU ratios.
  MEDIAN_KEYS: Outcome keys reported as per-tier medians.
  CENSUS_KEYS: Census keys printed in the markdown report.
  _KV_RE: Regex matching ``key=value`` tokens.
  _NUM_RE: Regex matching a plain or ``a/b`` number.
  _MIB: Bytes per MiB.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

TIERS: tuple[str, ...] = ("lt8", "8_64", "64_256", "ge256", "all")
STAGE_PAIRS: dict[str, tuple[str, str]] = {
  "feed": ("feed_s", "feed_cpu_s"),
  "build_df": ("build_df_s", "build_df_cpu_s"),
  "delta": ("delta_s", "delta_cpu_s"),
  "collapse": ("collapse_s", "collapse_cpu_s"),
  "arc": ("arc_s", "arc_cpu_s"),
}
MEDIAN_KEYS: tuple[str, ...] = (
  "elapsed_s",
  "parse_elapsed_s",
  "postgres_s",
  "stages_sum_s",
  "thr_cpu_s",
  "thr_runq_s",
  "thr_sleep_s",
  "wq_resid_s",
  "wq_join_s",
  "w_exec_s",
  "w_cpu_s",
  "w_runq_s",
  "slot_wait_s",
  "parse_unaccounted_s",
)
CENSUS_KEYS: tuple[str, ...] = (
  "psi_cpu10",
  "psi_mem10",
  "psi_io10",
  "sys_runnable",
  "cpu_ingest",
  "runq_ingest",
  "cpu_write",
  "runq_write",
  "cpu_native",
  "runq_native",
  "cpu_coord",
  "cpu_other",
  "pygc_n",
  "pygc_s",
  "pygc_max_ms",
  "boehm_gc_no",
  "boehm_gc_ms",
  "wq_depth",
  "w_busy",
  "parse_put_block_s",
  "codon_slot_occ",
)
_KV_RE = re.compile(r"(\w+)=(\S+)")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?$")
_MIB = 1024 * 1024


def parse_kv_line(line: str) -> dict[str, str]:
  """
  Extract every ``key=value`` token from one log line.

  Args:
    line (str): Raw log line.

  Returns:
    dict[str, str]: Token map; later duplicates win.

  Examples:
    >>> parse_kv_line("ingest file outcome=ingested elapsed_s=1.5")["elapsed_s"]
    '1.5'
  """
  return dict(_KV_RE.findall(line))


def tier_of(size_bytes: float) -> str:
  """
  Map a raw file size to a report tier.

  Args:
    size_bytes (float): Raw stats file size in bytes.

  Returns:
    str: ``lt8`` (<8 MiB), ``8_64``, ``64_256`` or ``ge256`` (>=256 MiB).

  Examples:
    >>> tier_of(20 * 1024 * 1024)
    '8_64'
  """
  mib = size_bytes / _MIB
  if mib < 8:
    return "lt8"
  if mib < 64:
    return "8_64"
  return "64_256" if mib < 256 else "ge256"


def record_from_kv(kv: dict[str, str]) -> dict[str, Any]:
  """
  Convert a token map into a record with numeric values as floats.

  Args:
    kv (dict[str, str]): Output of :func:`parse_kv_line`.

  Returns:
    dict[str, Any]: Same keys; values that parse as plain numbers become
      ``float``, everything else stays ``str``.

  Examples:
    >>> record_from_kv({"a": "1.5", "b": "x"})
    {'a': 1.5, 'b': 'x'}
  """
  out: dict[str, Any] = {}
  for key, val in kv.items():
    try:
      out[key] = float(val)
    except ValueError:
      out[key] = val
  return out


def load_records(path: Path) -> list[dict[str, Any]]:
  """
  Load telemetry-bearing, fully ingested ``ingest file`` records.

  Args:
    path (Path): Pipeline or soak child log.

  Returns:
    list[dict[str, Any]]: One record per line with ``outcome=ingested`` and
      the ``thr_cpu_s`` key (``db_skip`` and telemetry-off lines are skipped).

  Examples:
    >>> load_records(Path("/nonexistent")) if False else []
    []
  """
  out: list[dict[str, Any]] = []
  with path.open(encoding="utf-8", errors="replace") as fh:
    for line in fh:
      if "ingest file " not in line or "outcome=ingested" not in line:
        continue
      if "thr_cpu_s=" not in line:
        continue
      out.append(record_from_kv(parse_kv_line(line)))
  return out


def _sum(recs: list[dict[str, Any]], key: str) -> float:
  """
  Sum a numeric key over records, treating missing values as zero.

  Args:
    recs (list[dict[str, Any]]): Records from :func:`load_records`.
    key (str): Record key.

  Returns:
    float: Total.

  Examples:
    >>> _sum([{"a": 1.0}, {}], "a")
    1.0
  """
  return sum(float(r.get(key, 0.0) or 0.0) for r in recs)


def _median(recs: list[dict[str, Any]], key: str) -> float:
  """
  Median of a numeric key over records that carry it.

  Args:
    recs (list[dict[str, Any]]): Records from :func:`load_records`.
    key (str): Record key.

  Returns:
    float: Median, or ``0.0`` when no record has the key.

  Examples:
    >>> _median([{"a": 1.0}, {"a": 3.0}], "a")
    2.0
  """
  vals = [float(r[key]) for r in recs if isinstance(r.get(key), float)]
  return statistics.median(vals) if vals else 0.0


def _tier_summary(recs: list[dict[str, Any]]) -> dict[str, float]:
  """
  Build the metric map for one tier.

  Args:
    recs (list[dict[str, Any]]): Records of one size tier (non-empty).

  Returns:
    dict[str, float]: ``n``, wall shares, ``cpu_s_per_mib``, stage
      CPU-over-wall ratios and per-key medians.

  Examples:
    >>> _tier_summary([{"thr_cpu_s": 1.0, "thr_sleep_s": 1.0}])["n"]
    1.0
  """
  cpu, runq = _sum(recs, "thr_cpu_s"), _sum(recs, "thr_runq_s")
  sleep = _sum(recs, "thr_sleep_s")
  join = sum(
    min(
      float(r.get("wq_join_s", 0.0) or 0.0),
      float(r.get("thr_sleep_s", 0.0) or 0.0),
    )
    for r in recs
  )
  total = (cpu + runq + sleep) or 1.0
  mib = _sum(recs, "size_bytes") / _MIB
  out: dict[str, float] = {
    "n": float(len(recs)),
    "cpu_share": cpu / total,
    "runq_share": runq / total,
    "write_wait_share": join / total,
    "other_sleep_share": (sleep - join) / total,
    "cpu_s_per_mib": cpu / mib if mib else 0.0,
    "writer_cpu_s_per_mib": _sum(recs, "w_cpu_s") / mib if mib else 0.0,
  }
  for name, (wall_key, cpu_key) in STAGE_PAIRS.items():
    wall = _sum(recs, wall_key)
    out[f"{name}_cpu_over_wall"] = _sum(recs, cpu_key) / wall if wall else 0.0
    out[f"{name}_wall_share"] = wall / total
  for key in MEDIAN_KEYS:
    out[f"median_{key}"] = _median(recs, key)
  return out


def summarize(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
  """
  Summarize records per size tier plus an ``all`` roll-up.

  Args:
    records (list[dict[str, Any]]): Output of :func:`load_records`.

  Returns:
    dict[str, dict[str, float]]: Tier name -> metric map; tiers with no
      records are omitted.

  Examples:
    >>> summarize([])
    {}
  """
  if not records:
    return {}
  by_tier: dict[str, list[dict[str, Any]]] = {t: [] for t in TIERS}
  for rec in records:
    by_tier[tier_of(float(rec.get("size_bytes", 0.0) or 0.0))].append(rec)
    by_tier["all"].append(rec)
  return {t: _tier_summary(v) for t, v in by_tier.items() if v}


def census_summary(lines: list[str]) -> dict[str, dict[str, float]]:
  """
  Aggregate numeric tokens on ``queue_orchestrator census`` lines.

  Args:
    lines (list[str]): Log lines (others are ignored).

  Returns:
    dict[str, dict[str, float]]: Key -> ``mean`` / ``max`` / ``sum`` / ``n``.
      ``a/b`` tokens (``wq_depth``, ``w_busy``) use the first number.

  Examples:
    >>> census_summary(
    ...   ["queue_orchestrator census x=2", "queue_orchestrator census x=4"]
    ... )["x"]["mean"]
    3.0
  """
  series: dict[str, list[float]] = {}
  for line in lines:
    if "queue_orchestrator census" not in line:
      continue
    for key, val in parse_kv_line(line).items():
      if _NUM_RE.match(val):
        series.setdefault(key, []).append(float(val.split("/")[0]))
  return {
    k: {
      "mean": sum(v) / len(v),
      "max": max(v),
      "sum": sum(v),
      "n": float(len(v)),
    }
    for k, v in series.items()
  }


def render_markdown(
  summary: dict[str, dict[str, float]], census: dict[str, dict[str, float]]
) -> str:
  """
  Render the tier table and census digest as markdown.

  Args:
    summary (dict[str, dict[str, float]]): Output of :func:`summarize`.
    census (dict[str, dict[str, float]]): Output of :func:`census_summary`.

  Returns:
    str: Markdown report.

  Examples:
    >>> render_markdown({}, {}).startswith("# ")
    True
  """
  lines = ["# Ingest wait breakdown", ""]
  lines += [
    "Shares are of (thr_cpu + thr_runq + thr_sleep); write_wait is the part of "
    "sleep spent joining the write queue.",
    "",
    "| tier | n | cpu | runq | write_wait | other_sleep | cpu_s/MiB | "
    "med elapsed_s | med postgres_s | med wq_resid_s | med w_exec_s |",
    "|---|---|---|---|---|---|---|---|---|---|---|",
  ]
  for tier, s in summary.items():
    lines.append(
      f"| {tier} | {int(s['n'])} | {s['cpu_share']:.2f} | {s['runq_share']:.2f} | "
      f"{s['write_wait_share']:.2f} | {s['other_sleep_share']:.2f} | "
      f"{s['cpu_s_per_mib']:.2f} | {s['median_elapsed_s']:.0f} | "
      f"{s['median_postgres_s']:.0f} | {s['median_wq_resid_s']:.1f} | "
      f"{s['median_w_exec_s']:.0f} |"
    )
  lines += ["", "## Stage CPU over wall (1.0 = fully on CPU)", ""]
  lines += [
    "| tier | " + " | ".join(STAGE_PAIRS) + " |",
    "|---|" + "---|" * len(STAGE_PAIRS),
  ]
  for tier, s in summary.items():
    cells = " | ".join(f"{s[f'{n}_cpu_over_wall']:.2f}" for n in STAGE_PAIRS)
    lines.append(f"| {tier} | {cells} |")
  if census:
    lines += [
      "",
      "## Census (60 s lines)",
      "",
      "| key | mean | max |",
      "|---|---|---|",
    ]
    for key in CENSUS_KEYS:
      if key in census:
        lines.append(
          f"| {key} | {census[key]['mean']:.2f} | {census[key]['max']:.2f} |"
        )
  return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
  """
  CLI entry: print the breakdown for ``--log-file``.

  Args:
    argv (list[str] | None): Argument list; ``None`` reads ``sys.argv``.

  Returns:
    int: ``0`` on success, ``1`` when the log has no telemetry-bearing
      ``ingest file`` lines.

  Examples:
    >>> main(["--help"]) if False else 0
    0
  """
  ap = argparse.ArgumentParser(
    description="Ingest wall split: cpu / runq / write wait / sleep"
  )
  ap.add_argument(
    "--log-file", required=True, type=Path, help="pipeline or soak child log"
  )
  ap.add_argument(
    "--json", action="store_true", help="emit JSON instead of markdown"
  )
  args = ap.parse_args(argv)
  records = load_records(args.log_file)
  if not records:
    print(
      "no ingest file lines with wait telemetry (is sync_ingest_telemetry on?)",
      file=sys.stderr,
    )
    return 1
  with args.log_file.open(encoding="utf-8", errors="replace") as fh:
    census = census_summary(fh.readlines())
  summary = summarize(records)
  if args.json:
    print(json.dumps({"tiers": summary, "census": census}, indent=2))
  else:
    print(render_markdown(summary, census), end="")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
