"""Unit tests for sync_timedb supervisor loop (no real multiprocessing or DB)."""

from collections import deque
from datetime import datetime
from pathlib import Path
import json

import hpcperfstats.dbload.sync_timedb as st
import pandas as pd
import pytest
from hpcperfstats.shutdown_utils import shutdown_requested


class _FakeIngestPool:
  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    return False

  def imap_unordered(self, fn, chunk):
    del fn
    for path in chunk:
      yield (path, False)


class _FakeFailedIngestPool:
  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    return False

  def imap_unordered(self, fn, chunk):
    del fn
    for path in chunk:
      yield (path, False, False)


class _FakeArchivePool:
  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    return False

  def map_async(self, fn, items):
    del fn, items

    class _R:
      def get(self):
        return None

    return _R()


class _FakeArchivePoolPending:
  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    return False

  def map_async(self, fn, items):
    del fn, items

    class _R:
      def get(self):
        return None

    return _R()


class _FakeArchivePoolRetry:
  def __init__(self):
    self.calls = 0

  def map_async(self, fn, items):
    del fn, items
    self.calls += 1
    result = [False] if self.calls == 1 else [True]

    class _R:
      def __init__(self, value):
        self.value = value

      def get(self):
        return self.value

    return _R(result)


def test_parse_sync_timedb_argv_defaults_include_current_day(monkeypatch):
  """Default end date should include current day time, not midnight-only."""
  class _FakeDateTime(datetime):
    @classmethod
    def today(cls):
      return cls(2026, 4, 14, 10, 30, 45)

  monkeypatch.setattr(st, "datetime", _FakeDateTime)
  run_once, startdate, enddate = st.parse_sync_timedb_argv(["sync_timedb.py"])
  assert run_once is False
  assert startdate == datetime(2026, 4, 14, 0, 0, 0) - st.timedelta(days=st.days_to_process)
  assert enddate == datetime(2026, 4, 14, 10, 30, 45)


def test_parse_sync_timedb_argv_once_and_all(monkeypatch):
  class _FakeDateTime(datetime):
    @classmethod
    def today(cls):
      return cls(2026, 4, 14, 10, 30, 45)

  monkeypatch.setattr(st, "datetime", _FakeDateTime)
  run_once, startdate, enddate = st.parse_sync_timedb_argv(
      ["sync_timedb.py", "once", "all"]
  )
  assert run_once is True
  assert startdate == "all"
  assert enddate is None


def test_supervisor_sleeps_once_then_exits_after_empty_full_rescan(monkeypatch):
  shutdown_requested[0] = False
  try:
    rescans = deque([["/fake/stats0"], []])

    def fake_rescan(*a, **k):
      if rescans:
        return list(rescans.popleft())
      return []

    sleeps = []
    final_maintenance = {"calls": 0}

    def fake_sleep(secs):
      sleeps.append(secs)

    def fake_get_context(name):
      assert name == "spawn"

      class _Ctx:
        def Pool(self, processes=None):
          del processes
          return _FakeIngestPool()

      return _Ctx()

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", fake_sleep)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(
        st,
        "seal_dirty_daily_archives",
        lambda *a, **k: final_maintenance.__setitem__(
            "calls", final_maintenance["calls"] + 1),
    )
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st.multiprocessing, "get_context", fake_get_context)
    monkeypatch.setattr(
        st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          "/tmp/archive",
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert sleeps == [st.EMPTY_QUEUE_RESCAN_SLEEP_SECONDS]
    assert final_maintenance["calls"] == 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_run_once_exits_without_idle_sleep_when_empty(monkeypatch):
  """``run_once=True`` must not call the 300s empty-queue sleep."""
  shutdown_requested[0] = False
  try:

    def fake_rescan(*a, **k):
      return []

    sleeps = []

    def fake_sleep(secs):
      sleeps.append(secs)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", fake_sleep)
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          "/tmp/archive",
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
          run_once=True,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert sleeps == []
  finally:
    shutdown_requested[0] = False


def test_supervisor_logs_queue_watermarks(monkeypatch):
  shutdown_requested[0] = False
  try:
    logs = []

    monkeypatch.setattr(st, "rescan_pending_stats_files", lambda *a, **k: [])
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_queue_max_size", lambda: 10)
    monkeypatch.setattr(st.cfg, "get_sync_archive_queue_max_size", lambda: 8)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          "/tmp/archive",
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
          run_once=True,
      )
    finally:
      archive_pool.__exit__(None, None, None)
  finally:
    shutdown_requested[0] = False

  assert any("Queue watermarks ingest" in line for line in logs)


