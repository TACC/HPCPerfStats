"""Regression tests for incremental large-segment ingest (parse -> DB -> parse)."""

from __future__ import annotations

import inspect
import threading

import numpy as np
import pandas as pd

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  _ARC_GROUP_COLS,
  _COUNTER_GROUP_COLS,
  DeltaCarryState,
  _apply_arc_and_finalize,
  _apply_counter_deltas,
  build_stats_dataframes,
  compute_deltas_and_arc,
  compute_deltas_and_arc_chunk,
  parse_stats_file_streaming_incremental,
  stats_payload_to_records,
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
    lines.append(f"170912345{6 + i} job1 host.example.com\n")
    lines.append(f"cpu 0 {value}\n")
  return lines


def _stats_list_from_lines(lines, start_idx=0):
  from hpcperfstats.dbload.lib.sync_timedb_parsing import parse_stats_lines

  return parse_stats_lines(lines, start_idx)


def _apply_counter_deltas_rowwise_ref(stats_df, carry=None):
  """Frozen pre-RC-4 row-wise reference for equivalence tests."""
  stats_df = stats_df.sort_values(by=[*_COUNTER_GROUP_COLS, "time"]).copy()
  stats_df["delta"] = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True)[
    "value"
  ].diff()

  if carry is not None and carry.raw:
    first_rows = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).head(1)
    for idx, row in first_rows.iterrows():
      key = (row["host"], row["type"], row["dev"], row["event"])
      prev = carry.raw.get(key)
      if prev is None:
        continue
      delta = float(row["value"]) - float(
        prev[0] if isinstance(prev, tuple) else prev["value"]
      )
      stats_df.at[idx, "delta"] = delta

  delta = stats_df["delta"].to_numpy(dtype=np.float64, copy=True)
  values = stats_df["value"].to_numpy(dtype=np.float64, copy=False)
  wid = stats_df["wid"].to_numpy(dtype=np.float64, copy=False)
  finite_neg = (delta < 0) & np.isfinite(delta)
  if finite_neg.any():
    modulus = np.power(2.0, wid)
    prev = values - delta
    one_wrap = (
      finite_neg
      & np.isfinite(prev)
      & (prev >= (modulus * 0.5))
      & (delta > -modulus)
    )
    delta[one_wrap] = modulus[one_wrap] + delta[one_wrap]
    delta[finite_neg & ~one_wrap] = np.nan
    stats_df["delta"] = delta
  stats_df["delta"] = stats_df["delta"] * stats_df["mult"]

  if carry is not None:
    for key, group in stats_df.groupby(_COUNTER_GROUP_COLS, observed=True):
      last = group.iloc[-1]
      carry.raw[key] = (
        float(last["value"]),
        int(last["wid"]),
        float(last["mult"]),
        float(last["time"]),
      )

  stats_df.drop(columns=["wid", "mult"], inplace=True)
  return stats_df


def _apply_arc_and_finalize_rowwise_ref(stats_df, carry=None):
  """Frozen pre-RC-4 row-wise reference for equivalence tests."""
  deltat = stats_df.groupby(_ARC_GROUP_COLS, observed=True)["time"].diff()
  _dy = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  _dt = deltat.to_numpy(dtype=np.float64, copy=False)
  _arc = np.full(len(stats_df), np.nan, dtype=np.float64)
  _ok = (_dt > 0) & np.isfinite(_dt)
  np.divide(_dy, _dt, out=_arc, where=_ok)

  if carry is not None and carry.arc:
    first_rows = stats_df.groupby(_ARC_GROUP_COLS, observed=True).head(1)
    for idx, row in first_rows.iterrows():
      key = (
        row["host"],
        row["type"],
        str(row.get("dev") or ""),
        row["event"],
      )
      prev = carry.arc.get(key)
      if prev is None:
        continue
      dt = float(row["time"]) - float(
        prev["time"] if isinstance(prev, dict) else prev
      )
      if dt > 0 and np.isfinite(row["delta"]):
        _arc[stats_df.index.get_loc(idx)] = float(row["delta"]) / dt

  stats_df = stats_df.copy()
  stats_df["arc"] = _arc

  if carry is not None:
    for key, group in stats_df.groupby(_ARC_GROUP_COLS, observed=True):
      last = group.iloc[-1]
      # Normalize key to (host, type, dev, event) even if groupby key shape varies.
      if isinstance(key, tuple) and len(key) == 4:
        carry_key = (key[0], key[1], str(key[2] or ""), key[3])
      else:
        carry_key = (
          last["host"],
          last["type"],
          str(last.get("dev") or ""),
          last["event"],
        )
      carry.arc[carry_key] = float(last["time"])

  return stats_df.dropna(subset=["host", "type", "event", "time", "value"])


