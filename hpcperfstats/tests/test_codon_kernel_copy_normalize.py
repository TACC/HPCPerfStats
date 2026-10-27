"""Codon kernel COPY scalar normalization vs CPython insert helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from hpcperfstats.dbload.lib.date_utils import to_pydatetime_or_none
from hpcperfstats.dbload.lib.sync_timedb_host_data_insert import (
  HOST_DATA_COPY_COLUMNS,
  coerce_host_stats_to_columnar,
  normalize_host_columnar_for_copy,
)
from hpcperfstats.dbload.lib.sync_timedb_proc_data_insert import (
  normalize_proc_columnar_for_copy,
)

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[1] / "dbload" / "codon" / "feed_collapse.py"
)


def _load_feed_collapse():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_copy_norm_test", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _host_soa_from_finished(finished: tuple[Any, ...]) -> dict[str, list[Any]]:
  keys = (
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
  return dict(zip(keys, finished, strict=True))


def test_kernel_host_normalize_matches_insert_helper() -> None:
  mod = _load_feed_collapse()
  raw = (
    [1710000001.0, 1710000002.0],
    ["h", "h"],
    ["-", "9"],
    ["cpu", "cpu"],
    [float("nan"), ""],
    ["user", "user"],
    ["%", "%"],
    [1.0, float("nan")],
    [0.1, 0.2],
    [0.0, float("nan")],
  )
  collapsed_host, _ = mod.finalize_copy_unique_emit(raw, ([], [], [], [], []))
  normed_host, _proc = mod.normalize_copy_unique_scalars(
    collapsed_host, ([], [], [], [], [])
  )
  kernel_cols = _host_soa_from_finished(normed_host)
  ref = normalize_host_columnar_for_copy(
    coerce_host_stats_to_columnar(_host_soa_from_finished(collapsed_host))
  )
  for col in HOST_DATA_COPY_COLUMNS:
    if col == "time":
      assert [to_pydatetime_or_none(t) for t in kernel_cols["time"]] == ref[
        "time"
      ]
    else:
      assert kernel_cols[col] == ref[col]


def test_whole_node_cpu_sums_to_empty_dev_and_gpu_keeps_hardware_dev() -> None:
  """Whole-node cpu 0/1 sum to one '' row; GPU rows keep their hardware dev."""
  mod = _load_feed_collapse()
  lines = [
    "!cpu user,W=64,U=1",
    "!nvidia_gpu gpu_util,W=64,U=1",
    "1000.1 J9 h1",
    "cpu 0 @full 1.0",
    "cpu 1 @full 4.0",
    "nvidia_gpu 0 @full 5.0",
    "nvidia_gpu 1 @full 7.0",
  ]
  finished, _proc, *_ = mod.feed_delta_collapse_arc(lines, {}, {})
  cols = _host_soa_from_finished(finished)
  rows = list(zip(cols["type"], cols["dev"], cols["value"], strict=True))
  assert sorted(rows) == [
    ("cpu", "", 5.0),
    ("nvidia_gpu", "0", 5.0),
    ("nvidia_gpu", "1", 7.0),
  ]


def test_feed_delta_arc_host_matches_normalize_helper() -> None:
  mod = _load_feed_collapse()
  lines = [
    "!cpu user,W=64,U=1",
    "1710000001.0 9 hosta",
    "cpu 0 @full 1.0",
    "1710000002.0 - hosta",
    "cpu 0 @full 2.0",
  ]
  finished, proc, _, _, _ = mod.feed_delta_collapse_arc(lines, {}, {})
  finished, proc = mod.normalize_copy_unique_scalars(finished, proc)
  kernel_cols = _host_soa_from_finished(finished)
  ref = normalize_host_columnar_for_copy(
    coerce_host_stats_to_columnar(kernel_cols)
  )
  for col in HOST_DATA_COPY_COLUMNS:
    if col == "time":
      assert [to_pydatetime_or_none(t) for t in kernel_cols["time"]] == ref[
        "time"
      ]
    else:
      assert kernel_cols[col] == ref[col]
  if proc[0]:
    proc_cols: dict[str, list[Any]] = {
      "jid": list(proc[0]),
      "host": list(proc[1]),
      "proc": list(proc[2]),
      "device": [""] * len(proc[0]),
    }
    for metric in mod.HOST_PROC_KEYS:
      proc_cols[metric] = [
        row.get(metric) if metric in row else None for row in proc[3]
      ]
    assert normalize_proc_columnar_for_copy(proc_cols) == proc_cols
