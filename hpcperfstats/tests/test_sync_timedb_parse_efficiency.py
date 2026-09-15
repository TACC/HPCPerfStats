"""Regression tests for columnar parse, DCGM numpy collapse, and packed carry."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    HOST_PROC_KEYS,
    IncrementalStatsParser,
    _cluster_mean_sum_sorted,
    _compile_schema_token,
    _nullable_int_max,
    _nvidia_bitwise_or_values,
    build_stats_dataframes,
    compute_deltas_and_arc,
    compute_deltas_and_arc_chunk,
    dedupe_proc_stats_peak_merge,
    exclude_types,
    parse_stats_file_streaming_incremental,
    parse_stats_lines,
    reset_parse_stage_timing,
    snapshot_parse_stage_timing,
    stats_payload_row_count,
    stats_payload_to_records,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK, nan_out_dcgm_numeric_blanks


_MINIMAL_LINES = [
    "1709123456 job1 cn001\n",
    "!cpu user,W=48 sys,W=48\n",
    "cpu 0 100 200\n",
]


def test_exclude_types_is_frozenset():
  """Hot-path membership checks must use an immutable exclude set."""
  assert isinstance(exclude_types, frozenset)
  assert "host_ps" in exclude_types


def test_compile_schema_token_parses_width_unit_once():
  """``!`` schema tokens compile W=/U= once, not per event row."""
  event, wid, mult, unit = _compile_schema_token("CAS_READS,W=48,U=64B")
  assert event == "CAS_READS"
  assert wid == 48
  assert mult == 64.0
  assert unit == "B"


def test_compile_schema_is_idempotent_on_bang_replay():
  """Re-feeding the same ``!`` line must not change compiled fields."""
  parser = IncrementalStatsParser(0)
  parser.feed_line("!cpu user,W=48 sys,W=48\n")
  first = {
      k: list(v) for k, v in parser.schema_compiled["cpu"].items()
  }
  parser.feed_line("!cpu user,W=48 sys,W=48\n")
  assert {
      k: list(v) for k, v in parser.schema_compiled["cpu"].items()
  } == first


def test_compile_schema_soa_has_no_per_line_zip_in_append():
  """Approach C: emit must extend SoA columns without ``zip(*compiled)``."""
  text = Path(__file__).resolve().parents[1].joinpath(
      "dbload/lib/sync_timedb_parsing.py",
  ).read_text(encoding="utf-8")
  start = text.index("def _append_compiled_stats_columns(")
  end = text.index("\ndef ", start + 1)
  body = text[start:end]
  assert "zip(*compiled)" not in body
  compiled = IncrementalStatsParser(0)
  compiled.feed_line("!cpu user,W=48 sys\n")
  soa = compiled.schema_compiled["cpu"]
  assert soa["events"] == ["user", "sys"]
  assert soa["wids"] == [48, 64]
  assert compiled.schema_bare["cpu"] == ["user", "sys"]


def test_proc_bare_names_compiled_at_bang_not_per_sample():
  """Proc ``schema_key_basename`` runs at ``!``, not on every sample line."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import HOST_PROC_KEYS

  keys = " ".join(
      f"{k},U=kB" if k.startswith("vm_") else k for k in HOST_PROC_KEYS
  )
  parser = IncrementalStatsParser(0)
  parser.feed_line(f"!host_proc {keys}\n")
  assert parser.schema_bare["host_proc"] == list(HOST_PROC_KEYS)
  parser.feed_line("1709123456 job1 cn001\n")
  # HOST_PROC_KEYS order: uid vm_size vm_rss ... threads (13 fields).
  vals = " ".join(str(1000 + i) for i in range(len(HOST_PROC_KEYS)))
  parser.feed_line(f"host_proc python/4242/0-7/0 {vals}\n")
  row = parser.proc_stats[0]
  assert row["proc"] == "python"
  assert row["uid"] == 1000
  assert row["threads"] == 1000 + len(HOST_PROC_KEYS) - 1


def test_parse_stats_lines_records_adapter_matches_columnar_builder():
  """Public records adapter must match DataFrame built from columns."""
  stats_list, proc_list = parse_stats_lines(_MINIMAL_LINES, 0)
  assert proc_list == []
  parser = IncrementalStatsParser(0)
  parser.feed_lines(_MINIMAL_LINES)
  cols = parser.take_stats_columns()
  from_cols, _ = build_stats_dataframes(cols, [])
  from_records, _ = build_stats_dataframes(stats_list, [])
  pd.testing.assert_frame_equal(
      from_cols.reset_index(drop=True),
      from_records.reset_index(drop=True),
      check_dtype=False,
  )