def _counter_rows_with_wrap():
  """Two flushes where the second starts after a counter wrap (wid=8)."""
  base = {
    "host": "h1",
    "type": "cpu",
    "dev": "0",
    "event": "user",
    "unit": "",
    "wid": 8,
    "mult": 1.0,
  }
  flush1 = pd.DataFrame(
    [
      {**base, "time": 10.0, "value": 200.0},
      {**base, "time": 20.0, "value": 250.0},
    ]
  )
  flush2 = pd.DataFrame(
    [
      {**base, "time": 30.0, "value": 10.0},  # wrapped past 255
      {**base, "time": 40.0, "value": 40.0},
    ]
  )
  return flush1, flush2


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
    check_categorical=False,
    rtol=1e-9,
    atol=1e-9,
  )


def test_counter_delta_carry_vectorized_matches_rowwise():
  flush1, flush2 = _counter_rows_with_wrap()
  carry_vec = DeltaCarryState()
  carry_ref = DeltaCarryState()
  out1 = _apply_counter_deltas(flush1.copy(), carry=carry_vec)
  ref1 = _apply_counter_deltas_rowwise_ref(flush1.copy(), carry=carry_ref)
  pd.testing.assert_frame_equal(
    out1.reset_index(drop=True),
    ref1.reset_index(drop=True),
    check_dtype=False,
    check_categorical=False,
  )
  assert carry_vec.raw == carry_ref.raw
  out2 = _apply_counter_deltas(flush2.copy(), carry=carry_vec)
  ref2 = _apply_counter_deltas_rowwise_ref(flush2.copy(), carry=carry_ref)
  pd.testing.assert_frame_equal(
    out2.reset_index(drop=True),
    ref2.reset_index(drop=True),
    check_dtype=False,
    check_categorical=False,
  )
  assert carry_vec.raw == carry_ref.raw
  # Wrapped first-row delta across flush: 10 - 250 + 256 = 16
  first_delta = float(out2.iloc[0]["delta"])
  assert abs(first_delta - 16.0) < 1e-9


def test_arc_carry_vectorized_matches_rowwise():
  flush1, flush2 = _counter_rows_with_wrap()
  carry_vec = DeltaCarryState()
  carry_ref = DeltaCarryState()
  d1 = _apply_counter_deltas(flush1.copy(), carry=carry_vec)
  d1r = _apply_counter_deltas_rowwise_ref(flush1.copy(), carry=carry_ref)
  a1 = _apply_arc_and_finalize(d1.copy(), carry=carry_vec)
  a1r = _apply_arc_and_finalize_rowwise_ref(d1r.copy(), carry=carry_ref)
  pd.testing.assert_frame_equal(
    a1.reset_index(drop=True),
    a1r.reset_index(drop=True),
    check_dtype=False,
    check_categorical=False,
  )
  assert carry_vec.arc == carry_ref.arc
  d2 = _apply_counter_deltas(flush2.copy(), carry=carry_vec)
  d2r = _apply_counter_deltas_rowwise_ref(flush2.copy(), carry=carry_ref)
  a2 = _apply_arc_and_finalize(d2.copy(), carry=carry_vec)
  a2r = _apply_arc_and_finalize_rowwise_ref(d2r.copy(), carry=carry_ref)
  pd.testing.assert_frame_equal(
    a2.reset_index(drop=True),
    a2r.reset_index(drop=True),
    check_dtype=False,
    check_categorical=False,
  )
  assert carry_vec.arc == carry_ref.arc


