"""Unit tests for sync_timedb parsing and helper functions (no DB; uses sync_timedb_parsing to avoid Django)."""
import os
import pandas as pd
import pytest

from hpcperfstats.dbload.sync_timedb_parsing import (
    EVENTMAPS_BY_TYPE,
    build_stats_dataframes,
    compute_deltas_and_arc,
    exclude_types,
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


def test_find_processing_start_index_with_leading_spaces():
  """Leading whitespace before timestamps should not prevent detection."""
  lines = [
      "   1709123456 job1 cn001\n",
      "   1709123460 job1 cn001\n",
  ]
  itimes_set = set()
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  assert start_idx == 0
  assert need_archival is False


def test_find_processing_start_index_includes_job_missing():
  """Lines with jid '-' still drive processing start detection."""
  lines = [
      "1709123456 - cn001\n",
      "1709123460 - cn001\n",
  ]
  itimes_set = set()
  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
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


def test_parse_stats_lines_with_missing_jid_keeps_placeholder():
  """When jid is '-', samples are still ingested with jid='-'."""
  lines = [
      "1709123456 - cn001\n",
      "!cpu user sys\n",
      "cpu 0 100 200\n",
  ]
  start_idx = 0
  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  assert len(stats_list) == 2
  assert all(r["jid"] == "-" for r in stats_list)
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
  """Two timestamps: first row keeps value with NaN delta/arc; second has rates."""
  stats_df = pd.DataFrame([
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 100.0, "wid": 48, "mult": 1},
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 250.0, "wid": 48, "mult": 1},
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
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 1000.0, "wid": 48, "mult": 1},
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 10.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  expected_delta = (2**48 - 1000 + 10) * 1
  assert pd.isna(result.iloc[0]["delta"])
  assert result.iloc[1]["delta"] == expected_delta


def test_compute_deltas_and_arc_keeps_first_timestamp_value():
  """First timestamp per group has NaN delta/arc but value is kept for complex metrics."""
  stats_df = pd.DataFrame([
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 100.0, "value": 100.0, "wid": 48, "mult": 1},
      {"host": "h", "jid": "j", "type": "t", "dev": "d", "event": "e", "unit": "#", "time": 110.0, "value": 200.0, "wid": 48, "mult": 1},
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 2
  assert "arc" in result.columns
  assert "delta" in result.columns
  assert "time" in result.columns
  assert result.iloc[0]["value"] == 100.0
  assert pd.isna(result.iloc[0]["arc"])


def test_compute_deltas_and_arc_nvidia_gpu_util_sums_across_dev():
  """nvidia_gpu gpu_util: same timestamp two devs -> value is sum (per plan)."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "jid": "j", "type": "nvidia_gpu", "dev": "0",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": 40.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "jid": "j", "type": "nvidia_gpu", "dev": "1",
          "event": "gpu_util", "unit": "#", "time": 100.0, "value": 50.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  assert result.iloc[0]["value"] == 90.0


def test_compute_deltas_and_arc_nvidia_temperature_means_across_dev():
  """nvidia_gpu temperature: two devs -> mean value and mean delta."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "jid": "j", "type": "nvidia_gpu", "dev": "0",
          "event": "temperature", "unit": "C", "time": 100.0, "value": 60.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "jid": "j", "type": "nvidia_gpu", "dev": "1",
          "event": "temperature", "unit": "C", "time": 100.0, "value": 80.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  assert result.iloc[0]["value"] == 70.0


def test_compute_deltas_and_arc_nvidia_clocks_event_reasons_bitwise_or():
  """nvidia_gpu clocks_event_reasons: OR across devs."""
  stats_df = pd.DataFrame([
      {
          "host": "h", "jid": "j", "type": "nvidia_gpu", "dev": "0",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0, "value": 5.0, "wid": 48, "mult": 1,
      },
      {
          "host": "h", "jid": "j", "type": "nvidia_gpu", "dev": "1",
          "event": "clocks_event_reasons", "unit": "#", "time": 100.0, "value": 2.0, "wid": 48, "mult": 1,
      },
  ])
  result = compute_deltas_and_arc(stats_df)
  assert len(result) == 1
  assert int(result.iloc[0]["value"]) == 7


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
  assert need_archival is False

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
  assert {"delta", "arc", "time", "host", "jid"}.issubset(deltas_df.columns)
