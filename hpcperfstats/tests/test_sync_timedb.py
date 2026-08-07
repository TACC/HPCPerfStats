"""Unit tests for sync_timedb parsing and helper functions (no DB; uses sync_timedb_parsing to avoid Django)."""
import os
from collections import namedtuple
import pandas as pd

from hpcperfstats.dbload.lib.io_helpers import host_data_instance_from_stats_row
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    EVENTMAPS_BY_TYPE,
    DeltaCarryState,
    _COLLAPSE_GROUP_COLS,
    _COLLAPSE_GROUP_COLS_WITH_DEV,
    _collapse_dcg_cpu_power_gauge_group,
    _collapse_dcg_cpu_power_vectorized,
    _collapse_nvidia_gpu_group,
    _collapse_nvidia_gpu_vectorized,
    _collapse_stats_with_deltas,
    build_stats_dataframes,
    compute_deltas_and_arc,
    compute_deltas_and_arc_chunk,
    exclude_types,
    find_processing_start_index,
    load_stats_file_lines,
    parse_first_timestamp_line,
    parse_stats_file_path,
    parse_stats_file_streaming,
    parse_stats_file_streaming_incremental,
    parse_stats_lines,
    stats_file_size_bytes,
)


# --- parse_stats_file_path ---


def test_parse_stats_file_path_normal():
  """Path with hostname and create_time returns both."""
  hostname, create_time = parse_stats_file_path("/var/stats/cn001/1709123456")
  assert hostname == "cn001"
  assert create_time == "1709123456"


def test_parse_stats_file_path_deep():
  """Path with multiple segments uses last two."""
  hostname, create_time = parse_stats_file_path("/a/b/c/host/123")
  assert hostname == "host"
  assert create_time == "123"


def test_parse_stats_file_path_single_segment():
  """Single segment returns None, None."""
  hostname, create_time = parse_stats_file_path("only")
  assert hostname is None
  assert create_time is None


def test_parse_stats_file_path_empty():
  """Empty string yields empty parts; last two are missing."""
  hostname, create_time = parse_stats_file_path("")
  assert hostname is None
  assert create_time is None


# --- load_stats_file_lines ---


def test_load_stats_file_lines_from_contents():
  """When contents provided, returns them as lines and no error."""
  contents = ["12345 job1 node1\n", "! amd64_pmc a b\n"]
  lines, err = load_stats_file_lines("/any/path", stats_file_contents=contents)
  assert err is None
  assert lines == contents


def test_load_stats_file_lines_from_contents_string():
  """When contents is a list of lines, each element is a line."""
  contents = ["line1\n", "line2\n"]
  lines, err = load_stats_file_lines("/any/path", stats_file_contents=contents)
  assert lines == contents
  assert err is None


def test_load_stats_file_lines_file_not_found():
  """When path does not exist and no contents, returns error message."""
  lines, err = load_stats_file_lines("/nonexistent/path/123/456")
  assert lines is None
  assert err is not None
  assert "disappeared" in err or "nonexistent" in err or "123" in err


def test_load_stats_file_lines_reads_file(tmp_path):
  """When contents not provided, reads from disk."""
  stats_file = tmp_path / "host" / "123"
  stats_file.parent.mkdir(parents=True, exist_ok=True)
  stats_file.write_text("1709123456 job1 cn001\n")
  lines, err = load_stats_file_lines(str(stats_file))
  assert err is None
  assert len(lines) >= 1
  assert "1709123456" in lines[0]
  # Lock file is created while reading and cleaned up on release; it should not
  # remain after load_stats_file_lines returns.
  assert not (tmp_path / "host" / "123.fnctl.lock").exists()


# --- parse_first_timestamp_line ---


def test_parse_first_timestamp_line_found():
  """First digit line is parsed as t jid host."""
  lines = ["\n", "  \n", "1709123456 job1 cn001\n", "other\n"]
  t, jid, host = parse_first_timestamp_line(lines)
  assert t == "1709123456"
  assert jid == "job1"
  assert host == "cn001"


def test_parse_first_timestamp_line_empty():
  """Empty lines list returns None, None, None."""
  t, jid, host = parse_first_timestamp_line([])
  assert t is None
  assert jid is None
  assert host is None


def test_parse_first_timestamp_line_no_digit():
  """No line starting with digit returns None."""
  lines = ["! amd64_pmc a b\n", "amd64_pmc dev 1 2 3\n"]
  t, jid, host = parse_first_timestamp_line(lines)
  assert t is None
  assert jid is None
  assert host is None


def test_parse_first_timestamp_line_skips_bad_line():
  """Line with wrong number of tokens is skipped, next digit line used."""
  lines = ["12345\n", "1709123456 job1 cn001\n"]
  t, jid, host = parse_first_timestamp_line(lines)
  assert t == "1709123456"
  assert jid == "job1"
  assert host == "cn001"


def test_parse_first_timestamp_line_ignores_leading_spaces():
  """Leading whitespace before timestamp is ignored."""
  lines = ["   1709123456 job1 cn001\n"]
  t, jid, host = parse_first_timestamp_line(lines)
  assert t == "1709123456"
  assert jid == "job1"
  assert host == "cn001"


def test_parse_first_timestamp_line_keeps_placeholder_jid():
  """jid '-' is valid input and should be returned as-is by the parser."""
  lines = ["1709123456 - cn001\n"]
  t, jid, host = parse_first_timestamp_line(lines)
  assert t == "1709123456"
  assert jid == "-"
  assert host == "cn001"


