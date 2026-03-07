"""Unit tests for sync_timedb parsing and helper functions (no DB; uses sync_timedb_parsing to avoid Django)."""
import pandas as pd
import pytest

from hpcperfstats.dbload.sync_timedb_parsing import (
    build_stats_dataframes,
    compute_deltas_and_arc,
    find_processing_start_index,
    load_stats_file_lines,
    map_hardware_counter_vals,
    parse_first_timestamp_line,
    parse_stats_file_path,
    parse_stats_lines,
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
  assert need_archival is False


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
  assert need_archival is False


def test_find_processing_start_index_skips_job_missing():
  """Lines with jid '-' are skipped and do not update last_idx."""
  lines = [
      "1709123456 - cn001\n",
      "1709123460 job1 cn001\n",
  ]
  itimes_set = set()
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  # First line has jid '-', so last_idx stays 0; second line is first "valid" and missing from times
  assert start_idx == 0
  assert need_archival is False


# --- map_hardware_counter_vals ---


def test_map_hardware_counter_vals_fixed_ctr():
  """FIXED_CTR entries use eventmap and keep value position."""
  schema_events = ["FIXED_CTR0,W=48", "FIXED_CTR1,W=48"]
  eventmap = {"FIXED_CTR0": "INST_RETIRED,W=48", "FIXED_CTR1": "APERF,W=48"}
  vals = [100, 200]
  result = map_hardware_counter_vals("intel_8pmc3", schema_events, vals, eventmap)
  assert result["INST_RETIRED,W=48"] == 100
  assert result["APERF,W=48"] == 200


def test_map_hardware_counter_vals_ctl_ctr():
  """CTL maps to event name, CTR uses that name and gets value."""
  schema_events = ["CTL0", "CTR0"]
  eventmap = {0: "EVENT_A,W=48", 1: "EVENT_B,W=48"}
  vals = [0, 100]  # CTL value 0 -> EVENT_A, CTR value 100
  result = map_hardware_counter_vals("amd64_pmc", schema_events, vals, eventmap)
  assert result["EVENT_A,W=48"] == 100


def test_map_hardware_counter_vals_plain():
  """Plain event names pass through; schema_events first token is used as key."""
  schema_events = ["EV1,W=48", "EV2,W=48"]
  eventmap = {}
  vals = [10, 20]
  result = map_hardware_counter_vals("other", schema_events, vals, eventmap)
  assert result["EV1"] == 10
  assert result["EV2"] == 20


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


def test_parse_stats_lines_proc_type():
  """proc type lines add to proc_stats only (proc name is first path component)."""
  lines = [
      "1709123456 job1 cn001\n",
      "proc usr/bin/foo 0\n",
  ]
  start_idx = 0
  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  assert len(stats_list) == 0
  assert len(proc_list) == 1
  assert proc_list[0]["proc"] == "usr"
  assert proc_list[0]["jid"] == "job1"


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
  """Duplicate proc_stats entries are deduplicated."""
  stats_list = [
      {"time": 1.0, "host": "h", "jid": "j", "type": "cpu", "dev": "0", "event": "a", "value": 1.0, "wid": 64, "mult": 1, "unit": "#"},
  ]
  proc_list = [
      {"jid": "j", "host": "h", "proc": "p"},
      {"jid": "j", "host": "h", "proc": "p"},
  ]
  stats_df, proc_df = build_stats_dataframes(stats_list, proc_list)
  assert len(stats_df) == 1
  assert len(proc_df) == 1
  assert proc_df.iloc[0]["proc"] == "p"


def test_build_stats_dataframes_records():
  """Stats list becomes DataFrame with expected columns."""
  stats_list = [
      {"time": 1.0, "host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "value": 10.0, "wid": 48, "mult": 1, "unit": "#"},
  ]
  stats_df, _ = build_stats_dataframes(stats_list, [])
  assert "value" in stats_df.columns
  assert stats_df.iloc[0]["value"] == 10.0


# --- compute_deltas_and_arc ---


def test_compute_deltas_and_arc_two_timestamps():
  """Two timestamps for same group yield one row with delta and arc."""
  stats_df = pd.DataFrame([
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 100.0, "wid": 48, "mult": 1},
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 250.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  assert result.iloc[0]["delta"] == 150.0
  assert result.iloc[0]["arc"] == 15.0


def test_compute_deltas_and_arc_rollover():
  """Negative delta is corrected for 48-bit rollover."""
  stats_df = pd.DataFrame([
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 1000.0, "wid": 48, "mult": 1},
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 10.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  expected_delta = (2**48 - 1000 + 10) * 1
  assert result.iloc[0]["delta"] == expected_delta


def test_compute_deltas_and_arc_drops_first_timestamp():
  """First timestamp per group has no diff, so that row is dropped."""
  stats_df = pd.DataFrame([
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 100.0, "wid": 48, "mult": 1},
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 200.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  assert "arc" in result.columns
  assert "delta" in result.columns
  assert "time" in result.columns