def test_checkpoint_round_trip_persists_completed_entries(tmp_path):
  """Completed file metadata should survive restart round-trip."""
  state_path = Path(tmp_path) / "sync_timedb_state.json"
  completed = [
      {"path": "/a/1", "size": 10, "mtime": 1000},
      {"path": "/b/2", "size": 20, "mtime": 2000},
  ]

  st._save_sync_checkpoint(state_path, completed)
  loaded = st._load_sync_checkpoint(state_path)

  assert loaded == completed


def test_checkpoint_load_ignores_invalid_shape(tmp_path):
  """Corrupt checkpoint content should not crash startup."""
  state_path = Path(tmp_path) / "sync_timedb_state.json"
  state_path.write_text(json.dumps({"bad": "shape"}))

  loaded = st._load_sync_checkpoint(state_path)
  assert loaded == []


def test_failed_ingest_is_not_marked_processed(monkeypatch):
  """Failed ingest must remain eligible on the next rescan."""
  shutdown_requested[0] = False
  try:
    path = "/fake/stats0"
    seen_processed = []
    call_count = {"n": 0}

    def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
      call_count["n"] += 1
      seen_processed.append(set(processed_files))
      if call_count["n"] <= 2:
        return [path]
      return []

    sleeps = []

    def fake_sleep(secs):
      sleeps.append(secs)
      shutdown_requested[0] = True

    def fake_get_context(name):
      assert name == "spawn"

      class _Ctx:
        def Pool(self, processes=None):
          del processes
          return _FakeFailedIngestPool()

      return _Ctx()

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", fake_sleep)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st.multiprocessing, "get_context", fake_get_context)
    monkeypatch.setattr(
        st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          "/tmp/archive",
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    # second scan still sees empty processed set for the failed file
    assert path not in seen_processed[1]
  finally:
    shutdown_requested[0] = False


def test_checkpoint_flush_is_coalesced(monkeypatch, tmp_path):
  """Checkpoint writes should be coalesced, not rewritten per-file."""
  shutdown_requested[0] = False
  try:
    stats_files = ["/fake/stats0", "/fake/stats1", "/fake/stats2"]

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return list(stats_files)
      return []
    fake_rescan.calls = 0

    def fake_get_context(name):
      assert name == "spawn"

      class _Ctx:
        def Pool(self, processes=None):
          del processes
          return _FakeIngestPool()

      return _Ctx()

    writes = {"count": 0}

    def fake_save(_path, _entries):
      writes["count"] += 1

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "_save_sync_checkpoint", fake_save)
    monkeypatch.setattr(st, "_path_fingerprint", lambda p: {
        "path": p, "size": 1, "mtime": 1})
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st.multiprocessing, "get_context", fake_get_context)
    monkeypatch.setattr(
        st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          str(tmp_path),
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
          run_once=True,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert writes["count"] == 1
  finally:
    shutdown_requested[0] = False


def test_rescan_excludes_inflight_archive_paths(monkeypatch):
  """Files waiting on archive completion should not be rediscovered on rescan."""
  shutdown_requested[0] = False
  try:
    target = "/fake/statsA"
    seen_processed = []
    call_count = {"n": 0}

    def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
      call_count["n"] += 1
      seen_processed.append(set(processed_files))
      if call_count["n"] == 1:
        return [target]
      shutdown_requested[0] = True
      return []

    def fake_add(_lock, path, _contents=None):
      return (path, True, True)

    class _NeverDone:
      def get(self):
        return None

    class _ArchivePool:
      def map_async(self, _fn, _items):
        return _NeverDone()

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {
        "/tmp/day.tar.gz": [target]})
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(
        st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 1)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "_path_fingerprint", lambda p: {
        "path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePool(),
    )

    assert target in seen_processed[1]
  finally:
    shutdown_requested[0] = False


