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
import pytest

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
  cols, _proc, _cr, _ca, _marks, _np = mod.feed_delta_collapse_arc(
    lines, {}, {}
  )
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
  cols, proc, carry_raw, carry_arc, _marks, _np = mod.feed_delta_collapse_arc(
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
    _bridge_dt,
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
  # [value, wid, mult, time, missed sentinel samples, chain broken]
  assert carry_out[key] == [5.0, 4.0, 1.0, 102.0, 0.0, 0.0]
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
    _bridge_dt,
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
    _bridge_dt,
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
  cols, proc, carry_raw, carry_arc, _marks, parsed = (
    mod.feed_delta_collapse_arc([], {}, {})
  )
  for col in cols:
    assert col == []
  for col in proc:
    assert col == []
  assert carry_raw == {}
  assert carry_arc == {}
  assert parsed == 0


def test_parsed_host_rows_counts_host_records_before_collapse() -> None:
  """RC-0: the zero-host gate needs host records parsed, not rows kept."""
  mod = _load_feed_collapse()
  lines = [
    "!cpu user,E,W=64,U=1",
    "1000.0 J1 hostA",
    "cpu 0 @full 10.0",
    "cpu 1 @full 20.0",
  ]
  cols, *_rest, parsed = mod.feed_delta_collapse_arc(lines, {}, {})
  assert len(cols[0]) == 1
  assert parsed == 2


def test_parsed_host_rows_is_zero_for_proc_only_input() -> None:
  mod = _load_feed_collapse()
  lines = [
    "!host_proc uid,W=32 vm_peak,W=64",
    "1000.0 J1 hostA",
    "host_proc 12/a/0/0 @full 7 100",
  ]
  cols, proc, *_rest, parsed = mod.feed_delta_collapse_arc(lines, {}, {})
  assert cols[0] == [] and len(proc[0]) == 1
  assert parsed == 0


_SENTINEL_HDR = ["$hpcperfstats 3.1", "$hostname hostA"]


def _sentinel_corpora() -> dict[str, list[list[str]]]:
  """Flush sequences (each a list of lines) covering every sentinel rule."""

  def samples(typ: str, rows) -> list[str]:
    out: list[str] = []
    for t, devs in rows:
      out.append(f"{t} J1 hostA")
      out += [f"{typ} {d} {v}" for d, v in devs]
    return out

  ib = "!host_ib rx_bytes,E,W=64,U=B"
  cpu = "!cpu user,E,W=64,U=cs"
  hw = "!host_cpu_hw instr_retired,E,W=48,U=1"
  nv = "!nvidia_gpu clocks_event_reasons,W=64,U=1 gpu_util,W=64,U=1"
  one = lambda pts: [(t, [("mlx5_0.1", v)]) for t, v in pts]  # noqa: E731
  return {
    "bridge": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples("host_ib", one([(100, 1000), (110, -3), (120, 1300)])),
      ]
    ],
    "chain_break": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples(
          "host_ib", one([(100, 1000), (110, -2), (120, 1300), (130, 1400)])
        ),
      ]
    ],
    "limit_break": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples(
          "host_ib",
          one(
            [(100, 0), (110, -3), (120, -3), (130, -3), (140, -3), (150, 50)]
          ),
        ),
      ]
    ],
    "limit_exact": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples(
          "host_ib",
          one([(100, 0), (110, -3), (120, -3), (130, -3), (140, 40)]),
        ),
      ]
    ],
    "unknown_code": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples("host_ib", one([(100, 1000), (110, -77), (120, 1300)])),
      ]
    ],
    "restart": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples("host_ib", one([(100, 1000), (110, -3)])),
        *_SENTINEL_HDR,
        *samples("host_ib", one([(120, 1300)])),
      ]
    ],
    "likwid_override": [
      [
        hw,
        *_SENTINEL_HDR,
        "$build x86_64-ver3.1-hw-ib-likwid-full",
        *samples(
          "host_cpu_hw",
          [(100, [("0", 10)]), (110, [("0", -1)]), (120, [("0", 30)])],
        ),
      ]
    ],
    "arm_bridges": [
      [
        hw,
        *_SENTINEL_HDR,
        "$build aarch64-ver3.1-hw-arm-full",
        *samples(
          "host_cpu_hw",
          [(100, [("0", 2**48 - 100)]), (110, [("0", -3)]), (120, [("0", 50)])],
        ),
      ]
    ],
    "collapse_mix": [
      [
        cpu,
        *_SENTINEL_HDR,
        *samples(
          "cpu",
          [
            (100, [("0", 100), ("1", 100)]),
            (110, [("0", 110), ("1", -3)]),
            (120, [("0", 120), ("1", 140)]),
            (130, [("0", -2), ("1", -2)]),
            (140, [("0", -5), ("1", -3)]),
          ],
        ),
      ]
    ],
    "nvidia_or": [
      [
        nv,
        *_SENTINEL_HDR,
        *samples(
          "nvidia_gpu",
          [
            (100, [("0", "4 10")]),
            (110, [("0", "-8 -3")]),
            (120, [("0", "6 30")]),
          ],
        ),
      ]
    ],
    "cross_flush": [
      [ib, *_SENTINEL_HDR, *samples("host_ib", one([(100, 1000), (110, -3)]))],
      [ib, *samples("host_ib", one([(120, -3), (130, 1300)]))],
      [ib, *samples("host_ib", one([(140, -5)]))],
      [ib, *samples("host_ib", one([(150, 1500), (160, 1600)]))],
    ],
    "cross_flush_header_cut": [
      [
        ib,
        *_SENTINEL_HDR,
        *samples("host_ib", one([(100, 1000), (110, -3)])),
        *_SENTINEL_HDR,
      ],
      [ib, *samples("host_ib", one([(120, 1300)]))],
    ],
  }


