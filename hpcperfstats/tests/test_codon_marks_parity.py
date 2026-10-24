"""Codon marks-in-feed parity vs IncrementalStatsParser + SoA COPY parity."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)


def _load_feed_collapse():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_marks_under_test", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _shared_mark_corpus() -> list[str]:
  return [
    "!cpu user,W=64,U=1",
    "%jobs jids=9,10",
    "%affinity jid=9 cpus=0-1 gpus=- cg=sched",
    "1710000001.0 9 hosta",
    "cpu 0 @full 1.0",
  ]


def test_codon_marks_affinity_and_jobs_bound() -> None:
  mod = _load_feed_collapse()
  _cols, _proc, _cr, _ca, marks = mod.feed_delta_collapse_arc(
    _shared_mark_corpus(), {}, {}
  )
  kinds = {m["kind"] for m in marks}
  assert "jobs" in kinds
  assert "affinity" in kinds
  aff = next(m for m in marks if m["kind"] == "affinity")
  assert aff["jid"] == "9"
  assert aff["host"] == "hosta"
  assert float(aff["time"]) == 1710000001.0
  jobs = next(m for m in marks if m["kind"] == "jobs")
  assert "9" in jobs["jids"] and "10" in jobs["jids"]


def test_codon_marks_match_incremental_ready() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import IncrementalStatsParser

  mod = _load_feed_collapse()
  lines = _shared_mark_corpus()
  _c, _p, _cr, _ca, codon_marks = mod.feed_delta_collapse_arc(lines, {}, {})
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  ready = parser.take_ready_marks()
  aff_c = next(m for m in codon_marks if m["kind"] == "affinity")
  aff_p = next(m for m in ready if m.get("kind") == "affinity")
  assert aff_c["jid"] == aff_p["jid"]
  assert aff_c["host"] == aff_p["host"]
  assert float(aff_c["time"]) == float(aff_p["time"])


def test_host_soa_copy_bytes_match_dataframe() -> None:
  from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as hdi

  t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
  frame = pd.DataFrame(
    [
      {
        "time": t,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 1.0,
        "delta": 0.5,
        "arc": 0.25,
      },
      {
        "time": t,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "system",
        "unit": "%",
        "value": 2.0,
        "delta": 1.0,
        "arc": 0.5,
      },
    ]
  )
  soa = hdi.coerce_host_stats_to_columnar(frame)
  assert hdi.host_rows_to_copy_bytes(soa) == hdi.host_rows_to_copy_bytes(frame)
  assert hdi.host_rows_to_copy_bytes(soa) == hdi._host_columns_to_copy_bytes(
    soa
  )


_HOST_SOA_KEYS = (
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
  "value",
  "delta",
  "arc",
)


def _host_soa_from_feed_finished(
  finished: tuple[Any, ...],
) -> dict[str, list[Any]]:
  return dict(zip(_HOST_SOA_KEYS, finished, strict=True))


def test_codon_copy_unique_emit_matches_host_insert_collapse() -> None:
  """Codon finalize_copy_unique_emit matches host INSERT conflict collapse."""
  from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as hdi

  mod = _load_feed_collapse()
  finished = (
    [1.0, 1.0],
    ["h", "h"],
    ["1", "2"],
    ["cpu", "cpu"],
    ["", ""],
    ["user", "user"],
    ["%", "%"],
    [1.0, 2.0],
    [0.1, 0.2],
    [0.0, 0.0],
  )
  codon_host, _proc = mod.finalize_copy_unique_emit(
    finished,
    ([], [], [], [], []),
  )
  codon_cols = dict(
    zip(
      (
        "time",
        "host",
        "jid",
        "type",
        "dev",
        "event",
        "unit",
        "value",
        "delta",
        "arc",
      ),
      codon_host,
      strict=True,
    )
  )
  ref = hdi._collapse_host_columnar_conflict_keys(
    dict(
      zip(
        (
          "time",
          "host",
          "jid",
          "type",
          "dev",
          "event",
          "unit",
          "value",
          "delta",
          "arc",
        ),
        finished,
        strict=True,
      )
    )
  )
  assert codon_cols["time"] == ref["time"]
  assert codon_cols["value"] == ref["value"]


def test_codon_file_soa_matches_incremental_copy() -> None:
  """feed_collapse finished columns COPY-encode like an equivalent host DataFrame."""
  from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as hdi

  mod = _load_feed_collapse()
  lines = [
    *_shared_mark_corpus(),
    "1710000002.0 9 hosta",
    "cpu 0 @full 2.0",
  ]
  finished, _, _, _, _ = mod.feed_delta_collapse_arc(lines, {}, {})
  host_cols = _host_soa_from_feed_finished(finished)
  frame = pd.DataFrame(host_cols)
  assert hdi.host_rows_to_copy_bytes(frame) == hdi.host_rows_to_copy_bytes(
    host_cols, copy_unique=False
  )
  assert hdi.host_rows_to_copy_bytes(
    host_cols
  ) == hdi._host_columns_to_copy_bytes(host_cols)


def test_normalize_codon_marks_bridge() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
    _normalize_codon_marks,
  )

  out = _normalize_codon_marks(
    [
      {
        "kind": "affinity",
        "jid": "9",
        "jids": "",
        "cpus": "0-1",
        "gpus": "-",
        "cg": "sched",
        "time": "1710000001.0",
        "host": "hosta",
      },
      {
        "kind": "jobs",
        "jid": "",
        "jids": "9,10",
        "cpus": "",
        "gpus": "",
        "cg": "",
        "time": "1710000001.0",
        "host": "hosta",
      },
    ]
  )
  assert len(out) == 2
  aff = next(m for m in out if m["kind"] == "affinity")
  assert aff["time"] == 1710000001.0
  jobs = next(m for m in out if m["kind"] == "jobs")
  assert jobs["jids"] == ["9", "10"]