def test_hardware_parser_source_has_no_per_event_rec_spread():
  """Approach A: hardware emit must not copy tags via ``{**rec``."""
  text = Path(__file__).resolve().parents[1].joinpath(
      "dbload/lib/sync_timedb_parsing.py",
  ).read_text(encoding="utf-8")
  assert "{**rec" not in text


def test_cluster_mean_sum_sorted_matches_python_gap_walk():
  """DCGM cluster-mean must match the historical sequential gap walk."""
  values = [1.0, 1.1, 10.0, 10.2, 50.0]

  def _python_ref(vals: list[float], gap: float) -> float:
    v = sorted(float(x) for x in vals)
    total = 0.0
    cluster = [v[0]]
    for x in v[1:]:
      if x - cluster[-1] <= gap:
        cluster.append(x)
      else:
        total += sum(cluster) / len(cluster)
        cluster = [x]
    total += sum(cluster) / len(cluster)
    return total

  got = _cluster_mean_sum_sorted(values, 1.0)
  assert got == _python_ref(values, 1.0)


def test_cluster_mean_rejects_dcgm_blank_socket():
  """DCGM blank-family sentinels are missing, not a ~1.4e14 W cluster."""
  got = _cluster_mean_sum_sorted([100.0, 100.0, float(DCGM_FP64_BLANK)], 1.0)
  assert got == 100.0


def test_nvidia_bitwise_or_values_uses_finite_non_blank_bits():
  """OR collapse must skip NaN/blank and match a Python accumulator."""
  series = pd.Series([1.0, 2.0, np.nan, float(DCGM_FP64_BLANK), 4.0])
  acc = 0
  mask64 = (1 << 64) - 1
  for v in series:
    if pd.notna(v) and float(v) < DCGM_FP64_BLANK:
      acc |= int(v) & mask64
  assert _nvidia_bitwise_or_values(series) == float(acc & mask64)


def test_nan_out_dcgm_numeric_blanks_can_mutate_in_place():
  """Blank family values become NaN without requiring a second array copy."""
  arr = np.array([1.0, float(DCGM_FP64_BLANK)], dtype=np.float64)
  out = nan_out_dcgm_numeric_blanks(arr, copy=False)
  assert out is arr
  assert np.isnan(out[1])
  assert out[0] == 1.0


