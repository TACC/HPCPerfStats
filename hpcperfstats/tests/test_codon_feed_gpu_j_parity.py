"""GPU keep-dev and j-stamp / host_mem_job parity vs IncrementalStatsParser."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from hpcperfstats.dbload.lib.sync_timedb_parsing import IncrementalStatsParser

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)


def _load_feed_collapse():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_gpu_j", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _corpus_gpu_and_j() -> list[str]:
  return [
    "!cpu user,W=64,U=1",
    "!nvidia_gpu sm,W=64,U=1",
    "!host_mem_job rss,W=64,U=1",
    "%jobs jids=42",
    "1700000001.0 42 hostA",
    "cpu 0 @full 1.0",
    "nvidia_gpu 0 @full 2.0",
    "host_mem_job 42 @full 3.0",
  ]


def test_shared_row_dev_helpers_match_contract() -> None:
  mod = _load_feed_collapse()
  assert mod._shared_row_dev("0", "nvidia_gpu", "42", True) == "0"
  assert mod._shared_row_dev("0", "cpu", "42", True) == "j:42"
  assert mod._shared_row_dev("62909", "host_mem_job", "62909", False) == (
    "j:62909"
  )


def test_feed_lines_full_gpu_keep_dev_and_j_stamp() -> None:
  mod = _load_feed_collapse()
  lines = _corpus_gpu_and_j()
  (
    _t,
    _h,
    _j,
    types,
    devs,
    events,
    values,
    _w,
    _m,
    _u,
    _pj,
    _ph,
    _pp,
    _pf,
    _pd,
    _marks,
  ) = mod.feed_lines_full(lines, 0)

  by_key = {
    (types[i], events[i]): (devs[i], values[i]) for i in range(len(types))
  }
  assert by_key[("nvidia_gpu", "sm")][0] == "0"
  assert by_key[("cpu", "user")][0] == "j:42"
  assert by_key[("host_mem_job", "rss")][0] == "j:42"

  p = IncrementalStatsParser(0)
  p.feed_lines(list(lines))
  stats, _procs = p.finish()
  oracle = {(r["type"], r["event"]): r["dev"] for r in stats}
  assert oracle[("nvidia_gpu", "sm")] == "0"
  assert oracle[("cpu", "user")] == "j:42"
  assert oracle[("host_mem_job", "rss")] == "j:42"