def test_carry_state_survives_flush_boundary_split():
  lines = _counter_fixture_lines(extra_samples=8)
  full_stats, _ = _stats_list_from_lines(lines, 0)
  full_df, _ = build_stats_dataframes(full_stats, [])
  expected = compute_deltas_and_arc(full_df.copy())
  carry = DeltaCarryState()
  parts = []
  for i in range(0, len(full_stats), 2):
    chunk_df, _ = build_stats_dataframes(full_stats[i : i + 2], [])
    parts.append(compute_deltas_and_arc_chunk(chunk_df, carry=carry))
  combined = (
    pd.concat(parts, ignore_index=True)
    .sort_values(
      by=["host", "type", "event", "time"],
    )
    .reset_index(drop=True)
  )
  expected = expected.sort_values(
    by=["host", "type", "event", "time"],
  ).reset_index(drop=True)
  pd.testing.assert_frame_equal(
    combined[["host", "type", "event", "delta", "arc"]],
    expected[["host", "type", "event", "delta", "arc"]],
    check_dtype=False,
    check_categorical=False,
    rtol=1e-9,
    atol=1e-9,
  )


def test_apply_counter_deltas_carry_mult_matches_full_file():
  """Carry deltas stay unscaled until the single wrap + ``* mult`` pass.

  Chunked ingest and listend share ``_apply_counter_deltas``. Scaling inside
  the carry loop double-applies ``mult`` (legacy ``U=64B``). Full-file
  ``compute_deltas_and_arc`` (no carry) is the golden.
  """
  base = {
    "host": "h1",
    "type": "cpu",
    "dev": "0",
    "event": "cas_count",
    "unit": "B",
    "wid": 8,
    "mult": 64.0,
  }
  flush1 = pd.DataFrame(
    [
      {**base, "time": 10.0, "value": 200.0},
      {**base, "time": 20.0, "value": 250.0},
    ]
  )
  flush2 = pd.DataFrame(
    [
      {**base, "time": 30.0, "value": 10.0},
      {**base, "time": 40.0, "value": 40.0},
    ]
  )
  expected = compute_deltas_and_arc(
    pd.concat([flush1, flush2], ignore_index=True)
  )
  carry = DeltaCarryState()
  part1 = compute_deltas_and_arc_chunk(flush1.copy(), carry=carry)
  part2 = compute_deltas_and_arc_chunk(flush2.copy(), carry=carry)
  combined = (
    pd.concat([part1, part2], ignore_index=True)
    .sort_values(
      by=["host", "type", "event", "time"],
    )
    .reset_index(drop=True)
  )
  expected = expected.sort_values(
    by=["host", "type", "event", "time"],
  ).reset_index(drop=True)
  pd.testing.assert_frame_equal(
    combined[["host", "type", "event", "delta", "arc"]],
    expected[["host", "type", "event", "delta", "arc"]],
    check_dtype=False,
    check_categorical=False,
    rtol=1e-9,
    atol=1e-9,
  )
  wrap_delta = float(combined.loc[combined["time"] == 30.0, "delta"].iloc[0])
  # Unscaled wrap is 10 - 250 + 256 = 16; one ``* 64`` -> 1024, not 65536.
  assert abs(wrap_delta - 1024.0) < 1e-9


def test_apply_counter_deltas_no_rowwise_group_access():
  src = inspect.getsource(_apply_counter_deltas) + inspect.getsource(
    _apply_arc_and_finalize
  )
  assert "iterrows" not in src
  assert "iloc[-1]" not in src
  assert "index.get_loc" not in src
  assert ".at[" not in src


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
    chunks.append(
      (
        stats_payload_to_records(stats_list),
        list(proc_stats_list),
      )
    )

  parse_stats_file_streaming_incremental(
    str(stats_file),
    flush_rows=2,
    on_chunk=on_chunk,
  )
  assert len(chunks) >= 2
  first_times = {row["time"] for row in chunks[0][0]}
  assert len(first_times) == 1