# --- find_processing_start_index ---


def test_find_processing_start_index_all_missing():
  """When no timestamp is in itimes_set, start at first valid timestamp index."""
  lines = [
      "1709123456 job1 cn001\n",
      "1709123460 job1 cn001\n",
  ]
  itimes_set = set()
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  assert start_idx == 0
  assert need_archival is True


def test_find_processing_start_index_all_present():
  """When all timestamps are in itimes_set, start_idx is -1."""
  lines = [
      "1709123456 job1 cn001\n",
      "1709123460 job1 cn001\n",
  ]
  itimes_set = {1709123456, 1709123460}
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  assert start_idx == -1
  assert need_archival is True


def test_find_processing_start_index_one_missing():
  """First timestamp not in DB: start_idx is index of previous (last known) line."""
  lines = [
      "1709123456 job1 cn001\n",   # 0 - in DB
      "1709123460 job1 cn001\n",   # 1 - in DB
      "1709123464 job1 cn001\n",   # 2 - missing
  ]
  itimes_set = {1709123456, 1709123460}
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  assert start_idx == 1
  assert need_archival is True


def test_find_processing_start_index_with_leading_spaces():
  """Leading whitespace before timestamps should not prevent detection."""
  lines = [
      "   1709123456 job1 cn001\n",
      "   1709123460 job1 cn001\n",
  ]
  itimes_set = set()
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  assert start_idx == 0
  assert need_archival is True


def test_find_processing_start_index_includes_job_missing():
  """Lines with jid '-' still drive processing start detection."""
  lines = [
      "1709123456 - cn001\n",
      "1709123460 - cn001\n",
  ]
  itimes_set = set()
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  assert start_idx == 0
  assert need_archival is True


# --- parse_stats_lines ---


def test_parse_stats_lines_minimal_software():
  """Minimal lines: one timestamp, one schema (!type events...), one software counter line."""
  lines = [
      "1709123456 job1 cn001\n",
      "!cpu user sys\n",
      "cpu 0 100 200\n",
  ]
  start_idx = 0
  eventmaps = {}
  exclude = ["ib"]
  stats_list, proc_list = parse_stats_lines(
      lines, start_idx,
      eventmaps_by_type=eventmaps,
      exclude_types_list=exclude,
  )
  assert len(stats_list) == 2
  assert stats_list[0]["event"] == "user"
  assert stats_list[0]["value"] == 100.0
  assert stats_list[1]["event"] == "sys"
  assert stats_list[1]["value"] == 200.0
  assert len(proc_list) == 0


def test_parse_stats_lines_handles_leading_spaces():
  """Parser should tolerate leading whitespace on all line types."""
  lines = [
      "   1709123456 job1 cn001\n",
      "   !cpu user sys\n",
      "   cpu 0 100 200\n",
  ]
  start_idx = 0
  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  assert len(stats_list) == 2
  assert stats_list[0]["event"] == "user"
  assert stats_list[0]["value"] == 100.0
  assert stats_list[1]["event"] == "sys"
  assert stats_list[1]["value"] == 200.0
  assert len(proc_list) == 0


def test_parse_stats_lines_with_missing_jid_placeholder_no_host_data_jid():
  """When jid is '-', stats rows omit jid (host_data is time/host scoped); proc still uses jid."""
  lines = [
      "1709123456 - cn001\n",
      "!cpu user sys\n",
      "cpu 0 100 200\n",
  ]
  start_idx = 0
  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  assert len(stats_list) == 2
  assert all("jid" not in r for r in stats_list)
  assert len(proc_list) == 0


def test_parse_stats_lines_proc_type():
  """proc type lines add to proc_stats with device + KEYS (name is first path component)."""
  lines = [
      "1709123456 job1 cn001\n",
      "proc usr/bin/foo 1000 1 2 3 4 5 6 7 8 9 10 11 12\n",
  ]
  start_idx = 0
  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  assert len(stats_list) == 0
  assert len(proc_list) == 1
  assert proc_list[0]["proc"] == "usr"
  assert proc_list[0]["device"] == "usr/bin/foo"
  assert proc_list[0]["jid"] == "job1"
  assert proc_list[0]["host"] == "cn001"
  assert proc_list[0]["uid"] == 1000
  assert proc_list[0]["vm_peak"] == 1
  assert proc_list[0]["vm_rss"] == 5
  assert proc_list[0]["threads"] == 12


def test_parse_stats_lines_host_proc_schema_keys():
  """host_proc with !schema KEYS line stores device + all numeric fields (T0 contract)."""
  keys = (
      "uid vm_peak vm_size vm_lck vm_hwm vm_rss "
      "vm_data vm_stk vm_exe vm_lib vm_pte vm_swap threads"
  )
  lines = [
      f"!host_proc {keys}\n",
      "1709123456 job1 cn001\n",
      "host_proc python/4242/0-7/0 1001 9000 8000 0 7000 6000 5000 4000 3000 2000 1000 500 8\n",
  ]
  stats_list, proc_list = parse_stats_lines(lines, start_idx=0)
  assert stats_list == []
  assert len(proc_list) == 1
  row = proc_list[0]
  assert row["proc"] == "python"
  assert row["device"] == "python/4242/0-7/0"
  assert row["jid"] == "job1"
  assert row["host"] == "cn001"
  assert row["time"] == 1709123456.0
  assert row["uid"] == 1001
  assert row["vm_peak"] == 9000
  assert row["vm_size"] == 8000
  assert row["vm_lck"] == 0
  assert row["vm_hwm"] == 7000
  assert row["vm_rss"] == 6000
  assert row["vm_data"] == 5000
  assert row["vm_stk"] == 4000
  assert row["vm_exe"] == 3000
  assert row["vm_lib"] == 2000
  assert row["vm_pte"] == 1000
  assert row["vm_swap"] == 500
  assert row["threads"] == 8