def _cpython_sentinel_rows(flushes: list[list[str]]) -> dict[tuple, tuple]:
  from unittest import mock

  from hpcperfstats.dbload.lib import sync_timedb_parsing as stp

  carry = stp.DeltaCarryState()
  parser = stp.IncrementalStatsParser(0, delta_carry=carry)
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
  rows: dict[tuple, tuple] = {}
  with mock.patch.object(
    stp, "_sentinel_bridge_max_missed_samples", return_value=3
  ):
    for lines in flushes:
      parser.feed_lines(list(lines))
      df = pd.DataFrame(
        stp.stats_payload_to_records(parser.take_stats_columns()), columns=cols
      )
      out = stp.compute_deltas_and_arc_chunk(df, carry=carry)
      for _, r in out.iterrows():
        key = (
          r["host"],
          r["type"],
          str(r["dev"] or ""),
          r["event"],
          float(r["time"]),
        )
        assert key not in rows, f"duplicate output row {key}"
        rows[key] = (float(r["value"]), float(r["delta"]), float(r["arc"]))
  return rows


def _codon_sentinel_rows(mod, flushes: list[list[str]]) -> dict[tuple, tuple]:
  carry_raw: dict = {"\0cfg\0bridge_max": [3.0]}
  carry_arc: dict = {}
  rows: dict[tuple, tuple] = {}
  for lines in flushes:
    cols, _p, carry_raw, carry_arc, _m, _n = mod.feed_delta_collapse_arc(
      list(lines), carry_raw, carry_arc
    )
    times, hosts, _j, types, devs, events, _u, values, deltas, arcs = cols
    for i in range(len(times)):
      key = (hosts[i], types[i], devs[i], events[i], times[i])
      assert key not in rows, f"duplicate output row {key}"
      rows[key] = (values[i], deltas[i], arcs[i])
  return rows


def _restart_drop_corpora() -> dict[str, list[list[str]]]:
  """W=32 ``pkg_energy`` drops across a ``$`` restart header, in one flush
  and across a flush boundary, plus a genuine wrap with no header between."""
  rapl = "!intel_x86_rapl pkg_energy,E,W=32,U=mJ"
  restart_hdr = [*_SENTINEL_HDR, "$uptime 5"]

  def pkg(points) -> list[str]:
    out: list[str] = []
    for t, v in points:
      out += [f"{t} J1 hostA", f"intel_x86_rapl 0 {v}"]
    return out

  return {
    "restart_drop": [
      [
        rapl,
        *_SENTINEL_HDR,
        *pkg([(100, 4000001000)]),
        *restart_hdr,
        *pkg([(110, 5000), (120, 6000)]),
      ]
    ],
    "restart_drop_cross_flush": [
      [rapl, *_SENTINEL_HDR, *pkg([(100, 4000001000)])],
      [rapl, *restart_hdr, *pkg([(110, 5000)])],
    ],
    "wrap_no_restart": [
      [rapl, *_SENTINEL_HDR, *pkg([(100, 2**32 - 296), (110, 704)])]
    ],
  }


@pytest.mark.parametrize("name", sorted(_sentinel_corpora()))
def test_sentinel_corpora_codon_matches_cpython(name: str) -> None:
  """Bridge, chain break, limit, unknown code, restart, LIKWID override, W=48
  wrap, valid-only collapse, modal code, per-device bridged arc, NVIDIA OR
  restore, cross-flush carry and a header cut at a window boundary."""
  mod = _load_feed_collapse()
  flushes = _sentinel_corpora()[name]
  want = _cpython_sentinel_rows(flushes)
  got = _codon_sentinel_rows(mod, flushes)
  assert want, f"{name}: empty oracle"
  _assert_rows_match(got, want)