def test_streaming_incremental_combined_path_writes_multiple_chunks(
  monkeypatch,
  tmp_path,
):
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  lines = _counter_fixture_lines(extra_samples=30)
  stats_file.write_text("".join(lines), encoding="utf-8")

  monkeypatch.setattr(st, "_ingest_parse_flush_max_bytes", lambda: 1)
  monkeypatch.setattr(
    st,
    "_resolve_streaming_ingest_start",
    lambda _path, _elapsed: (False, (0, True)),
  )
  write_calls = []

  def fake_write(path, stats, proc_stats, need_archival=True):
    write_calls.append((len(stats), len(proc_stats), need_archival))
    return path, need_archival, True

  monkeypatch.setattr(st, "_write_stats_payload_to_db", fake_write)
  monkeypatch.setattr(st, "_release_ingest_worker_heap", lambda: None)

  result = st._add_stats_file_to_db_streaming_incremental(
    str(stats_file),
    0.0,
  )
  _path, need_archival, ingest_ok, _elapsed, _meta = (
    st._unpack_ingest_worker_result(
      result,
    )
  )
  assert ingest_ok is True
  assert need_archival is True
  assert len(write_calls) >= 2


def _streaming_incremental_with_fake_write(
  monkeypatch,
  tmp_path,
  fake_write,
  *,
  extra_samples=30,
):
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  lines = _counter_fixture_lines(extra_samples=extra_samples)
  stats_file.write_text("".join(lines), encoding="utf-8")
  monkeypatch.setattr(st, "_ingest_parse_flush_max_bytes", lambda: 1)
  monkeypatch.setattr(
    st,
    "_resolve_streaming_ingest_start",
    lambda _path, _elapsed: (False, (0, True)),
  )
  monkeypatch.setattr(st, "_write_stats_payload_to_db", fake_write)
  monkeypatch.setattr(st, "_release_ingest_worker_heap", lambda: None)
  result = st._add_stats_file_to_db_streaming_incremental(
    str(stats_file),
    0.0,
  )
  return lines, result


def test_streaming_incremental_one_chunk_rows_match_serial(
  monkeypatch,
  tmp_path,
):
  """Chunk writes match one-shot delta/arc on the same file."""
  written = []

  def fake_write(path, stats, proc_stats, need_archival=True):
    del proc_stats
    written.append(stats.copy())
    return path, need_archival, True

  lines, result = _streaming_incremental_with_fake_write(
    monkeypatch,
    tmp_path,
    fake_write,
  )
  _path, _need_archival, ingest_ok, _elapsed, _meta = (
    st._unpack_ingest_worker_result(result)
  )
  assert ingest_ok is True
  assert len(written) >= 2
  stats_list, _proc_list = _stats_list_from_lines(lines)
  full_stats, _full_proc = build_stats_dataframes(stats_list, [])
  expected = compute_deltas_and_arc(full_stats)
  combined = (
    pd.concat(written, ignore_index=True)
    .sort_values(by=["host", "type", "event", "time"])
    .reset_index(drop=True)
  )
  expected = expected.sort_values(
    by=["host", "type", "event", "time"],
  ).reset_index(drop=True)
  cols = ["host", "type", "event", "time", "delta", "arc"]
  pd.testing.assert_frame_equal(
    combined[cols],
    expected[cols],
    check_dtype=False,
    check_categorical=False,
    rtol=1e-9,
    atol=1e-9,
  )