def test_pick_write_lock_for_path_uses_stable_sharding():
  locks = [object(), object(), object()]
  selected_a = st._pick_write_lock_for_path(locks, "/tmp/a")
  selected_a_again = st._pick_write_lock_for_path(locks, "/tmp/a")
  selected_b = st._pick_write_lock_for_path(locks, "/tmp/b")
  assert selected_a is selected_a_again
  assert selected_a in locks
  assert selected_b in locks


def test_head_timestamp_cache_reuses_recent_lookup(monkeypatch):
  calls = {"n": 0}

  class _QS:
    def exists(self):
      calls["n"] += 1
      return True

  class _Mgr:
    def filter(self, **_kwargs):
      return _QS()

  monkeypatch.setattr(st.host_data, "objects", _Mgr())
  monkeypatch.setattr(st, "_HEAD_TIMESTAMP_CACHE", {})
  ts = st.datetime.now(st.timezone.utc)
  assert st._head_timestamp_present_cached("h1", ts)
  assert st._head_timestamp_present_cached("h1", ts)
  assert calls["n"] == 1


def test_db_writer_pipeline_flag_uses_separate_parse_and_write(monkeypatch):
  shutdown_requested[0] = False
  try:
    target = "/fake/stats-db-writer"

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      return []
    fake_rescan.calls = 0

    parse_calls = {"n": 0}
    write_calls = {"n": 0}

    def fake_parse(path, stats_file_contents=None):
      del stats_file_contents
      parse_calls["n"] += 1
      assert path == target
      return (path, ("stats_df", "proc_df"), True, True)

    def fake_write(_lock, task):
      write_calls["n"] += 1
      stats_file, payload, need_archival = task
      assert stats_file == target
      assert payload == ("stats_df", "proc_df")
      assert need_archival is True
      return (stats_file, True, True)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "_parse_stats_file_payload", fake_parse)
    monkeypatch.setattr(st, "_db_writer_worker", fake_write)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: True)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "collect_first_timestamps_by_path", lambda *_a, **_k: {})
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(
        st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          "/tmp/archive",
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
          run_once=True,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert parse_calls["n"] == 1
    assert write_calls["n"] == 1
  finally:
    shutdown_requested[0] = False


def test_archive_retry_backoff_requeues_failed_archive(monkeypatch):
  shutdown_requested[0] = False
  try:
    target = "/fake/stats-retry"

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "collect_first_timestamps_by_path", lambda *_a, **_k: {target: "1709123456"})
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {"/tmp/day.tar.gz": [target]})
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_max_attempts", lambda: 2)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_base_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_max_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")

    archive_pool = _FakeArchivePoolRetry()
    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        archive_pool,
        run_once=True,
    )
    assert archive_pool.calls >= 2
  finally:
    shutdown_requested[0] = False


def test_transition_file_state_rejects_invalid_transition():
  file_states = {}
  assert st._transition_file_state(
      file_states, "/tmp/x", st.SyncFileState.DISCOVERED)
  assert not st._transition_file_state(
      file_states, "/tmp/x", st.SyncFileState.ARCHIVED)