def test_parse_stats_lines_host_proc_schema_keys_with_unit_suffixes():
  """Regression jid 1778: production !host_proc uses vm_peak,U=kB tokens."""
  keys = (
      "uid vm_peak,U=kB vm_size,U=kB vm_lck,U=kB vm_hwm,U=kB vm_rss,U=kB "
      "vm_data,U=kB vm_stk,U=kB vm_exe,U=kB vm_lib,U=kB vm_pte,U=kB "
      "vm_swap,U=kB threads"
  )
  lines = [
      f"!host_proc {keys}\n",
      "1709123456 job1 cn001\n",
      "host_proc python/4242/0-7/0 1001 9000 8000 0 7000 6000 5000 4000 3000 2000 1000 500 8\n",
  ]
  _stats, proc_list = parse_stats_lines(lines, start_idx=0)
  assert len(proc_list) == 1
  row = proc_list[0]
  assert row["uid"] == 1001
  assert row["vm_peak"] == 9000
  assert row["vm_hwm"] == 7000
  assert row["vm_rss"] == 6000
  assert row["vm_stk"] == 4000
  assert row["vm_exe"] == 3000
  assert row["vm_lib"] == 2000
  assert row["threads"] == 8


def test_parse_stats_lines_host_proc_at_full_production_shape():
  """hs04: @full must not be consumed as uid (was all-null / threads=0)."""
  keys = (
      "uid,R=S vm_peak,U=kB vm_size,U=kB vm_lck,U=kB,R=S vm_hwm,U=kB,R=S "
      "vm_rss,U=kB vm_data,U=kB vm_stk,U=kB vm_exe,U=kB vm_lib,U=kB "
      "vm_pte,U=kB,R=S vm_swap,U=kB threads"
  )
  lines = [
      f"!host_proc {keys}\n",
      "1709123456 job1 cn001\n",
      "host_proc polkitd/499505/0-143/0-2,10,18,26 @full "
      "114 374080 308544 0 9920 9920 35200 192 128 9920 448 0 4\n",
  ]
  _stats, proc_list = parse_stats_lines(lines, start_idx=0)
  assert len(proc_list) == 1
  row = proc_list[0]
  assert row["proc"] == "polkitd"
  assert row["device"] == "polkitd/499505/0-143/0-2,10,18,26"
  assert row["uid"] == 114
  assert row["vm_peak"] == 374080
  assert row["vm_size"] == 308544
  assert row["vm_lck"] == 0
  assert row["vm_hwm"] == 9920
  assert row["vm_rss"] == 9920
  assert row["vm_data"] == 35200
  assert row["vm_stk"] == 192
  assert row["vm_exe"] == 128
  assert row["vm_lib"] == 9920
  assert row["vm_pte"] == 448
  assert row["vm_swap"] == 0
  assert row["threads"] == 4


def test_parse_stats_lines_host_proc_at_fast_omits_slow_keys():
  """@fast maps schema_fast only; slow KEYS stay None (never invent 0)."""
  keys = (
      "uid,R=S vm_peak,U=kB vm_size,U=kB vm_lck,U=kB,R=S vm_hwm,U=kB,R=S "
      "vm_rss,U=kB vm_data,U=kB vm_stk,U=kB vm_exe,U=kB vm_lib,U=kB "
      "vm_pte,U=kB,R=S vm_swap,U=kB threads"
  )
  # Fast KEYS: vm_peak vm_size vm_rss vm_data vm_stk vm_exe vm_lib vm_swap threads
  lines = [
      f"!host_proc {keys}\n",
      "1709123456 job1 cn001\n",
      "host_proc python/1/0/0 @fast "
      "9000 8000 6000 5000 4000 3000 2000 500 8\n",
  ]
  _stats, proc_list = parse_stats_lines(lines, start_idx=0)
  assert len(proc_list) == 1
  row = proc_list[0]
  assert row["uid"] is None
  assert row["vm_lck"] is None
  assert row["vm_hwm"] is None
  assert row["vm_pte"] is None
  assert row["vm_peak"] == 9000
  assert row["vm_size"] == 8000
  assert row["vm_rss"] == 6000
  assert row["vm_data"] == 5000
  assert row["vm_stk"] == 4000
  assert row["vm_exe"] == 3000
  assert row["vm_lib"] == 2000
  assert row["vm_swap"] == 500
  assert row["threads"] == 8


