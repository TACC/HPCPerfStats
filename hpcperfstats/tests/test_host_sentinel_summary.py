"""host_sentinel_summary run building (both engines, no database).

Runs are built per flush on the collapsed series and ride the side-mark
channel as ``kind=sentinel``; the DB merge lives in
``site/lib/machine/tests/test_sentinel_run_persist.py``.
"""

from __future__ import annotations

import pytest

from hpcperfstats.tests.test_codon_feed_delta_collapse_arc import (
  _SENTINEL_HDR,
  _load_feed_collapse,
  _norm_sentinel_marks,
)

_CPU = "!cpu user,E,W=64,U=cs"


def _flush_lines(rows, *, header=True) -> list[str]:
  """``rows`` = ``[(time, [dev0_token, dev1_token, ...]), ...]``."""
  lines = [_CPU, *(_SENTINEL_HDR if header else [])]
  for t, toks in rows:
    lines.append(f"{t} J1 hostA")
    lines += [f"cpu {d} {tok}" for d, tok in enumerate(toks)]
  return lines


def _runs_codon(flushes) -> list[tuple]:
  mod = _load_feed_collapse()
  carry_raw: dict = {}
  carry_arc: dict = {}
  marks: list = []
  for lines in flushes:
    _c, _p, carry_raw, carry_arc, m, _n = mod.feed_delta_collapse_arc(
      list(lines), carry_raw, carry_arc
    )
    marks += m
  return sorted(_norm_sentinel_marks(marks), key=lambda r: (r[4], r[5]))


def _runs_cpython(flushes, monkeypatch) -> list[tuple]:
  import pandas as pd

  from hpcperfstats.dbload.lib import (
    monitor_mark_side_ingest as mmsi,
    sync_timedb_parsing as stp,
  )

  seen: list = []
  monkeypatch.setattr(mmsi, "persist_monitor_marks", lambda m: seen.extend(m))
  carry = stp.DeltaCarryState()
  parser = stp.IncrementalStatsParser(0, delta_carry=carry)
  cols = ["time", "host", "jid", "type", "dev", "event", "value", "wid"]
  cols += ["mult", "unit"]
  for lines in flushes:
    parser.feed_lines(list(lines))
    df = pd.DataFrame(
      stp.stats_payload_to_records(parser.take_stats_columns()), columns=cols
    )
    stp.compute_deltas_and_arc_chunk(df, carry=carry)
  return sorted(_norm_sentinel_marks(seen), key=lambda r: (r[4], r[5]))


@pytest.fixture(params=["codon", "cpython"])
def runs(request, monkeypatch):
  if request.param == "codon":
    return _runs_codon
  return lambda flushes: _runs_cpython(flushes, monkeypatch)


def _shape(r) -> tuple:
  """(code, start, end, device_count, total_devices, prev)."""
  return (r[4], r[5], r[6], r[7], r[8], r[9])


def test_sentinel_summary_static_code_is_one_run(runs):
  rows = [(100 + 10 * i, ["-10", "5"]) for i in range(50)]
  (run,) = runs([_flush_lines(rows)])
  assert _shape(run) == (-10, 100.0, 590.0, 1, 2, None)


def test_sentinel_summary_no_runs_without_sentinels(runs):
  assert runs([_flush_lines([(100, ["1", "2"]), (110, ["3", "4"])])]) == []


def test_sentinel_summary_code_change_closes_run(runs):
  got = runs([_flush_lines([(100, ["-3", "1"]), (110, ["-5", "2"])])])
  assert [_shape(r) for r in got] == [
    (-5, 110.0, 110.0, 1, 2, 100.0),
    (-3, 100.0, 100.0, 1, 2, None),
  ]


def test_sentinel_summary_device_count_change_splits_run(runs):
  got = runs([_flush_lines([(100, ["-2", "1"]), (110, ["-2", "-2"])])])
  assert [_shape(r) for r in got] == [
    (-2, 100.0, 100.0, 1, 2, None),
    (-2, 110.0, 110.0, 2, 2, 100.0),
  ]


def test_sentinel_summary_recovery_closes_run(runs):
  got = runs(
    [_flush_lines([(100, ["-3", "1"]), (110, ["4", "2"]), (120, ["-3", "3"])])]
  )
  assert [_shape(r) for r in got] == [
    (-3, 100.0, 100.0, 1, 2, None),
    (-3, 120.0, 120.0, 1, 2, 110.0),
  ]


def test_sentinel_summary_concurrent_codes_are_separate_runs(runs):
  got = runs(
    [_flush_lines([(100, ["-3", "-2", "5"]), (110, ["-3", "-2", "6"])])]
  )
  assert {_shape(r) for r in got} == {
    (-3, 100.0, 110.0, 1, 3, None),
    (-2, 100.0, 110.0, 1, 3, None),
  }


def test_sentinel_summary_next_flush_names_previous_sample(runs):
  first = _flush_lines([(100, ["-2", "1"]), (110, ["-2", "2"])])
  second = _flush_lines([(120, ["-2", "3"])], header=False)
  got = runs([first, second])
  assert [_shape(r) for r in got] == [
    (-2, 100.0, 110.0, 1, 2, None),
    (-2, 120.0, 120.0, 1, 2, 110.0),
  ]


def test_sentinel_summary_keep_dev_series_per_device(runs):
  lines = ["!nvidia_gpu gpu_util,W=64,U=1", *_SENTINEL_HDR]
  for t in (100, 110):
    lines += [f"{t} J1 hostA", "nvidia_gpu 0 -2", "nvidia_gpu 1 50"]
  got = runs([lines])
  assert len(got) == 1
  run = got[0]
  assert run[1:4] == ("nvidia_gpu", "0", "gpu_util")
  assert _shape(run) == (-2, 100.0, 110.0, 1, 1, None)