def test_groupby_sum_min_count_keeps_all_nan_as_nan():
  """``sum(min_count=1)`` must not turn an all-NaN group into 0."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import _groupby_sum_min_count

  df = pd.DataFrame({
      "host": ["h", "h"],
      "type": ["cpu", "cpu"],
      "event": ["user", "user"],
      "unit": ["#", "#"],
      "time": [1.0, 1.0],
      "value": [np.nan, np.nan],
      "delta": [np.nan, np.nan],
      "jid": ["j", "j"],
  })
  out = _groupby_sum_min_count(
      df, ["host", "type", "event", "unit", "time"],
  )
  assert len(out) == 1
  assert pd.isna(out.iloc[0]["value"])
  assert pd.isna(out.iloc[0]["delta"])
  assert out.iloc[0]["jid"] == "j"


def test_packed_carry_tuple_matches_full_file_delta():
  """Packed carry values must keep wrap/delta continuity across flushes."""
  rows = [
      {
          "host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#",
          "time": 100.0, "value": 10.0, "wid": 8, "mult": 1.0, "jid": "j",
      },
      {
          "host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#",
          "time": 110.0, "value": 20.0, "wid": 8, "mult": 1.0, "jid": "j",
      },
      {
          "host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#",
          "time": 120.0, "value": 5.0, "wid": 8, "mult": 1.0, "jid": "j",
      },
  ]
  full = compute_deltas_and_arc(pd.DataFrame(rows))
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState

  carry = DeltaCarryState()
  part1 = compute_deltas_and_arc_chunk(pd.DataFrame(rows[:1]), carry=carry)
  part2 = compute_deltas_and_arc_chunk(pd.DataFrame(rows[1:]), carry=carry)
  combined = pd.concat([part1, part2], ignore_index=True)
  pd.testing.assert_series_equal(
      combined.sort_values("time")["delta"].reset_index(drop=True),
      full.sort_values("time")["delta"].reset_index(drop=True),
      check_names=False,
  )
  raw_val = next(iter(carry.raw.values()))
  assert isinstance(raw_val, tuple)
  assert len(raw_val) == 4


def test_incremental_flush_holds_compiled_schema(tmp_path):
  """Schema compiled at ``!`` must survive stats-column flushes."""
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text(
      "!cpu user sys\n"
      "1709123456 job1 host.example.com\n"
      "cpu 0 1 2\n"
      "1709123457 job1 host.example.com\n"
      "cpu 0 3 4\n",
      encoding="utf-8",
  )
  chunks = []

  def on_chunk(stats_payload, proc_payload):
    chunks.append((
        stats_payload_to_records(stats_payload),
        list(proc_payload),
    ))

  parse_stats_file_streaming_incremental(
      str(stats_file),
      flush_rows=1,
      on_chunk=on_chunk,
  )
  assert len(chunks) >= 2
  assert stats_payload_row_count(chunks[0][0]) >= 1
  expected, _ = parse_stats_lines(
      stats_file.read_text(encoding="utf-8").splitlines(keepends=True),
      0,
  )
  got = [row for stats, _proc in chunks for row in stats]
  assert got == expected


def test_listend_one_sample_matches_chunk_kernel():
  """listend one-sample ORM path must share compute_deltas_and_arc_chunk."""
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState

  schema = {"cpu": ["user,W=48", "sys,W=48"]}
  schema_fast = {"cpu": ["user,W=48", "sys,W=48"]}
  sample = (
      "1710000001.0 job42 host.example.edu\n"
      "cpu 0 10 20\n"
  )
  carry = DeltaCarryState()
  host_objs, proc_objs = ldi._process_sample_to_orm(
      sample,
      host="host.example.edu",
      schema=schema,
      schema_fast=schema_fast,
      carry=carry,
  )
  assert proc_objs == []
  assert host_objs
  parser = IncrementalStatsParser(0)
  parser.schema = dict(schema)
  parser.schema_fast = dict(schema_fast)
  parser.feed_lines(sample.splitlines())
  cols = parser.take_stats_columns()
  stats_df, _ = build_stats_dataframes(cols, [])
  finalized = compute_deltas_and_arc_chunk(stats_df, carry=DeltaCarryState())
  assert len(finalized) == len(host_objs)
  assert {o.event for o in host_objs} == set(finalized["event"].tolist())
  assert all(o.jid == "job42" for o in host_objs)


def test_host_microbench_parse_fixture_runs():
  """Approach G smoke: parse a synthetic sample; no new engine deps."""
  lines = _MINIMAL_LINES * 20
  stats_list, proc_list = parse_stats_lines(lines, 0)
  stats_df, proc_df = build_stats_dataframes(stats_list, proc_list)
  assert proc_df.empty
  assert not stats_df.empty


def test_append_compiled_stats_value_extend_is_materialized_list():
  """Approach A: value column extend must use a list, not a float generator."""
  text = Path(__file__).resolve().parents[1].joinpath(
      "dbload/lib/sync_timedb_parsing.py",
  ).read_text(encoding="utf-8")
  start = text.index("def _append_compiled_stats_columns(")
  end = text.index("\ndef ", start + 1)
  body = text[start:end]
  assert 'cols["value"].extend([float(v) for v in vals])' in body
  assert 'cols["value"].extend(float(v) for v in vals)' not in body


def test_sparse_host_proc_omits_missing_keys():
  """Approach B: host_proc rows omit unparsed HOST_PROC_KEYS (no None prefill)."""
  keys = (
      "uid,R=S vm_peak,U=kB vm_size,U=kB vm_lck,U=kB,R=S vm_hwm,U=kB,R=S "
      "vm_rss,U=kB vm_data,U=kB vm_stk,U=kB vm_exe,U=kB vm_lib,U=kB "
      "vm_pte,U=kB,R=S vm_swap,U=kB threads"
  )
  parser = IncrementalStatsParser(0)
  parser.feed_line(f"!host_proc {keys}\n")
  parser.feed_line("1709123456 job1 cn001\n")
  parser.feed_line(
      "host_proc python/1/0/0 @fast "
      "9000 8000 6000 5000 4000 3000 2000 500 8\n",
  )
  row = parser.proc_stats[0]
  for slow in ("uid", "vm_lck", "vm_hwm", "vm_pte"):
    assert slow not in row
  assert row["vm_peak"] == 9000
  assert row["threads"] == 8


def test_build_stats_dataframes_peak_merge_without_to_dict_roundtrip():
  """Approach C: peak-merge list-native; no DataFrame.to_dict in builder."""
  text = Path(__file__).resolve().parents[1].joinpath(
      "dbload/lib/sync_timedb_parsing.py",
  ).read_text(encoding="utf-8")
  start = text.index("def build_stats_dataframes(")
  end = text.index("\ndef ", start + 1)
  body = text[start:end]
  assert 'to_dict(orient="records")' not in body
  proc_list = [
      {
          "time": 1,
          "host": "h",
          "jid": "j",
          "proc": "p",
          "device": "p/1",
          "vm_peak": 10,
          "vm_hwm": 5,
      },
      {
          "time": 2,
          "host": "h",
          "jid": "j",
          "proc": "p",
          "device": "p/1",
          "vm_peak": 20,
          "vm_hwm": 3,
      },
  ]
  _stats_df, proc_df = build_stats_dataframes([], proc_list)
  assert len(proc_df) == 1
  assert int(proc_df.iloc[0]["vm_peak"]) == 20
  assert int(proc_df.iloc[0]["vm_hwm"]) == 5


def _host_proc_schema_line() -> str:
  keys = " ".join(
      f"{k},U=kB" if k.startswith("vm_") else k for k in HOST_PROC_KEYS
  )
  return f"!host_proc {keys}\n"


def _host_proc_vals(*, vm_peak: int, threads: int) -> str:
  """Build full host_proc value tokens; override peak + threads."""
  vals = []
  for i, k in enumerate(HOST_PROC_KEYS):
    if k == "vm_peak":
      vals.append(str(vm_peak))
    elif k == "threads":
      vals.append(str(threads))
    else:
      vals.append(str(1000 + i))
  return " ".join(vals)


def test_online_proc_merge_equals_batch_dedupe():
  """Wave 4: online (jid,host,proc) merge must match batch peak-merge."""
  schema = _host_proc_schema_line()
  lines = [
      schema,
      "1709123456 job1 cn001\n",
      f"host_proc python/1/0/0 {_host_proc_vals(vm_peak=9000, threads=1)}\n",
      "1709123457 job1 cn001\n",
      f"host_proc python/1/0/0 {_host_proc_vals(vm_peak=8000, threads=8)}\n",
      "1709123458 job1 cn001\n",
      f"host_proc other/2/0/0 {_host_proc_vals(vm_peak=100, threads=2)}\n",
  ]
  parser = IncrementalStatsParser(0)
  parser.feed_lines(lines)
  online = parser.take_proc_stats()
  assert parser.proc_stats == []
  assert len(online) == 2
  by_proc = {r["proc"]: r for r in online}
  assert by_proc["python"]["vm_peak"] == 9000
  assert by_proc["python"]["threads"] == 8
  assert by_proc["other"]["vm_peak"] == 100
  explicit = [
      {
          "time": 1709123456.0,
          "host": "cn001",
          "jid": "job1",
          "proc": "python",
          "device": "python/1/0/0",
          "vm_peak": 9000,
          "threads": 1,
      },
      {
          "time": 1709123457.0,
          "host": "cn001",
          "jid": "job1",
          "proc": "python",
          "device": "python/1/0/0",
          "vm_peak": 8000,
          "threads": 8,
      },
      {
          "time": 1709123458.0,
          "host": "cn001",
          "jid": "job1",
          "proc": "other",
          "device": "other/2/0/0",
          "vm_peak": 100,
          "threads": 2,
      },
  ]
  batch_by = {r["proc"]: r for r in dedupe_proc_stats_peak_merge(explicit)}
  assert by_proc["python"]["vm_peak"] == batch_by["python"]["vm_peak"]
  assert by_proc["python"]["threads"] == batch_by["python"]["threads"]


def test_nullable_int_max_fastpath_and_dirty():
  """Wave 4 Approach B: int/int fast path; None/dirty keep prior contract."""
  assert _nullable_int_max(10, 3) == 10
  assert _nullable_int_max(3, 10) == 10
  assert _nullable_int_max(None, 5) == 5
  assert _nullable_int_max(5, None) == 5
  assert _nullable_int_max(None, None) is None
  assert _nullable_int_max("12", 7) == 12
  assert _nullable_int_max("bad", 7) == 7


def test_peak_merge_ownership_first_hit_no_copy():
  """First insert must take row ownership (same object identity)."""
  row = {"jid": "j", "host": "h", "proc": "p", "vm_peak": 1, "threads": 1}
  out = dedupe_proc_stats_peak_merge([row])
  assert len(out) == 1
  assert out[0] is row


def test_online_proc_merge_telem_under_proc_merge_s():
  """Online merge work must still accumulate under proc_merge_s when telem on."""
  reset_parse_stage_timing(enabled=True)
  try:
    parser = IncrementalStatsParser(0)
    parser.feed_line(_host_proc_schema_line())
    parser.feed_line("1709123456 job1 cn001\n")
    vals = _host_proc_vals(vm_peak=1, threads=1)
    for _ in range(20):
      parser.feed_line(f"host_proc python/1/0/0 {vals}\n")
    snap = snapshot_parse_stage_timing()
    assert "proc_merge_s" in snap
    assert snap["proc_merge_s"] >= 0.0
    assert len(parser.take_proc_stats()) == 1
  finally:
    reset_parse_stage_timing(enabled=False)