def test_streaming_incremental_chunk_write_does_not_overlap_next_parse(
  monkeypatch,
  tmp_path,
):
  """A blocked chunk write finishes before the next chunk's deltas run."""
  state = {
    "writes": 0,
    "deltas": 0,
    "in_write": False,
    "overlapped": False,
    "entered": threading.Event(),
    "release": threading.Event(),
  }
  real_deltas = st.compute_deltas_and_arc_chunk

  def fake_write(path, stats, proc_stats, need_archival=True):
    del stats, proc_stats
    state["writes"] += 1
    if state["writes"] == 1:
      state["in_write"] = True
      state["entered"].set()
      state["release"].wait(1)
      state["in_write"] = False
    return path, need_archival, True

  def wrapped_deltas(stats_chunk, carry=None):
    state["deltas"] += 1
    if state["deltas"] >= 2:
      assert state["entered"].wait(2)
      if state["in_write"]:
        state["overlapped"] = True
      state["release"].set()
    return real_deltas(stats_chunk, carry=carry)

  monkeypatch.setattr(st, "compute_deltas_and_arc_chunk", wrapped_deltas)
  _lines, result = _streaming_incremental_with_fake_write(
    monkeypatch,
    tmp_path,
    fake_write,
  )
  _path, _need_archival, ingest_ok, _elapsed, _meta = (
    st._unpack_ingest_worker_result(result)
  )
  assert ingest_ok is True
  assert state["overlapped"] is False
  assert state["writes"] >= 2


def test_streaming_incremental_chunk_write_failure_skips_later_submits(
  monkeypatch,
  tmp_path,
):
  """A failed chunk write does not submit another write for that file."""
  calls = {"n": 0}

  def fake_write(path, stats, proc_stats, need_archival=True):
    del stats, proc_stats, need_archival
    calls["n"] += 1
    return path, False, False

  _lines, result = _streaming_incremental_with_fake_write(
    monkeypatch,
    tmp_path,
    fake_write,
  )
  _path, _need_archival, ingest_ok, _elapsed, _meta = (
    st._unpack_ingest_worker_result(result)
  )
  assert ingest_ok is False
  assert calls["n"] == 1


def test_streaming_incremental_folds_writer_postgres_s(
  monkeypatch,
  tmp_path,
):
  """Parser outcome postgres_s includes time recorded on the writer thread."""
  st._reset_ingest_write_timing(enabled=True)

  def fake_write(path, stats, proc_stats, need_archival=True):
    del stats, proc_stats
    st._add_ingest_postgres_s(0.25)
    return path, need_archival, True

  try:
    _lines, result = _streaming_incremental_with_fake_write(
      monkeypatch,
      tmp_path,
      fake_write,
      extra_samples=4,
    )
    _path, _need_archival, ingest_ok, _elapsed, meta = (
      st._unpack_ingest_worker_result(result)
    )
    assert ingest_ok is True
    assert float(meta["postgres_s"]) >= 0.25
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_streaming_incremental_chunk_write_stays_on_parser_thread(
  monkeypatch,
  tmp_path,
):
  """Chunk writes run on the parser thread, not ingest-chunk-write."""
  seen = []

  def fake_write(path, stats, proc_stats, need_archival=True):
    del stats, proc_stats
    seen.append(threading.current_thread().name)
    raise RuntimeError("chunk write failed")

  _lines, result = _streaming_incremental_with_fake_write(
    monkeypatch,
    tmp_path,
    fake_write,
    extra_samples=4,
  )
  _path, _need_archival, ingest_ok, _elapsed, _meta = (
    st._unpack_ingest_worker_result(result)
  )
  assert ingest_ok is False
  assert seen
  assert "ingest-chunk-write" not in seen


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

  st._add_stats_file_to_db_impl(str(stats_file))
  assert parse_calls["n"] == 1


def test_ingest_ok_false_when_individual_host_insert_fails():
  """P0-9: individual host insert non-integrity errors must not report ingest_ok."""
  assert (
    st._ingest_ok_from_host_write_path(individual_need_archival=None) is True
  )
  assert (
    st._ingest_ok_from_host_write_path(individual_need_archival=True) is True
  )
  assert (
    st._ingest_ok_from_host_write_path(individual_need_archival=False) is False
  )
  src = inspect.getsource(st._write_stats_payload_to_db)
  assert "_ingest_ok_from_host_write_path" in src