def test_dead_letter_round_trip(tmp_path):
  dead_letter = tmp_path / ".sync_timedb_dead_letter.json"
  entries = [{
      "task": st.ArchiveTask(archive_info=("/tmp/day.tar.gz", ["/tmp/a"]), attempt=3),
      "paths": ["/tmp/a"],
      "retry_at": 0.0,
  }]
  st._save_dead_letter_entries(str(dead_letter), entries)
  loaded = st._load_dead_letter_entries(str(dead_letter))
  assert len(loaded) == 1
  assert loaded[0]["task"].archive_info[0].endswith(".tar.gz")
  assert loaded[0]["paths"] == ["/tmp/a"]


def test_dead_letter_replay_runs_before_idle_sleep(monkeypatch):
  shutdown_requested[0] = False
  try:
    monkeypatch.setattr(st, "_load_dead_letter_entries", lambda *_a, **_k: [{
        "task": st.ArchiveTask(archive_info=("/tmp/day.tar.gz", ["/tmp/a"]), attempt=3),
        "paths": ["/tmp/a"],
        "retry_at": 0.0,
    }])
    monkeypatch.setattr(st, "_save_dead_letter_entries", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "rescan_pending_stats_files", lambda *_a, **_k: [])
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")

    class _ArchivePoolReplay:
      def __init__(self):
        self.calls = 0

      def map_async(self, _fn, _items):
        self.calls += 1

        class _R:
          def get(self):
            return [True]
        return _R()

    ap = _ArchivePoolReplay()
    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        ap,
        run_once=True,
    )
    assert ap.calls >= 1
  finally:
    shutdown_requested[0] = False


def test_parse_payload_marks_new_head_as_archival(monkeypatch):
  target = "/tmp/stats-new-head"
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("h1", "123"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "load_stats_file_lines", lambda *_a, **_k: (["100 job1 h1\n"], None))
  monkeypatch.setattr(st, "parse_first_timestamp_line", lambda _lines: ("100", "job1", "h1"))
  monkeypatch.setattr(st, "_head_timestamp_present_cached", lambda *_a, **_k: False)
  monkeypatch.setattr(st, "parse_stats_lines", lambda *_a, **_k: ([{"k": 1}], []))
  stats_df = pd.DataFrame([{
      "host": "h1",
      "type": "cpu",
      "dev": "0",
      "event": "user",
      "unit": "#",
      "time": 100.0,
      "value": 1.0,
      "wid": 48,
      "mult": 1,
  }])
  proc_df = pd.DataFrame(columns=["jid", "host", "proc"])
  monkeypatch.setattr(st, "build_stats_dataframes", lambda *_a, **_k: (stats_df, proc_df))
  monkeypatch.setattr(st, "compute_deltas_and_arc", lambda s: s)
  stats_file, payload, need_archival, ingest_ok = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload[0] is stats_df
  assert payload[1] is proc_df
  assert need_archival is True
  assert ingest_ok is True


def test_parse_payload_marks_fully_duplicate_file_for_archival(monkeypatch):
  target = "/tmp/stats-dup"
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("h1", "123"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "load_stats_file_lines", lambda *_a, **_k: (["100 job1 h1\n"], None))
  monkeypatch.setattr(st, "parse_first_timestamp_line", lambda _lines: ("100", "job1", "h1"))
  monkeypatch.setattr(st, "_head_timestamp_present_cached", lambda *_a, **_k: True)
  monkeypatch.setattr(st.host_data, "objects", type("_Mgr", (), {
      "filter": staticmethod(lambda **_k: type("_QS", (), {
          "values_list": staticmethod(lambda *a, **k: type("_V", (), {"distinct": staticmethod(lambda: type("_I", (), {"iterator": staticmethod(lambda: iter([]))})())})())
      })())
  })())
  monkeypatch.setattr(st, "find_processing_start_index", lambda *_a, **_k: (-1, True))
  stats_file, payload, need_archival, ingest_ok = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload is None
  assert need_archival is True
  assert ingest_ok is True


