"""Regression tests for incremental large-segment ingest (parse → DB → parse)."""

from __future__ import annotations

import pandas as pd

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    DeltaCarryState,
    build_stats_dataframes,
    compute_deltas_and_arc,
    compute_deltas_and_arc_chunk,
    parse_stats_file_streaming_incremental,
)


def _counter_fixture_lines(extra_samples=0):
  """Minimal stats lines with monotonic counters across time samples."""
  lines = [
      "1709123456 job1 host.example.com\n",
      "!cpu user\n",
  ]
  value = 1000
  for i in range(1, 2 + extra_samples):
    value += 100
    lines.append("170912345%d job1 host.example.com\n" % (6 + i))
    lines.append("cpu 0 %d\n" % value)
  return lines


def _stats_list_from_lines(lines, start_idx=0):
  from hpcperfstats.dbload.lib.sync_timedb_parsing import parse_stats_lines

  return parse_stats_lines(lines, start_idx)


def test_compute_deltas_and_arc_chunk_matches_full_file():
  lines = _counter_fixture_lines(extra_samples=4)
  full_stats, _proc = _stats_list_from_lines(lines, 0)
  full_df, _ = build_stats_dataframes(full_stats, [])
  expected = compute_deltas_and_arc(full_df)

  mid = len(full_stats) // 2
  chunk1_stats = full_stats[:mid]
  chunk2_stats = full_stats[mid:]
  carry = DeltaCarryState()
  part1_df, _ = build_stats_dataframes(chunk1_stats, [])
  part1 = compute_deltas_and_arc_chunk(part1_df, carry=carry)
  part2_df, _ = build_stats_dataframes(chunk2_stats, [])
  part2 = compute_deltas_and_arc_chunk(part2_df, carry=carry)
  combined = pd.concat([part1, part2], ignore_index=True)
  combined = combined.sort_values(
      by=["host", "type", "event", "time"],
  ).reset_index(drop=True)
  expected = expected.sort_values(
      by=["host", "type", "event", "time"],
  ).reset_index(drop=True)

  pd.testing.assert_frame_equal(
      combined[["host", "type", "event", "delta", "arc"]],
      expected[["host", "type", "event", "delta", "arc"]],
      check_dtype=False,
      rtol=1e-9,
      atol=1e-9,
  )


def test_incremental_parse_flushes_at_time_sample_boundary(tmp_path):
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  body = (
      "1709123456 job1 host.example.com\n"
      "!cpu user sys\n"
      "1709123457 job1 host.example.com\n"
      "cpu 0 100 200\n"
      "cpu 0 110 210\n"
      "1709123458 job1 host.example.com\n"
      "cpu 0 120 220\n"
  )
  stats_file.write_text(body, encoding="utf-8")
  chunks = []

  def on_chunk(stats_list, proc_stats_list):
    chunks.append((list(stats_list), list(proc_stats_list)))

  parse_stats_file_streaming_incremental(
      str(stats_file),
      flush_rows=2,
      on_chunk=on_chunk,
  )
  assert len(chunks) >= 2
  first_times = {row["time"] for row in chunks[0][0]}
  assert len(first_times) == 1


def test_streaming_incremental_combined_path_writes_multiple_chunks(
    monkeypatch, tmp_path,
):
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  lines = _counter_fixture_lines(extra_samples=30)
  stats_file.write_text("".join(lines), encoding="utf-8")

  monkeypatch.setattr(st, "bulk_create_batch_size", lambda: 2)
  monkeypatch.setattr(
      st,
      "_resolve_streaming_ingest_start",
      lambda _path, _elapsed: (False, (0, True)),
  )
  write_calls = []

  def fake_write(_lock, path, stats, proc_stats, need_archival=True):
    write_calls.append((len(stats), len(proc_stats), need_archival))
    return path, need_archival, True

  monkeypatch.setattr(st, "_write_stats_payload_to_db", fake_write)
  monkeypatch.setattr(st, "_release_ingest_worker_heap", lambda: None)

  result = st._add_stats_file_to_db_streaming_incremental(
      object(), str(stats_file), 0.0,
  )
  _path, need_archival, ingest_ok, _elapsed, _meta = st._unpack_ingest_worker_result(
      result,
  )
  assert ingest_ok is True
  assert need_archival is True
  assert len(write_calls) >= 2


def test_small_file_combined_path_unchanged(monkeypatch, tmp_path):
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text(
      "1709123456 job1 host.example.com\n!cpu user\n1709123457 job1 host.example.com\ncpu 0 100\n",
      encoding="utf-8",
  )
  monkeypatch.setattr(st, "_should_stream_stats_file", lambda _p, _c: False)
  parse_calls = {"n": 0}

  def fake_parse(*args, **kwargs):
    parse_calls["n"] += 1
    return (
        str(stats_file),
        (pd.DataFrame(), pd.DataFrame()),
        True,
        True,
        0.1,
    )

  monkeypatch.setattr(st, "_parse_stats_file_payload", fake_parse)
  monkeypatch.setattr(
      st,
      "_write_stats_payload_to_db",
      lambda *_a, **_k: (str(stats_file), True, True),
  )

  st._add_stats_file_to_db_impl(object(), str(stats_file))
  assert parse_calls["n"] == 1
