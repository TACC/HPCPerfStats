"""Two-tier sparse monitor row parsing (@fast/@full, ,R=S schema suffix)."""
import warnings

import pandas as pd

from hpcperfstats.dbload.sync_timedb_parsing import (
    _fast_schema_keys,
    _schema_token_is_slow_tier,
    build_stats_dataframes,
    compute_deltas_and_arc,
    parse_stats_lines,
)

_HOST_TT_SCHEMA = "!host_tt a,E b,E,R=S c,E d,E,R=S\n"
_HOST_TT_TS = "1700000000 job1 cn001\n"


def _events_by_name(stats_list):
  return {(r["type"], r["event"], r["value"]) for r in stats_list}


def test_schema_r_s_builds_fast_subset():
  assert _schema_token_is_slow_tier("b,E,R=S") is True
  assert _schema_token_is_slow_tier("a,E") is False
  assert _schema_token_is_slow_tier("rx_bytes,E,U=B,R=S") is True

  full = ["a,E", "b,E,R=S", "c,E"]
  assert _fast_schema_keys(full) == ["a,E", "c,E"]


def test_legacy_row_unchanged():
  lines = [
      _HOST_TT_TS,
      _HOST_TT_SCHEMA,
      "host_tt dev0 100 200 300 400\n",
  ]
  stats, _ = parse_stats_lines(lines, start_idx=0)
  events = _events_by_name(stats)
  assert ("host_tt", "a", 100.0) in events
  assert ("host_tt", "b", 200.0) in events
  assert ("host_tt", "c", 300.0) in events
  assert ("host_tt", "d", 400.0) in events
  assert len(stats) == 4


def test_full_row_matches_legacy():
  legacy_lines = [
      _HOST_TT_TS,
      _HOST_TT_SCHEMA,
      "host_tt dev0 100 200 300 400\n",
  ]
  full_lines = [
      _HOST_TT_TS,
      _HOST_TT_SCHEMA,
      "host_tt dev0 @full 100 200 300 400\n",
  ]
  legacy_stats, _ = parse_stats_lines(legacy_lines, start_idx=0)
  full_stats, _ = parse_stats_lines(full_lines, start_idx=0)
  assert _events_by_name(legacy_stats) == _events_by_name(full_stats)


def test_fast_row_sparse_subset():
  lines = [
      _HOST_TT_TS,
      _HOST_TT_SCHEMA,
      "host_tt dev0 @fast 100 300\n",
  ]
  stats, _ = parse_stats_lines(lines, start_idx=0)
  events = _events_by_name(stats)
  assert ("host_tt", "a", 100.0) in events
  assert ("host_tt", "c", 300.0) in events
  assert ("host_tt", "b", 200.0) not in events
  assert ("host_tt", "d", 400.0) not in events
  assert len(stats) == 2


def test_mismatched_column_count_skipped():
  lines = [
      _HOST_TT_TS,
      _HOST_TT_SCHEMA,
      "host_tt dev0 @fast 100 300 999\n",
  ]
  with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    stats, _ = parse_stats_lines(lines, start_idx=0)
  assert stats == []
  assert any("value count" in str(w.message) for w in caught)


def test_compute_deltas_mixed_cadence():
  """Slow keys on @full rows use the slow sample interval for delta/arc."""
  lines = [
      "1700000000 job1 cn001\n",
      _HOST_TT_SCHEMA,
      "host_tt dev0 @fast 1000 3000\n",
      "1700000030 job1 cn001\n",
      "host_tt dev0 @fast 1100 3300\n",
      "1700000600 job1 cn001\n",
      "host_tt dev0 @full 2000 2500 4000 4500\n",
      "1700001200 job1 cn001\n",
      "host_tt dev0 @full 3000 3500 5000 5500\n",
  ]
  stats_list, _ = parse_stats_lines(lines, start_idx=0)
  stats_df, _ = build_stats_dataframes(stats_list, [])
  result = compute_deltas_and_arc(stats_df)

  slow_b = result[
      (result["type"] == "host_tt")
      & (result["event"] == "b")
  ].sort_values("time")
  assert len(slow_b) == 2
  second = slow_b.iloc[1]
  assert second["delta"] == 1000.0
  assert abs(float(second["arc"]) - (1000.0 / 600.0)) < 1e-6

  fast_a = result[
      (result["type"] == "host_tt")
      & (result["event"] == "a")
  ].sort_values("time")
  assert len(fast_a) == 4
  arc_30s = fast_a.iloc[1]
  assert arc_30s["delta"] == 100.0
  assert abs(float(arc_30s["arc"]) - (100.0 / 30.0)) < 1e-6


def test_archive_round_trip_tiered_fixture():
  """Inline tiered archive: parse + dataframe round-trip."""
  lines = [
      "1700000000 job1 cn001\n",
      _HOST_TT_SCHEMA,
      "host_tt dev0 @fast 100 300\n",
      "1700000600 job1 cn001\n",
      "host_tt dev0 @full 200 250 400 450\n",
  ]
  stats_list, proc_list = parse_stats_lines(lines, start_idx=0)
  assert proc_list == []

  stats_df, proc_df = build_stats_dataframes(stats_list, proc_list)
  assert proc_df.empty
  assert not stats_df.empty
  assert set(stats_df["event"].unique()) == {"a", "b", "c", "d"}

  fast_only_times = stats_df[
      stats_df["event"].isin(["a", "c"])
  ]["time"].unique()
  assert 1700000000.0 in fast_only_times

  slow_rows = stats_df[stats_df["event"].isin(["b", "d"])]
  assert set(slow_rows["time"].unique()) == {1700000600.0}

  deltas_df = compute_deltas_and_arc(stats_df)
  assert not deltas_df.empty
  assert {"delta", "arc"}.issubset(deltas_df.columns)
  assert pd.api.types.is_datetime64_any_dtype(deltas_df["time"])