def test_merge_proc_row_dicts_greatest_peak_and_hwm():
  """vm_peak/vm_hwm use GREATEST so later 0 cannot erase job-level high water."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import merge_proc_row_dicts

  merged = merge_proc_row_dicts(
      {"vm_peak": 9000, "vm_hwm": 7000, "vm_stk": 100, "threads": 1},
      {"vm_peak": 0, "vm_hwm": 100, "vm_stk": 40, "threads": 8},
  )
  assert merged["vm_peak"] == 9000
  assert merged["vm_hwm"] == 7000
  assert merged["vm_stk"] == 100
  assert merged["threads"] == 8


def test_parse_stats_lines_excluded_type():
  """Excluded type is skipped."""
  lines = [
      "1709123456 job1 cn001\n",
      "!tmpfs a b\n",
      "tmpfs dev 1 2\n",
  ]
  start_idx = 0
  stats_list, proc_list = parse_stats_lines(
      lines, start_idx,
      exclude_types_list=["tmpfs"],
  )
  assert len(stats_list) == 0
  assert len(proc_list) == 0


def test_parse_stats_lines_starts_at_start_idx():
  """Only timestamps at or after start_idx trigger insert."""
  lines = [
      "1709123456 job1 cn001\n",
      "!cpu a b\n",
      "cpu 0 1 2\n",
      "1709123460 job1 cn001\n",
      "cpu 0 3 4\n",
  ]
  start_idx = 3
  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  assert len(stats_list) == 2
  assert stats_list[0]["value"] == 3.0
  assert stats_list[1]["value"] == 4.0


# --- build_stats_dataframes ---


def test_build_stats_dataframes_empty():
  """Empty lists yield empty DataFrames."""
  stats_df, proc_df = build_stats_dataframes([], [])
  assert stats_df.empty
  assert proc_df.empty


def test_build_stats_dataframes_dedupe_proc():
  """Duplicate (jid, host, proc): last-write non-peak; GREATEST peak KEYS."""
  stats_list = [
      {"time": 1.0, "host": "h", "type": "cpu", "dev": "0", "event": "a", "value": 1.0, "wid": 64, "mult": 1, "unit": "#"},
  ]
  proc_list = [
      {
          "jid": "j",
          "host": "h",
          "proc": "p",
          "device": "p/1/0/0",
          "vm_rss": 10,
          "vm_peak": 900,
          "vm_hwm": 700,
          "vm_stk": 100,
          "vm_exe": 50,
          "vm_lib": 20,
          "threads": 1,
      },
      {
          "jid": "j",
          "host": "h",
          "proc": "p",
          "device": "p/2/0/0",
          "vm_rss": 99,
          "vm_peak": 800,
          "vm_hwm": 0,
          "vm_stk": 40,
          "vm_exe": 60,
          "vm_lib": 10,
          "threads": 4,
      },
  ]
  stats_df, proc_df = build_stats_dataframes(stats_list, proc_list)
  assert len(stats_df) == 1
  assert len(proc_df) == 1
  assert proc_df.iloc[0]["proc"] == "p"
  assert int(proc_df.iloc[0]["vm_rss"]) == 99
  assert int(proc_df.iloc[0]["vm_peak"]) == 900
  assert int(proc_df.iloc[0]["vm_hwm"]) == 700
  assert int(proc_df.iloc[0]["threads"]) == 4
  assert int(proc_df.iloc[0]["vm_stk"]) == 100
  assert int(proc_df.iloc[0]["vm_exe"]) == 60
  assert int(proc_df.iloc[0]["vm_lib"]) == 20
  assert proc_df.iloc[0]["device"] == "p/2/0/0"


def test_build_stats_dataframes_records():
  """Stats list becomes DataFrame with expected columns."""
  stats_list = [
      {"time": 1.0, "host": "h", "type": "t", "dev": "d", "event": "e", "value": 10.0, "wid": 48, "mult": 1, "unit": "#"},
  ]
  stats_df, _ = build_stats_dataframes(stats_list, [])
  assert "value" in stats_df.columns
  assert stats_df.iloc[0]["value"] == 10.0


# --- compute_deltas_and_arc ---


def test_compute_deltas_and_arc_two_timestamps():
  """Two timestamps: first row keeps value with NaN delta/arc; second has rates."""
  stats_df = pd.DataFrame([
      {"host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 100.0, "wid": 48, "mult": 1},
      {"host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 250.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  assert result.iloc[0]["value"] == 100.0
  assert pd.isna(result.iloc[0]["delta"]) and pd.isna(result.iloc[0]["arc"])
  assert result.iloc[1]["delta"] == 150.0
  assert result.iloc[1]["arc"] == 15.0


def test_compute_deltas_and_arc_rollover():
  """Negative delta is corrected for 48-bit rollover."""
  stats_df = pd.DataFrame([
      {"host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 1000.0, "wid": 48, "mult": 1},
      {"host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 10.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  expected_delta = (2**48 - 1000 + 10) * 1
  assert pd.isna(result.iloc[0]["delta"])
  assert result.iloc[1]["delta"] == expected_delta


def test_compute_deltas_and_arc_zero_deltat_yields_nan_arc():
  """Same (host,type,event) time twice (different unit rows): Δt=0 → arc undefined."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "cpu", "dev": "0", "event": "user",
          "unit": "jiffies", "time": 100.0, "value": 1.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "type": "cpu", "dev": "0", "event": "user",
          "unit": "ticks", "time": 100.0, "value": 2.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  arc_second = result.sort_values(
      by=["host", "type", "event", "time", "unit"]).iloc[1]["arc"]
  assert pd.isna(arc_second)


