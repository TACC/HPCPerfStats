"""Parity + contract tests for the Codon feed->delta->collapse->arc pipeline.

Locks the ``feed_delta_collapse_arc`` / ``apply_counter_deltas_cols`` /
``collapse_full_cols`` / ``apply_arc_cols`` contract in
``hpcperfstats/dbload/codon/feed_collapse.py`` against the CPython
``_apply_counter_deltas`` / ``_collapse_stats_with_deltas`` /
``_apply_arc_and_finalize`` oracle in ``sync_timedb_parsing.py``.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

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
    "feed_collapse_delta_arc_under_test", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _broad_corpus() -> list[str]:
  """Covers host-sum, CCM power cluster-mean-sum, and all nvidia classes."""
  return [
    "!cpu user,W=64,U=1",
    "!host_cpu_hw dcgm_cpu_power_util_w,W=64,U=1",
    (
      "!nvidia_gpu gpu_util,W=64,U=1 temperature,W=64,U=1 "
      "module_power_usage,W=64,U=1 clocks_event_reasons,W=64,U=1"
    ),
    "!host_mem_job rss,W=64,U=1",
    "1000.0 J1 hostA",
    "cpu 0 @full 10.0",
    "cpu 1 @full 20.0",
    "host_cpu_hw 0 @full 100.0",
    "host_cpu_hw 1 @full 100.5",
    "host_cpu_hw 2 @full 500.0",
    "nvidia_gpu 0 @full 1.0 60.0 5.0 3.0",
    "nvidia_gpu 1 @full 2.0 70.0 7.0 5.0",
    "host_mem_job 0 @full 42.0",
    "1001.0 J1 hostA",
    "cpu 0 @full 15.0",
    "cpu 1 @full 25.0",
    "host_cpu_hw 0 @full 150.0",
    "host_cpu_hw 1 @full 150.5",
    "host_cpu_hw 2 @full 600.0",
    "nvidia_gpu 0 @full 1.5 65.0 6.0 2.0",
    "nvidia_gpu 1 @full 2.5 75.0 8.0 6.0",
    "host_mem_job 0 @full 50.0",
  ]


def _cpython_oracle_rows(lines: list[str]) -> dict[tuple, tuple]:
  """Run the real CPython parser + delta/collapse/arc path as the oracle."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    compute_deltas_and_arc,
    stats_payload_to_records,
  )

  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  stats, _procs = parser.finish()
  records = stats_payload_to_records(stats)
  cols = [
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "value",
    "wid",
    "mult",
    "unit",
  ]
  df = (
    pd.DataFrame(records, columns=cols)
    if records
    else pd.DataFrame(columns=cols)
  )
  out = compute_deltas_and_arc(df)
  rows: dict[tuple, tuple] = {}
  for _, row in out.iterrows():
    key = (
      row["host"],
      row["type"],
      str(row["dev"] or ""),
      row["event"],
      float(row["time"]),
    )
    rows[key] = (float(row["value"]), float(row["delta"]), float(row["arc"]))
  return rows


def _codon_rows(mod, lines: list[str]) -> dict[tuple, tuple]:
  cols, _proc, _cr, _ca, _marks = mod.feed_delta_collapse_arc(lines, {}, {})
  times, hosts, _jids, types, devs, events, _units, values, deltas, arcs = cols
  rows: dict[tuple, tuple] = {}
  for i in range(len(times)):
    key = (hosts[i], types[i], devs[i], events[i], times[i])
    rows[key] = (values[i], deltas[i], arcs[i])
  return rows