def test_empty_primary_mapping_falls_back_to_mtime_archive(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  try:
    target = str(tmp_path / "stats0")
    Path(target).write_text("dummy")
    archive_calls = {"n": 0, "items": []}

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      return []
    fake_rescan.calls = 0

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "collect_first_timestamps_by_path", lambda *_a, **_k: {})
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", str(tmp_path / "daily"))

    class _ArchivePoolCapture:
      def map_async(self, _fn, items):
        archive_calls["n"] += 1
        archive_calls["items"].append(items)

        class _R:
          def get(self):
            return [True for _ in items]
        return _R()

    st.run_sync_timedb_supervisor_loop(
        str(tmp_path),
        "all",
        None,
        "",
        object(),
        _ArchivePoolCapture(),
        run_once=True,
    )
    assert archive_calls["n"] >= 1
    assert archive_calls["items"][0]
  finally:
    shutdown_requested[0] = False


def test_finally_path_finalizes_inflight_archive(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  try:
    target = str(tmp_path / "stats-finalize")
    Path(target).write_text("dummy")
    processed = {"n": 0}

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      raise RuntimeError("forced-exit")
    fake_rescan.calls = 0

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "collect_first_timestamps_by_path", lambda *_a, **_k: {target: "1709123456"})
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {str(tmp_path / "day.tar.gz"): [target]})
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})
    monkeypatch.setattr(st, "tgz_archive_dir", str(tmp_path / "daily"))

    real_add_processed = st._add_processed_path

    def wrapped_add(*args, **kwargs):
      processed["n"] += 1
      return real_add_processed(*args, **kwargs)

    monkeypatch.setattr(st, "_add_processed_path", wrapped_add)

    class _ArchivePoolDone:
      def map_async(self, _fn, _items):
        class _R:
          def get(self):
            return [True]
        return _R()

    with pytest.raises(RuntimeError):
      st.run_sync_timedb_supervisor_loop(
          str(tmp_path),
          "all",
          None,
          "",
          object(),
          _ArchivePoolDone(),
          run_once=True,
      )
    assert processed["n"] >= 1
  finally:
    shutdown_requested[0] = False


def test_archive_result_mismatch_retries_unmatched(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  try:
    target = str(tmp_path / "stats-mismatch")
    Path(target).write_text("dummy")

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "collect_first_timestamps_by_path", lambda *_a, **_k: {target: "1709123456"})
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {str(tmp_path / "day.tar.gz"): [target]})
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_max_attempts", lambda: 2)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_base_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_max_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_archive_pigz_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", str(tmp_path / "daily"))

    class _ArchivePoolMismatch:
      def __init__(self):
        self.calls = 0

      def map_async(self, _fn, _items):
        self.calls += 1

        class _R:
          def __init__(self, call_no):
            self.call_no = call_no

          def get(self):
            if self.call_no == 1:
              return []
            return [True]
        return _R(self.calls)

    ap = _ArchivePoolMismatch()
    st.run_sync_timedb_supervisor_loop(
        str(tmp_path),
        "all",
        None,
        "",
        object(),
        ap,
        run_once=True,
    )
    assert ap.calls >= 2
  finally:
    shutdown_requested[0] = False


def test_log_db_lock_wait_suppressed_under_30_seconds(monkeypatch):
  messages = []
  monkeypatch.setattr(st, "log_print", lambda msg, flush=True: messages.append(msg))

  st._log_db_lock_wait("proc", "/tmp/stats0", 29.999)

  assert messages == []


def test_log_db_lock_wait_emits_over_30_seconds(monkeypatch):
  messages = []
  monkeypatch.setattr(st, "log_print", lambda msg, flush=True: messages.append(msg))

  st._log_db_lock_wait("host", "/tmp/stats0", 30.001)

  assert len(messages) == 1
  assert "DB lock wait host batch file=/tmp/stats0" in messages[0]