def test_compute_deltas_and_arc_keeps_first_timestamp_value():
  """First timestamp per group has NaN delta/arc but value is kept for complex metrics."""
  stats_df = pd.DataFrame([
      {"host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 100.0, "wid": 48, "mult": 1},
      {"host": "h", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 200.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  assert "arc" in result.columns
  assert "delta" in result.columns
  assert "time" in result.columns
  assert result.iloc[0]["value"] == 100.0
  assert pd.isna(result.iloc[0]["arc"])


def test_compute_deltas_and_arc_nvidia_gpu_util_keeps_dev():
  """nvidia_gpu gpu_util: same timestamp two devs -> one row per device."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": 40.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": 50.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  by_dev = {str(r["dev"]): float(r["value"]) for _, r in result.iterrows()}
  assert by_dev == {"0": 40.0, "1": 50.0}


def test_compute_deltas_and_arc_nvidia_temperature_keeps_dev():
  """nvidia_gpu temperature: two devs stay distinct (mean only within a device)."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "temperature", "unit": "C", "time": 100.0, "value": 60.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "temperature", "unit": "C", "time": 100.0, "value": 80.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  by_dev = {str(r["dev"]): float(r["value"]) for _, r in result.iterrows()}
  assert by_dev == {"0": 60.0, "1": 80.0}


def test_compute_deltas_and_arc_dcg_cpu_power_clusters_sockets():
  """Grace DCGM CPU power: repeated per-core readings cluster into per-socket sums."""
  rows = []
  for _ in range(3):
    rows.append({
        "host": "h",
        "type": "cpu_counter_metrics",
        "dev": str(len(rows)),
        "event": "DCGM_CPU_POWER_UTIL_W",
        "unit": "W",
        "time": 100.0,
        "value": 100.0,
        "wid": 48,
        "mult": 1,
    })
  for _ in range(3):
    rows.append({
        "host": "h",
        "type": "cpu_counter_metrics",
        "dev": str(len(rows)),
        "event": "DCGM_CPU_POWER_UTIL_W",
        "unit": "W",
        "time": 100.0,
        "value": 80.0,
        "wid": 48,
        "mult": 1,
    })
  stats_df = pd.DataFrame(rows)
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  assert abs(float(result.iloc[0]["value"]) - 180.0) < 1e-6


def test_compute_deltas_and_arc_nvidia_module_power_keeps_dev():
  """module_power_usage: one row per GPU (MAX only within a device group)."""
  stats_df = pd.DataFrame([
      {
          "host": "h",
          "type": "nvidia_gpu",
          "dev": "0",
          "event": "module_power_usage",
          "unit": "W",
          "time": 100.0,
          "value": 500.0,
          "wid": 48,
          "mult": 1,
      },
      {
          "host": "h",
          "type": "nvidia_gpu",
          "dev": "1",
          "event": "module_power_usage",
          "unit": "W",
          "time": 100.0,
          "value": 500.0,
          "wid": 48,
          "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  assert set(float(v) for v in result["value"]) == {500.0}


def test_compute_deltas_and_arc_nvidia_clocks_event_reasons_keeps_dev():
  """nvidia_gpu clocks_event_reasons: OR only within each device."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0, "value": 5.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0, "value": 2.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  by_dev = {str(r["dev"]): int(r["value"]) for _, r in result.iterrows()}
  assert by_dev == {"0": 5, "1": 2}


def test_compute_deltas_and_arc_nvidia_gpu_count_max_not_nxn():
  """Monitor emits node gpu_count=N on every device; keep-dev + MAX → N not N²."""
  rows = []
  for d in ("0", "1", "2", "3"):
    rows.append({
        "host": "h", "type": "nvidia_gpu", "dev": d,
        "event": "gpu_count", "unit": "#", "time": 100.0, "value": 4.0, "wid": 48, "mult": 1,
    })
  result = compute_deltas_and_arc(pd.DataFrame(rows))
  assert len(result) == 4
  assert all(float(v) == 4.0 for v in result["value"])


def test_collapse_nvidia_gpu_count_max_when_dev_collapsed():
  """Defense: same empty-dev identity with duplicated gpu_count uses MAX."""
  rows = [
      {
          "host": "h", "type": "nvidia_gpu", "dev": "",
          "event": "gpu_count", "unit": "#", "time": 100.0, "value": 4.0, "delta": 0.0,
      }
      for _ in range(4)
  ]
  collapsed = _collapse_nvidia_gpu_vectorized(
      pd.DataFrame(rows), _COLLAPSE_GROUP_COLS_WITH_DEV)
  assert len(collapsed) == 1
  assert float(collapsed.iloc[0]["value"]) == 4.0


def _collapse_compare_columns():
  return ["host", "type", "dev", "event", "unit", "time", "value", "delta"]


def _assert_collapse_frames_equal(actual, expected):
  cols = _collapse_compare_columns()
  for frame in (actual, expected):
    if "dev" not in frame.columns:
      frame["dev"] = ""
    else:
      frame["dev"] = frame["dev"].fillna("").astype(str)
  actual_sorted = actual[cols].sort_values(by=cols).reset_index(drop=True)
  expected_sorted = expected[cols].sort_values(by=cols).reset_index(drop=True)
  pd.testing.assert_frame_equal(actual_sorted, expected_sorted, check_dtype=False)


def _nvidia_multi_event_fixture():
  """Post-delta rows spanning NVIDIA event classes and multiple devs."""
  return pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": 40.0, "delta": 4.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": 50.0, "delta": 5.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "temperature", "unit": "C", "time": 100.0, "value": 60.0, "delta": 1.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "temperature", "unit": "C", "time": 100.0, "value": 80.0, "delta": 3.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "module_power_usage", "unit": "W", "time": 100.0, "value": 400.0, "delta": 10.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "module_power_usage", "unit": "W", "time": 100.0, "value": 500.0, "delta": 20.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0, "value": 5.0, "delta": 1.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0, "value": 2.0, "delta": 2.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "custom_metric", "unit": "#", "time": 100.0, "value": 3.0, "delta": 1.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "custom_metric", "unit": "#", "time": 100.0, "value": 7.0, "delta": 2.0,
      },
  ])


def _mixed_collapse_fixture():
  """Post-delta rows for rest, DCGM, and NVIDIA collapse routes."""
  rows = []
  rows.extend([
      {
          "host": "h", "type": "host_cpu", "dev": "global",
          "event": "user", "unit": "#", "time": 100.0, "value": 10.0, "delta": 1.0,
      },
      {
          "host": "h", "type": "host_cpu", "dev": "global",
          "event": "user", "unit": "#", "time": 100.0, "value": 20.0, "delta": 2.0,
      },
  ])
  for idx, val in enumerate([100.0, 100.0, 100.0, 80.0, 80.0, 80.0]):
    rows.append({
        "host": "h",
        "type": "cpu_counter_metrics",
        "dev": str(idx),
        "event": "DCGM_CPU_POWER_UTIL_W",
        "unit": "W",
        "time": 100.0,
        "value": val,
        "delta": float(idx + 1),
    })
  rows.extend(_nvidia_multi_event_fixture().to_dict("records"))
  return pd.DataFrame(rows)


def test_collapse_nvidia_gpu_vectorized_matches_apply_reference():
  nv_df = _nvidia_multi_event_fixture()
  gcols = _COLLAPSE_GROUP_COLS_WITH_DEV
  expected = (
      nv_df.groupby(gcols, observed=True)
      .apply(_collapse_nvidia_gpu_group, include_groups=False)
      .reset_index()
  )
  actual = _collapse_nvidia_gpu_vectorized(nv_df, gcols)
  _assert_collapse_frames_equal(actual, expected)


def test_collapse_dcg_cpu_power_vectorized_matches_apply_reference():
  rows = []
  for idx, val in enumerate([100.0, 100.0, 100.0, 80.0, 80.0, 80.0]):
    rows.append({
        "host": "h",
        "type": "cpu_counter_metrics",
        "dev": str(idx),
        "event": "DCGM_CPU_POWER_UTIL_W",
        "unit": "W",
        "time": 100.0,
        "value": val,
        "delta": float((idx + 1) * 10),
    })
  ccm_df = pd.DataFrame(rows)
  gcols = _COLLAPSE_GROUP_COLS
  expected = (
      ccm_df.groupby(gcols, observed=True)
      .apply(_collapse_dcg_cpu_power_gauge_group, include_groups=False)
      .reset_index()
  )
  expected["dev"] = ""
  actual = _collapse_dcg_cpu_power_vectorized(ccm_df, gcols)
  actual["dev"] = ""
  _assert_collapse_frames_equal(actual, expected)


def test_collapse_stats_with_deltas_vectorized_matches_apply_reference():
  stats_df = _mixed_collapse_fixture()
  actual = _collapse_stats_with_deltas(stats_df.copy())

  gcols = _COLLAPSE_GROUP_COLS
  gcols_gpu = _COLLAPSE_GROUP_COLS_WITH_DEV
  parts = []
  from hpcperfstats.dbload.lib import sync_timedb_parsing as parsing

  rest_df = stats_df[~stats_df["type"].isin(parsing._GPU_STATS_TYPES)]
  ccm_power_mask = (
      rest_df["type"].isin(parsing._HOST_CPU_HW_TYPES)
      & rest_df["event"].isin(parsing._DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS))
  ccm_power_df = rest_df[ccm_power_mask]
  rest_other = rest_df[~ccm_power_mask]
  if not rest_other.empty:
    part = rest_other.groupby(gcols, observed=True).sum(min_count=1).reset_index()
    part["dev"] = ""
    parts.append(part)
  if not ccm_power_df.empty:
    part = (
        ccm_power_df.groupby(gcols, observed=True)
        .apply(_collapse_dcg_cpu_power_gauge_group, include_groups=False)
        .reset_index()
    )
    part["dev"] = ""
    parts.append(part)
  other_gpu = stats_df[stats_df["type"].isin({"amd_gpu", "intel_gpu"})]
  if not other_gpu.empty:
    parts.append(
        other_gpu.groupby(gcols_gpu, observed=True).sum(min_count=1).reset_index()
    )
  nv_df = stats_df[stats_df["type"] == "nvidia_gpu"]
  if not nv_df.empty:
    parts.append(
        nv_df.groupby(gcols_gpu, observed=True)
        .apply(_collapse_nvidia_gpu_group, include_groups=False)
        .reset_index()
    )
  expected = pd.concat(parts, ignore_index=True)
  _assert_collapse_frames_equal(actual, expected)


def test_compute_deltas_and_arc_nvidia_unknown_event_keeps_dev():
  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "custom_gpu_metric", "unit": "#", "time": 100.0, "value": 12.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "custom_gpu_metric", "unit": "#", "time": 100.0, "value": 18.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  by_dev = {str(r["dev"]): float(r["value"]) for _, r in result.iterrows()}
  assert by_dev == {"0": 12.0, "1": 18.0}


def test_compute_deltas_and_arc_nvidia_sum_all_nan_yields_nan():
  """All-NaN NVIDIA sum groups collapse to NaN (min_count=1), not zero."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": float("nan"), "delta": float("nan"),
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": float("nan"), "delta": float("nan"),
      },
  ])
  collapsed = _collapse_nvidia_gpu_vectorized(
      stats_df, _COLLAPSE_GROUP_COLS_WITH_DEV)
  assert len(collapsed) == 2
  assert all(pd.isna(v) for v in collapsed["value"])
  assert pd.isna(collapsed.iloc[0]["delta"])


def test_collapse_nvidia_gpu_excludes_dcgm_blanks_from_sum():
  """DCGM blank on one device is NaN'd; other devices keep real watts (keep-dev)."""
  from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "power_usage", "unit": "W", "time": 100.0,
          "value": DCGM_FP64_BLANK, "delta": 1.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "power_usage", "unit": "W", "time": 100.0,
          "value": 250.0, "delta": 1.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "2",
          "event": "power_usage", "unit": "W", "time": 100.0,
          "value": 300.0, "delta": 1.0,
      },
  ])
  gcols = _COLLAPSE_GROUP_COLS_WITH_DEV
  collapsed = _collapse_nvidia_gpu_vectorized(stats_df, gcols)
  assert len(collapsed) == 3
  by_dev = {
      str(r["dev"]): r["value"] for _, r in collapsed.iterrows()
  }
  assert pd.isna(by_dev["0"])
  assert float(by_dev["1"]) == 250.0
  assert float(by_dev["2"]) == 300.0
  expected = (
      stats_df.groupby(gcols, observed=True)
      .apply(_collapse_nvidia_gpu_group, include_groups=False)
      .reset_index()
  )
  _assert_collapse_frames_equal(collapsed, expected)


def test_collapse_nvidia_gpu_or_skips_dcgm_int64_blank():
  from hpcperfstats.lib.dcgm_blank import DCGM_INT64_BLANK

  stats_df = pd.DataFrame([
      {
          "host": "h", "type": "nvidia_gpu", "dev": "0",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0,
          "value": float(DCGM_INT64_BLANK), "delta": 1.0,
      },
      {
          "host": "h", "type": "nvidia_gpu", "dev": "1",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0,
          "value": 7.0, "delta": 1.0,
      },
  ])
  gcols = _COLLAPSE_GROUP_COLS_WITH_DEV
  collapsed = _collapse_nvidia_gpu_vectorized(stats_df, gcols)
  assert len(collapsed) == 2
  by_dev = {str(r["dev"]): float(r["value"]) for _, r in collapsed.iterrows()}
  # Blank device OR of empty set → 0; real device keeps mask.
  assert by_dev["0"] == 0.0
  assert by_dev["1"] == 7.0


def test_host_data_instance_from_stats_row_sets_jid_when_present():
  row_type = namedtuple(
      "Row",
      ["time", "host", "jid", "type", "event", "unit", "value", "delta", "arc"],
  )
  row = row_type(
      time=pd.Timestamp("2026-04-22T12:00:00Z"),
      host="node001.demo.cluster.local",
      jid="pipeline_e2e_j01",
      type="cpu",
      event="user",
      unit="cs",
      value=10.0,
      delta=1.0,
      arc=0.5,
  )
  obj = host_data_instance_from_stats_row(row)
  assert obj.jid == "pipeline_e2e_j01"


def test_host_data_instance_from_stats_row_omits_placeholder_jid():
  row_type = namedtuple(
      "Row",
      ["time", "host", "jid", "type", "event", "unit", "value", "delta", "arc"],
  )
  row = row_type(
      time=pd.Timestamp("2026-04-22T12:00:00Z"),
      host="node001.demo.cluster.local",
      jid="-",
      type="cpu",
      event="user",
      unit="cs",
      value=10.0,
      delta=1.0,
      arc=0.5,
  )
  obj = host_data_instance_from_stats_row(row)
  assert obj.jid is None


def test_sync_timedb_parsing_with_real_sample_produces_deltas_and_arc():
  """Use HPCPerfStatsdDataSample to validate real parsing + delta/arc computation."""
  sample_path = os.path.abspath(
      os.path.join(
          os.path.dirname(os.path.realpath(__file__)),
          "..",
          "dbload",
          "tests",
          "HPCPerfStatsdDataSample",
      )
  )

  lines, load_err = load_stats_file_lines(sample_path)
  assert load_err is None
  assert lines

  # Sanity check that the sample includes multiple timestamp snapshots so
  # compute_deltas_and_arc should have usable diffs for at least some groups.
  digit_start_count = sum(1 for l in lines if l and l[0].isdigit())
  assert digit_start_count >= 2

  first_t, first_jid, first_host = parse_first_timestamp_line(lines)
  assert first_t is not None
  assert first_jid is not None
  assert first_host is not None
  assert first_host == "c571-001.stampede3.tacc.utexas.edu"

  start_idx, need_archival = find_processing_start_index(lines, set())
  assert start_idx >= 0
  assert need_archival is True

  sliced_lines = lines[start_idx:]
  stats_list, proc_stats_list = parse_stats_lines(
      sliced_lines,
      0,
      eventmaps_by_type=EVENTMAPS_BY_TYPE,
      exclude_types_list=exclude_types,
  )
  assert stats_list

  stats_df, proc_df = build_stats_dataframes(stats_list, proc_stats_list)
  assert not stats_df.empty
  assert "delta" not in stats_df.columns

  deltas_df = compute_deltas_and_arc(stats_df)
  assert not deltas_df.empty
  assert {"delta", "arc", "time", "host"}.issubset(deltas_df.columns)
  assert "jid" not in deltas_df.columns


def test_parse_stats_file_streaming_matches_readlines_path(tmp_path):
  """Streaming parse must match in-memory parse for the same fixture."""
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  body = (
      "1709123456 job1 host.example.com\n"
      "!cpu user sys\n"
      "1709123457 job1 host.example.com\n"
      "cpu 0 100 200\n"
  )
  stats_file.write_text(body, encoding="utf-8")
  lines, err = load_stats_file_lines(str(stats_file))
  assert err is None
  expected_stats, expected_proc = parse_stats_lines(lines, 0)
  stream_stats, stream_proc = parse_stats_file_streaming(str(stats_file))
  assert stream_stats == expected_stats
  assert stream_proc == expected_proc


def _resume_schema_fixture_lines():
  """Header schema before resume offset; later samples must still emit stats."""
  return [
      "1709123456 job1 host.example.com\n",
      "!cpu user\n",
      "cpu 0 100\n",
      "1709123457 job1 host.example.com\n",
      "cpu 0 200\n",
      "1709123458 job1 host.example.com\n",
      "cpu 0 300\n",
      "proc usr/bin/foo 1000 1 2 3 4 5 6 7 8 9 10 11 12\n",
  ]


def test_streaming_resume_registers_schema_from_skipped_header(tmp_path):
  """RC-0: resume past !schema must still emit hardware stats rows."""
  lines = _resume_schema_fixture_lines()
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("".join(lines), encoding="utf-8")
  # Offset 3 is the second timestamp line — past the !cpu schema.
  start_idx = 3
  expected_stats, expected_proc = parse_stats_lines(lines, start_idx)
  assert len(expected_stats) > 0
  stream_stats, stream_proc = parse_stats_file_streaming(
      str(stats_file),
      start_line_idx=start_idx,
  )
  assert len(stream_stats) > 0
  assert stream_stats == expected_stats
  assert stream_proc == expected_proc


def test_streaming_resume_matches_nonstreaming_parse(tmp_path):
  """Both streaming entry points match parse_stats_lines for every resume offset."""
  lines = _resume_schema_fixture_lines()
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("".join(lines), encoding="utf-8")
  for start_idx in range(len(lines) + 1):
    expected_stats, expected_proc = parse_stats_lines(lines, start_idx)
    stream_stats, stream_proc = parse_stats_file_streaming(
        str(stats_file),
        start_line_idx=start_idx,
    )
    assert stream_stats == expected_stats, "streaming start_idx=%d" % start_idx
    assert stream_proc == expected_proc, "streaming proc start_idx=%d" % start_idx
    chunks = []

    def on_chunk(stats_list, proc_list):
      chunks.append((list(stats_list), list(proc_list)))

    parse_stats_file_streaming_incremental(
        str(stats_file),
        start_line_idx=start_idx,
        flush_rows=10_000,
        on_chunk=on_chunk,
    )
    inc_stats = [row for stats, _proc in chunks for row in stats]
    inc_proc = [row for _stats, proc in chunks for row in proc]
    assert inc_stats == expected_stats, "incremental start_idx=%d" % start_idx
    assert inc_proc == expected_proc, "incremental proc start_idx=%d" % start_idx


def test_streaming_resume_emits_nothing_before_start_idx(tmp_path):
  """Feeding the prefix must not emit samples before the resume offset."""
  lines = _resume_schema_fixture_lines()
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("".join(lines), encoding="utf-8")
  start_idx = 3
  expected_stats, _ = parse_stats_lines(lines, start_idx)
  full_stats, _ = parse_stats_lines(lines, 0)
  assert len(full_stats) > len(expected_stats)
  stream_stats, _ = parse_stats_file_streaming(
      str(stats_file),
      start_line_idx=start_idx,
  )
  assert stream_stats == expected_stats
  times = {row["time"] for row in stream_stats}
  assert 1709123456.0 not in times


def test_zero_host_mark_not_recorded_when_stats_lines_parsed(monkeypatch, tmp_path):
  """Parsed stats > 0 with zero written rows must not bypass head+tail via mark."""
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib import sync_timedb_zero_host_ingest_mark as zhm

  recorded = []

  def fake_record(path, **kwargs):
    recorded.append(path)
    return True

  monkeypatch.setattr(zhm, "record_zero_host_ingest_mark", fake_record)
  outcome = st.IngestFileOutcome(
      path=str(tmp_path / "seg"),
      elapsed_s=1.0,
      ingest_ok=True,
      need_archival=True,
      outcome="ingested",
      stats_rows=0,
      stats_rows_parsed=12,
      proc_rows=3,
  )
  st._log_ingest_file_outcome(outcome)
  zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      outcome.path,
      ingest_ok=outcome.ingest_ok,
      outcome=outcome.outcome,
      stats_rows=outcome.stats_rows,
      stats_rows_parsed=outcome.stats_rows_parsed,
  )
  assert recorded == []


def test_nonempty_stats_frame_collapsing_to_empty_delta_logs_warning():
  """Non-empty frame missing required delta cols must warn, not fail silently."""
  import warnings

  df = pd.DataFrame(
      [
          {
              "time": 1.0,
              "host": "h",
              "type": "cpu",
              "dev": "0",
              "event": "user",
              "unit": "",
              "value": 1.0,
          }
      ]
  )
  with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    result = compute_deltas_and_arc_chunk(df, carry=DeltaCarryState())
  assert result.empty
  assert any("collapsed to empty" in str(w.message) for w in caught)


def test_stats_file_size_bytes_reads_file(tmp_path):
  stats_file = tmp_path / "seg"
  stats_file.write_text("x", encoding="utf-8")
  assert stats_file_size_bytes(str(stats_file)) == 1