def _assert_rows_match(
  got: dict[tuple, tuple], want: dict[tuple, tuple]
) -> None:
  assert set(got.keys()) == set(want.keys()), (
    f"key mismatch: only_got={set(got) - set(want)} only_want={set(want) - set(got)}"
  )
  for key, (gv, gd, ga) in got.items():
    wv, wd, wa = want[key]
    for g, w, label in ((gv, wv, "value"), (gd, wd, "delta"), (ga, wa, "arc")):
      if w is None or (isinstance(w, float) and math.isnan(w)):
        assert g is None or (isinstance(g, float) and math.isnan(g)), (
          f"{key} {label}: got={g} want=missing"
        )
      else:
        assert math.isclose(g, w, rel_tol=1e-9, abs_tol=1e-6), (
          f"{key} {label}: got={g} want={w}"
        )


def test_counter_key_joins_with_nul_and_empty_dev() -> None:
  mod = _load_feed_collapse()
  assert mod._counter_key("h", "cpu", "0", "user") == "h\x00cpu\x000\x00user"
  assert mod._counter_key("h", "cpu", "", "user") == "h\x00cpu\x00\x00user"


def test_feed_delta_collapse_arc_matches_cpython_broad_corpus() -> None:
  """Host-sum + CCM cluster-mean-sum + nvidia max/mean/OR/sum all match."""
  mod = _load_feed_collapse()
  lines = _broad_corpus()
  got = _codon_rows(mod, lines)
  want = _cpython_oracle_rows(lines)
  _assert_rows_match(got, want)
  # Whole-node CCM power collapses across cores: one '' row per timestamp.
  ccm_keys = [k for k in got if k[1] == "host_cpu_hw"]
  assert len(ccm_keys) == 2
  assert {k[2] for k in ccm_keys} == {""}
  # Whole-node CPU sums to '' too; GPU keeps hardware dev, host_mem_job j:.
  assert {k[2] for k in got if k[1] == "cpu"} == {""}
  assert {k[2] for k in got if k[1] == "nvidia_gpu"} == {"0", "1"}
  assert {k[2] for k in got if k[1] == "host_mem_job"} == {"j:J1"}


def test_feed_delta_collapse_arc_e6_corpus_smoke() -> None:
  """Regression lock for the exact smoke invocation used during development."""
  mod = _load_feed_collapse()
  lines = mod.build_e6_payload_lines(5)
  cols, proc, carry_raw, carry_arc, _marks = mod.feed_delta_collapse_arc(
    lines, {}, {}
  )
  assert len(cols) == 10
  assert len(cols[0]) > 0
  assert len(proc) == 5
  assert len(proc[0]) == 1
  assert isinstance(carry_raw, dict)
  assert isinstance(carry_arc, dict)


def test_apply_counter_deltas_cols_wrap_and_carry_continuity() -> None:
  """Wrap on negative diff, carry hit on first-of-batch, no caller mutation."""
  mod = _load_feed_collapse()
  # wid=4 -> modulus 16; raw counter wraps from 15 back to 2.
  carry_raw_in = {
    mod._counter_key("h", "cpu", "0", "user"): [15.0, 4.0, 1.0, 100.0],
  }
  carry_raw_snapshot = dict(carry_raw_in)

  times = [101.0, 102.0]
  hosts = ["h", "h"]
  jids = ["J1", "J1"]
  types = ["cpu", "cpu"]
  devs = ["0", "0"]
  events = ["user", "user"]
  units = ["#", "#"]
  values = [2.0, 5.0]
  wids = [4, 4]
  mults = [1.0, 1.0]

  (
    _ot,
    _oh,
    _oj,
    _oty,
    _od,
    _oe,
    _ou,
    ovalue,
    odelta,
    carry_out,
  ) = mod.apply_counter_deltas_cols(
    times,
    hosts,
    jids,
    types,
    devs,
    events,
    units,
    values,
    wids,
    mults,
    carry_raw_in,
  )

  # First row: wrap-corrected delta from carry (15 -> 2 over modulus 16): (2-15)=-13 -> 16-13=3.
  assert math.isclose(odelta[0], 3.0)
  # Second row: in-batch diff 5-2=3, no wrap.
  assert math.isclose(odelta[1], 3.0)
  assert ovalue == [2.0, 5.0]

  key = mod._counter_key("h", "cpu", "0", "user")
  assert carry_out[key] == [5.0, 4.0, 1.0, 102.0]
  # Caller's dict must be untouched (copy semantics).
  assert carry_raw_in == carry_raw_snapshot