def test_counter_drop_across_restart_breaks_chain_both_engines() -> None:
  """A counter drop across a ``$`` restart header is a chain break (NaN),
  not a W-bit wrap, in both engines; a drop with no header between still
  unwraps."""
  mod = _load_feed_collapse()
  key = ("hostA", "intel_x86_rapl", "", "pkg_energy")
  want = {}
  for name, flushes in _restart_drop_corpora().items():
    want[name] = _cpython_sentinel_rows(flushes)
    _assert_rows_match(_codon_sentinel_rows(mod, flushes), want[name])
  assert math.isnan(want["restart_drop"][(*key, 110.0)][1])
  assert want["restart_drop"][(*key, 120.0)][1] == 1000
  assert math.isnan(want["restart_drop_cross_flush"][(*key, 110.0)][1])
  assert want["wrap_no_restart"][(*key, 110.0)][1] == 1000


def _whole_file_corpora() -> dict[str, list[str]]:
  """Single-flush sentinel and restart-drop corpora as one file each."""
  corpora = {**_sentinel_corpora(), **_restart_drop_corpora()}
  return {name: f[0] for name, f in corpora.items() if len(f) == 1}


def _cpython_whole_file_rows(lines: list[str]) -> dict[tuple, tuple]:
  """Rows of the non-streamed contents path with Codon disabled.

  Same steps as ``sync_timedb._parse_stats_file_payload_impl``:
  ``parse_stats_lines`` -> ``build_stats_dataframes`` -> delta step, with the
  parser's ``$`` header state in one ``DeltaCarryState``.
  """
  from unittest import mock

  from hpcperfstats.dbload.lib import (
    sync_timedb_codon_bridge as bridge,
    sync_timedb_parsing as stp,
  )

  carry = stp.DeltaCarryState()
  with (
    mock.patch.object(bridge, "try_codon_parse_collapse", return_value=None),
    mock.patch.object(
      stp, "_sentinel_bridge_max_missed_samples", return_value=3
    ),
  ):
    stats, procs = stp.parse_stats_lines(list(lines), 0, delta_carry=carry)
    df, _procs = stp.build_stats_dataframes(stats, procs)
    out = stp.compute_deltas_and_arc(df, carry=carry)
  return {
    (r["host"], r["type"], str(r["dev"] or ""), r["event"], float(r["time"])): (
      float(r["value"]),
      float(r["delta"]),
      float(r["arc"]),
    )
    for _, r in out.iterrows()
  }


@pytest.mark.parametrize("name", sorted(_whole_file_corpora()))
def test_whole_file_cpython_path_matches_codon(name: str) -> None:
  """The CPython whole-file path sees ``$`` restarts and ``$build`` like
  Codon's line batch, which scans the headers itself."""
  lines = _whole_file_corpora()[name]
  want = _codon_sentinel_rows(_load_feed_collapse(), [lines])
  _assert_rows_match(_cpython_whole_file_rows(lines), want)


def test_whole_file_cpython_path_breaks_chains_at_restart_headers() -> None:
  """W=32 reset across ``$`` is NaN, not 294971296 mJ; a bridgeable gap
  across a restart breaks; ``$build`` still proves the ARM bridge."""
  corpora = _whole_file_corpora()
  rapl = ("hostA", "intel_x86_rapl", "", "pkg_energy")
  drop = _cpython_whole_file_rows(corpora["restart_drop"])
  assert math.isnan(drop[(*rapl, 110.0)][1])
  assert drop[(*rapl, 120.0)][1] == 1000
  gap = _cpython_whole_file_rows(corpora["restart"])
  assert math.isnan(gap[("hostA", "host_ib", "", "rx_bytes", 120.0)][1])
  arm = _cpython_whole_file_rows(corpora["arm_bridges"])
  assert arm[("hostA", "host_cpu_hw", "", "instr_retired", 120.0)][1] == 150


def test_sentinel_corpora_have_sentinel_and_bridged_rows() -> None:
  """Positive control: the corpora really carry codes and bridged deltas."""
  mod = _load_feed_collapse()
  rows = _codon_sentinel_rows(mod, _sentinel_corpora()["collapse_mix"])
  assert any(v < 0 for v, _d, _a in rows.values())
  bridged = rows[("hostA", "cpu", "", "user", 120.0)]
  assert bridged[1] == 50 and bridged[2] == pytest.approx(3.0)