def test_apply_counter_deltas_cols_gauge_dip_is_nan() -> None:
  """A drop from 100 to 40 must not become 2**64."""
  mod = _load_feed_collapse()
  (
    _ot,
    _oh,
    _oj,
    _oty,
    _od,
    _oe,
    _ou,
    _ov,
    odelta,
    _carry,
  ) = mod.apply_counter_deltas_cols(
    [1.0, 2.0],
    ["h", "h"],
    ["J", "J"],
    ["nfs", "nfs"],
    ["", ""],
    ["READ_ops", "READ_ops"],
    ["#", "#"],
    [100.0, 40.0],
    [64, 64],
    [1.0, 1.0],
    {},
  )
  assert math.isnan(odelta[1])


def test_apply_counter_deltas_cols_top_half_wrap_still_corrects() -> None:
  """8-bit 250 -> 10 is one modulus (16), not a reset."""
  mod = _load_feed_collapse()
  (
    _ot,
    _oh,
    _oj,
    _oty,
    _od,
    _oe,
    _ou,
    _ov,
    odelta,
    _carry,
  ) = mod.apply_counter_deltas_cols(
    [1.0, 2.0],
    ["h", "h"],
    ["J", "J"],
    ["nfs", "nfs"],
    ["", ""],
    ["READ_ops", "READ_ops"],
    ["#", "#"],
    [250.0, 10.0],
    [8, 8],
    [1.0, 1.0],
    {},
  )
  assert math.isclose(odelta[1], 16.0)


def test_apply_arc_cols_carry_continuity_and_dropna() -> None:
  """Separate keys isolate carry-continuity from the dropna(NaN time) path.

  CPython's ``_apply_arc_and_finalize`` updates ``carry.arc`` from the
  group's last row (by sort order) *before* the final ``dropna`` on the
  return frame, so a NaN-time last row legitimately carries NaN forward for
  its own group/key; it must not affect a different group's carry entry.
  """
  mod = _load_feed_collapse()
  key_user = mod._counter_key("h", "cpu", "", "user")
  key_nice = mod._counter_key("h", "cpu", "", "nice")
  carry_arc_in = {key_user: 100.0}
  carry_arc_snapshot = dict(carry_arc_in)

  times = [103.0, float("nan")]
  hosts = ["h", "h"]
  jids = ["J1", "J1"]
  types = ["cpu", "cpu"]
  devs = ["", ""]
  events = ["user", "nice"]
  units = ["#", "#"]
  values = [9.0, float("nan")]
  deltas = [6.0, 1.0]

  (
    f_time,
    _fh,
    _fj,
    _ft,
    _fd,
    _fe,
    _fu,
    f_value,
    f_delta,
    f_arc,
    carry_out,
  ) = mod.apply_arc_cols(
    times, hosts, jids, types, devs, events, units, values, deltas, carry_arc_in
  )

  # Row with NaN time must be dropped from the finished output.
  assert f_time == [103.0]
  assert f_value == [9.0]
  assert f_delta == [6.0]
  # arc = delta / (103.0 - 100.0) = 6/3 = 2.0
  assert math.isclose(f_arc[0], 2.0)
  assert carry_out[key_user] == 103.0
  assert math.isnan(carry_out[key_nice])
  assert carry_arc_in == carry_arc_snapshot


def test_feed_delta_collapse_arc_empty_lines() -> None:
  mod = _load_feed_collapse()
  cols, proc, carry_raw, carry_arc, _marks = mod.feed_delta_collapse_arc(
    [], {}, {}
  )
  for col in cols:
    assert col == []
  for col in proc:
    assert col == []
  assert carry_raw == {}
  assert carry_arc == {}