def test_sentinel_codon_literals_match_cpython() -> None:
  from hpcperfstats.dbload.lib import conf_parser, sync_timedb_parsing as stp
  from hpcperfstats.lib import monitor_sentinel

  mod = _load_feed_collapse()
  assert sorted(mod.SENTINEL_BRIDGEABLE_CODES) == sorted(
    float(c) for c, e in monitor_sentinel.CATALOG.items() if e.bridge
  )
  assert mod.CARRY_BUILD_KEY == stp._CARRY_BUILD_KEY
  assert mod.CARRY_RESTART_KEY == stp._CARRY_RESTART_KEY
  assert mod.CARRY_HEADER_PENDING_KEY == stp._CARRY_HEADER_PENDING_KEY
  assert mod.CARRY_RESTART_KEEP == stp._CARRY_RESTART_KEEP
  assert (
    str(mod.SENTINEL_BRIDGE_MAX_DEFAULT)
    == conf_parser.INI_OPTION_DEFAULTS[
      "ingest_sentinel_bridge_max_missed_samples"
    ]
  )
  assert set(mod._CCM_TYPES) == set(stp._HOST_CPU_HW_TYPES)
  from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
    CODON_CARRY_BRIDGE_MAX_KEY,
  )

  assert CODON_CARRY_BRIDGE_MAX_KEY == mod.CARRY_BRIDGE_MAX_KEY
  pyext_c = (FEED_COLLAPSE.parent / "pyext_ft_init.c").read_text()
  assert '#define CARRY_BRIDGE_MAX_KEY "\\0cfg\\0bridge_max"' in pyext_c


def _norm_sentinel_marks(marks) -> set[tuple]:
  out = set()
  for m in marks:
    if m.get("kind") != "sentinel":
      continue
    typ, dev, event, code, end, n, total, prev = str(m["jids"]).split("\t")
    out.add(
      (
        m["host"],
        typ,
        dev,
        event,
        int(code),
        float(m["time"]),
        float(end),
        int(n),
        int(total),
        float(prev) if prev else None,
      )
    )
  return out


@pytest.mark.parametrize("name", sorted(_sentinel_corpora()))
def test_sentinel_run_marks_codon_matches_cpython(
  name: str, monkeypatch
) -> None:
  """host_sentinel_summary runs: same records from both engines per flush."""
  from hpcperfstats.dbload.lib import (
    monitor_mark_side_ingest as mmsi,
    sync_timedb_parsing as stp,
  )

  seen: list = []
  monkeypatch.setattr(mmsi, "persist_monitor_marks", lambda m: seen.extend(m))
  _cpython_sentinel_rows(_sentinel_corpora()[name])
  want = _norm_sentinel_marks(seen)
  assert want, f"{name}: CPython twin emitted no sentinel runs"
  mod = _load_feed_collapse()
  carry_raw: dict = {"\0cfg\0bridge_max": [3.0]}
  carry_arc: dict = {}
  got_marks: list = []
  for lines in _sentinel_corpora()[name]:
    _c, _p, carry_raw, carry_arc, marks, _n = mod.feed_delta_collapse_arc(
      list(lines), carry_raw, carry_arc
    )
    got_marks += marks
  assert _norm_sentinel_marks(got_marks) == want
  assert stp is not None


def test_sentinel_run_marks_extend_and_split() -> None:
  """A steady code is one run; a device-count change splits it; recovery
  closes it; the next flush's run names the previous sample time."""
  mod = _load_feed_collapse()
  cpu = "!cpu user,E,W=64,U=cs"
  lines = [cpu, *_SENTINEL_HDR]
  for t, a, b in ((100, -2, 5), (110, -2, 6), (120, -2, -2), (130, 7, 8)):
    lines += [f"{t} J1 hostA", f"cpu 0 {a}", f"cpu 1 {b}"]
  _c, _p, _cr, carry_arc, marks, _n = mod.feed_delta_collapse_arc(lines, {}, {})
  runs = sorted(_norm_sentinel_marks(marks), key=lambda r: r[5])
  assert [(r[5], r[6], r[7], r[8]) for r in runs] == [
    (100.0, 110.0, 1, 2),
    (120.0, 120.0, 2, 2),
  ]
  assert runs[1][9] == 110.0
  nxt = [cpu, "140 J1 hostA", "cpu 0 -2", "cpu 1 9"]
  _c, _p, _cr, _ca, marks2, _n = mod.feed_delta_collapse_arc(nxt, {}, carry_arc)
  (run,) = _norm_sentinel_marks(marks2)
  assert run[5] == 140.0 and run[9] == 130.0
