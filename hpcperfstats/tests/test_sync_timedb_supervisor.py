"""Unit tests for sync_timedb supervisor loop (no real multiprocessing or DB)."""

from collections import deque
from datetime import datetime
from pathlib import Path
import json
from unittest.mock import MagicMock, patch

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


def _empty_maintenance_snapshot(*_a, **_k):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  return ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
  )


@pytest.fixture(autouse=True)
def _default_startup_daily_tar_count(monkeypatch):
  """Keep startup archival gating deterministic unless a test overrides it."""
  monkeypatch.setattr(st, "_count_daily_tars", lambda *_a, **_k: 0)
  monkeypatch.setattr(st.cfg, "get_sync_archive_maint_hints", lambda: False)
  monkeypatch.setattr(
      st,
      "build_archive_maintenance_snapshot",
      _empty_maintenance_snapshot,
  )
  monkeypatch.setattr(st, "save_archive_maint_hints", lambda *_a, **_k: None)


def test_periodic_maintenance_always_runs_gated_tar_removal(monkeypatch):
  """Every archive maintenance pass invokes remove_verified_uncompressed_daily_tars."""
  shutdown_requested[0] = False
  try:
    tar_removal_calls = []

    def fake_rescan(*_a, **_k):
      return []

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(
        st,
        "remove_verified_uncompressed_daily_tars",
        lambda *a, **k: tar_removal_calls.append(1),
    )
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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

    assert len(tar_removal_calls) >= 1
  finally:
    shutdown_requested[0] = False


def test_maintenance_passes_ingest_ready_fn_to_raw_removal(monkeypatch):
  shutdown_requested[0] = False
  try:
    captured = {}

    def fake_raw_removal(*_a, **kwargs):
      captured["ingest_ready_fn"] = kwargs.get("ingest_ready_fn")

    def fake_rescan(*_a, **_k):
      return []

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", fake_raw_removal)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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

    assert captured["ingest_ready_fn"] is st.stats_file_head_ingested_in_db
  finally:
    shutdown_requested[0] = False


def test_normalize_archive_groups_by_tgz_sorts_and_copies_paths():
  mapping = {
      "/tmp/2026-03-02.tar.gz": ["/tmp/p2", "/tmp/p1"],
      "/tmp/2026-03-01.tar.gz": ["/tmp/a"],
  }
  tasks = st._normalize_archive_groups_by_tgz(mapping)
  assert tasks == [
      ("/tmp/2026-03-01.tar.gz", ["/tmp/a"]),
      ("/tmp/2026-03-02.tar.gz", ["/tmp/p2", "/tmp/p1"]),
  ]
  tasks[0][1].append("/tmp/mut")
  assert mapping["/tmp/2026-03-01.tar.gz"] == ["/tmp/a"]


def test_db_writer_stage_batch_size_is_bounded():
  size = st._db_writer_stage_batch_size(target_chunk_size=1000, ingest_queue_high=2000)
  assert 1 <= size <= 32
  assert size <= 1000


def test_drain_db_write_tasks_clears_queue_and_updates_tracking(monkeypatch):
  monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
  monkeypatch.setattr(
      st,
      "_db_writer_worker",
      lambda _lock, task: (task[0], task[2], True, float(task[3]) + 0.1),
  )
  file_states = {}
  parse_tasks = deque(
      [
          st.DBWriteTask(path="/tmp/a", payload=("stats", "proc"), need_archival=True, parse_elapsed_s=0.2),
          st.DBWriteTask(path="/tmp/b", payload=("stats", "proc"), need_archival=False, parse_elapsed_s=0.3),
      ]
  )
  successful = []
  to_archive = []
  finished = st._drain_db_write_tasks(
      parse_tasks=parse_tasks,
      manager_lock=object(),
      db_writer_pool=None,
      file_states=file_states,
      successful_paths=successful,
      files_to_be_archived=to_archive,
      chunk_ingest_finished=0,
      pending_total=2,
  )
  assert finished == 2
  assert parse_tasks == deque()
  assert successful == ["/tmp/a", "/tmp/b"]
  assert to_archive == ["/tmp/a"]
  assert file_states["/tmp/a"] == st.SyncFileState.WRITTEN
  assert file_states["/tmp/b"] == st.SyncFileState.WRITTEN


def test_resolve_archive_maintenance_interval_seconds_rejects_nonfinite_and_nonpositive():
  interval, warning = st._resolve_archive_maintenance_interval_seconds(float("nan"))
  assert interval == 8 * 3600
  assert warning == "non_finite_or_non_positive"

  interval, warning = st._resolve_archive_maintenance_interval_seconds(float("inf"))
  assert interval == 8 * 3600
  assert warning == "non_finite_or_non_positive"

  interval, warning = st._resolve_archive_maintenance_interval_seconds(0)
  assert interval == 8 * 3600
  assert warning == "non_finite_or_non_positive"

  interval, warning = st._resolve_archive_maintenance_interval_seconds("bad")
  assert interval == 8 * 3600
  assert warning == "invalid"


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


def test_run_sync_timedb_supervisor_from_parsed_resets_runtime_caches(monkeypatch):
  """Session start clears timestamp caches so stale state never leaks across runs."""
  import hpcperfstats.dbload.sync_timedb_ingest_readiness as readiness

  readiness._HEAD_DB_CACHE[(("hostA"), 1)] = {"present": True, "checked_at": 1.0}
  st._HOST_ITIMES_CACHE[(("hostA"), 1, 2)] = {"times": (1, 2), "checked_at": 1.0}
  monkeypatch.setattr(st, "run_sync_timedb_supervisor_loop", lambda *a, **k: None)
  monkeypatch.setattr(st, "log_date_range", lambda *a, **k: None)
  monkeypatch.setattr(st.cfg, "get_host_name_ext", lambda: "demo.cluster.local")
  monkeypatch.setattr(st.cfg, "get_archive_dir_path", lambda: "/tmp/archive")
  monkeypatch.setattr(st.cfg, "get_sync_enable_cpuset_priority_budget", lambda: False)
  monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
  monkeypatch.setattr(st.cfg, "get_sync_write_lock_shards", lambda: 1)

  class _Manager:
    def Lock(self):
      return object()

    def shutdown(self):
      return None

  class _Pool:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
      return False

  class _Context:
    def Pool(self, processes=None, **kwargs):
      del processes
      return _Pool()

  monkeypatch.setattr(st.multiprocessing, "Manager", lambda: _Manager())
  monkeypatch.setattr(st.multiprocessing, "get_context", lambda _name: _Context())

  st.run_sync_timedb_supervisor_from_parsed(run_once=True, startdate="all", enddate=None)

  assert readiness._HEAD_DB_CACHE == {}
  assert readiness._PATH_READY_CACHE == {}
  assert st._HOST_ITIMES_CACHE == {}


def test_supervisor_sleeps_once_then_exits_after_empty_full_rescan(monkeypatch):
  shutdown_requested[0] = False
  try:
    rescans = deque([["/fake/stats0"], []])

    def fake_rescan(*a, **k):
      if rescans:
        return list(rescans.popleft())
      return []

    sleeps = []
    final_maintenance = {"calls": 0, "remove_verified_tars_calls": 0}

    def fake_sleep(secs):
      sleeps.append(secs)

    def fake_get_context(name):
      assert name == "spawn"

      class _Ctx:
        def Pool(self, processes=None, **kwargs):
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
    monkeypatch.setattr(
        st,
        "remove_verified_uncompressed_daily_tars",
        lambda *a, **k: final_maintenance.__setitem__(
            "remove_verified_tars_calls",
            final_maintenance["remove_verified_tars_calls"] + 1,
        ),
    )
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st.multiprocessing, "get_context", fake_get_context)
    monkeypatch.setattr(
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
    # Maintenance now runs only after an empty rescan. A non-empty first rescan
    # goes straight to ingest, then a single maintenance pass runs on final idle.
    assert final_maintenance["calls"] == 1
    assert final_maintenance["remove_verified_tars_calls"] == 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_runs_full_archive_maintenance_before_rescan_when_idle(monkeypatch):
  shutdown_requested[0] = False
  try:
    events = []

    def fake_rescan(*_a, **_k):
      events.append("rescan")
      return []

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(st, "_count_daily_tars", lambda *_a, **_k: 3)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: events.append("maintenance"))
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(
        st,
        "remove_verified_uncompressed_daily_tars",
        lambda *a, **k: events.append("tar_removal"),
    )
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)

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

    assert events[0] == "rescan"
    assert events[1:3] == ["maintenance", "tar_removal"]
    assert events[-1] == "rescan"
  finally:
    shutdown_requested[0] = False


def test_supervisor_rescans_before_full_maintenance_when_queue_empty(monkeypatch):
  shutdown_requested[0] = False
  try:
    events = []
    rescans = deque([["/fake/stats0"], []])

    def fake_rescan(*_a, **_k):
      events.append("rescan")
      if rescans:
        return list(rescans.popleft())
      return []

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(st, "_count_daily_tars", lambda *_a, **_k: 0)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: events.append("maintenance"))
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)

    class _Ctx:
      def Pool(self, processes=None, **kwargs):
        del processes
        return _FakeIngestPool()

    monkeypatch.setattr(st.multiprocessing, "get_context", lambda _name: _Ctx())

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

    assert events[:2] == ["rescan", "rescan"]
    assert "maintenance" in events
  finally:
    shutdown_requested[0] = False


def test_supervisor_runs_startup_archive_maintenance_when_daily_tars_above_threshold(monkeypatch):
  shutdown_requested[0] = False
  try:
    events = []
    seal_kwargs = []
    tar_removal_kwargs = []

    def fake_rescan(*_a, **_k):
      events.append("rescan")
      return []

    def fake_seal(*a, **k):
      events.append("maintenance")
      seal_kwargs.append(k)

    def fake_tar_removal(*a, **k):
      events.append("tar_removal")
      tar_removal_kwargs.append(k)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(st, "_count_daily_tars", lambda *_a, **_k: 4)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", fake_seal)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", fake_tar_removal)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)

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

    assert events == [
        "maintenance",
        "tar_removal",
        "rescan",
        "maintenance",
        "tar_removal",
        "rescan",
    ]
    assert all(k.get("force_remove_uncompressed_tar") is True for k in seal_kwargs[:1])
    assert all(k.get("force_remove_uncompressed_tar") is True for k in tar_removal_kwargs[:1])
    assert seal_kwargs[1].get("force_remove_uncompressed_tar") is not True
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
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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


def test_supervisor_logs_completed_file_with_global_remaining(monkeypatch):
  """Successful ingest logs path, elapsed, and backlog remaining (not chunk index)."""
  shutdown_requested[0] = False
  try:
    paths = ["/tmp/sync-a", "/tmp/sync-b"]
    calls = {"n": 0}
    logs = []

    def fake_rescan(*_a, **_k):
      if calls["n"] == 0:
        calls["n"] += 1
        return list(paths)
      return []

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(
        st,
        "add_stats_file_to_db",
        lambda _lock, p, _c=None: (p, True, True, 0.05),
    )
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
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

    completed_lines = [ln for ln in logs if ln.startswith("Completed file ")]
    assert len(completed_lines) == 2
    for ln in completed_lines:
      assert " - processed in " in ln
      assert " remaining to process." in ln
    assert any(" - 1 remaining to process." in ln for ln in completed_lines)
    assert any(" - 0 remaining to process." in ln for ln in completed_lines)
    assert not any("chunk " in ln and "completed file" in ln for ln in logs)
  finally:
    shutdown_requested[0] = False


def test_periodic_maintenance_runs_with_backlog_and_logs_context(monkeypatch):
  """Periodic maintenance should still run at chunk boundaries under backlog."""
  shutdown_requested[0] = False
  try:
    calls = {"n": 0}
    logs = []

    def fake_rescan(*_a, **_k):
      if calls["n"] == 0:
        calls["n"] += 1
        return ["/tmp/stats-a", "/tmp/stats-b", "/tmp/stats-c", "/tmp/stats-d"]
      return []

    class _Clock:
      def __init__(self):
        self.t = 0.0

      def __call__(self):
        self.t += 0.25
        return self.t

    clock = _Clock()
    maintenance_calls = {"n": 0}

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st.time, "time", clock)
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )
    monkeypatch.setattr(
        st,
        "seal_dirty_daily_archives",
        lambda *a, **k: maintenance_calls.__setitem__("n", maintenance_calls["n"] + 1),
    )
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")

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

    assert maintenance_calls["n"] >= 2  # periodic + final idle (no startup pass)
    assert any("context=chunk_boundary" in line for line in logs)
  finally:
    shutdown_requested[0] = False


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
        def Pool(self, processes=None, **kwargs):
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
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
        def Pool(self, processes=None, **kwargs):
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
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
      return (path, True, True, 0.0)

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
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
  import hpcperfstats.dbload.sync_timedb_ingest_readiness as readiness

  calls = {"n": 0}

  class _QS:
    def exists(self):
      calls["n"] += 1
      return True

  class _Mgr:
    def filter(self, **_kwargs):
      return _QS()

  monkeypatch.setattr(st.host_data, "objects", _Mgr())
  readiness.reset_sync_ingest_readiness_caches()
  ts = st.datetime.now(st.timezone.utc)
  assert readiness.head_timestamp_present_in_db("h1", ts)
  assert readiness.head_timestamp_present_in_db("h1", ts)
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
      return (path, ("stats_df", "proc_df"), True, True, 0.001)

    def fake_write(_lock, task):
      write_calls["n"] += 1
      stats_file, payload, need_archival, parse_elapsed_s = task
      assert stats_file == target
      assert payload == ("stats_df", "proc_df")
      assert need_archival is True
      assert parse_elapsed_s == 0.001
      return (stats_file, True, True, 0.002)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "_parse_stats_file_payload", fake_parse)
    monkeypatch.setattr(st, "_db_writer_worker", fake_write)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_db_writer_combined_task", lambda: False)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {"/tmp/day.tar.gz": [target]})
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_max_attempts", lambda: 2)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_base_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_max_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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


def test_retry_queue_dispatch_uses_retry_at_order_not_insertion(monkeypatch):
  """Archive retries should dispatch due items by earliest retry_at."""
  shutdown_requested[0] = False
  try:
    future_entry = {
        "task": st.ArchiveTask(archive_info=("/tmp/day-future.tar.gz", ["/tmp/future"]), attempt=1),
        "paths": ["/tmp/future"],
        "retry_at": 10_000.0,
    }
    due_entry = {
        "task": st.ArchiveTask(archive_info=("/tmp/day-due.tar.gz", ["/tmp/due"]), attempt=1),
        "paths": ["/tmp/due"],
        "retry_at": 0.0,
    }
    monkeypatch.setattr(st, "_load_dead_letter_entries", lambda *_a, **_k: [future_entry, due_entry])
    monkeypatch.setattr(st, "rescan_pending_stats_files", lambda *_a, **_k: [])
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st.time, "time", lambda: 1.0)

    dispatched = []

    class _ArchivePoolOrder:
      def map_async(self, _fn, items):
        dispatched.append(list(items))

        class _R:
          def get(self):
            return [True for _ in items]

        return _R()

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolOrder(),
        run_once=True,
    )
    assert dispatched
    first_dispatch = dispatched[0]
    assert first_dispatch == [("/tmp/day-due.tar.gz", ["/tmp/due"])]
  finally:
    shutdown_requested[0] = False


def test_nonblocking_finalize_queues_new_archive_work_when_busy(monkeypatch):
  """When archive job is not ready, new archive work should queue, not overwrite."""
  shutdown_requested[0] = False
  try:
    targets = ["/tmp/a", "/tmp/b"]
    logs = []

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return list(targets)
      return []
    fake_rescan.calls = 0

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "build_archive_mapping", lambda files, *_a, **_k: {
        "/tmp/%s.tar.gz" % files[0].split("/")[-1]: [files[0]]
    })
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st.time, "time", lambda: 1.0)
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )

    dispatched = []

    class _ArchiveResult:
      def __init__(self, ready_initially):
        self._ready = ready_initially

      def ready(self):
        return self._ready

      def wait(self, _timeout):
        return None

      def get(self):
        self._ready = True
        return [True]

    class _ArchivePoolBusy:
      def __init__(self):
        self.calls = 0

      def map_async(self, _fn, items):
        self.calls += 1
        dispatched.append(list(items))
        if self.calls == 1:
          return _ArchiveResult(False)
        return _ArchiveResult(True)

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolBusy(),
        run_once=True,
    )
    assert len(dispatched) == 2
    assert dispatched[0][0][0].endswith("a.tar.gz")
    assert dispatched[1][0][0].endswith("b.tar.gz")
  finally:
    shutdown_requested[0] = False


def test_archive_dispatch_by_tgz_groups_respects_archive_queue_max(monkeypatch):
  shutdown_requested[0] = False
  try:
    target = "/tmp/stats-q"

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      return []
    fake_rescan.calls = 0

    dispatched = []
    logs = []

    class _ArchivePoolCapture:
      def map_async(self, _fn, items):
        dispatched.append(list(items))

        class _R:
          def get(self):
            return [True for _ in items]
        return _R()

    mapping = {
        "/tmp/2026-03-03.tar.gz": ["/tmp/c"],
        "/tmp/2026-03-01.tar.gz": ["/tmp/a"],
        "/tmp/2026-03-02.tar.gz": ["/tmp/b"],
    }

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st.cfg, "get_sync_archive_queue_max_size", lambda: 2)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: mapping)
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolCapture(),
        run_once=True,
    )

    assert dispatched
    first_batch = dispatched[0]
    assert len(first_batch) == 2
    assert first_batch[0][0] == "/tmp/2026-03-01.tar.gz"
    assert first_batch[1][0] == "/tmp/2026-03-02.tar.gz"
    assert any("Archive dispatch by tgz groups submitted=2 deferred_groups=1" in ln for ln in logs)
  finally:
    shutdown_requested[0] = False


def test_periodic_maintenance_logs_deferred_when_archive_finalize_pending(monkeypatch):
  shutdown_requested[0] = False
  try:
    logs = []

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return ["/tmp/stats-a", "/tmp/stats-b"]
      if fake_rescan.calls <= 1:
        fake_rescan.calls += 1
        return []
      return []

    fake_rescan.calls = 0

    class _Clock:
      def __init__(self):
        self.t = 0.0

      def __call__(self):
        self.t += 0.5
        return self.t

    class _NeverReady:
      def ready(self):
        return False

      def wait(self, _timeout):
        return None

      def get(self):
        return [True]

    class _ArchivePoolNeverReady:
      def map_async(self, _fn, _items):
        return _NeverReady()

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(
        st.cfg, "get_archive_maintenance_max_defer_seconds", lambda: 86400.0)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(
        st,
        "build_archive_mapping",
        lambda files, *_a, **_k: {"/tmp/day.tar.gz": list(files)},
    )
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st.time, "time", _Clock())
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolNeverReady(),
        run_once=True,
    )

    assert any("Archive finalize deferred" in line for line in logs)
    assert any("Archive maintenance due but deferred" in line for line in logs)
    assert not any("forced two-phase archive maintenance" in line for line in logs)
  finally:
    shutdown_requested[0] = False


def test_periodic_maintenance_runs_forced_two_phase_when_defer_cap_exceeded(
    monkeypatch,
):
  shutdown_requested[0] = False
  try:
    logs = []
    clock = {"t": 1000.0, "frozen": True, "n": 0}

    def fake_time():
      clock["n"] += 1
      if clock["frozen"]:
        if clock["n"] > 50:
          clock["frozen"] = False
        return clock["t"]
      clock["t"] += 1.0
      return clock["t"]

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls < 3:
        fake_rescan.calls += 1
        return ["/tmp/stats-a", "/tmp/stats-b"]
      return []

    fake_rescan.calls = 0

    class _NeverReady:
      def ready(self):
        return False

      def get(self):
        return [True]

    class _ArchivePoolNeverReady:
      def map_async(self, _fn, _items):
        return _NeverReady()

    monkeypatch.setattr(st.time, "time", fake_time)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_max_defer_seconds", lambda: 0.001)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(
        st,
        "build_archive_mapping",
        lambda files, *_a, **_k: {"/tmp/day.tar.zst": list(files)},
    )
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st,
        "sleep_until_shutdown",
        lambda _seconds: shutdown_requested.__setitem__(0, True),
    )

    def log_print_capture(*args, **kwargs):
      line = " ".join(str(a) for a in args)
      logs.append(line)
      if "forced two-phase archive maintenance" in line:
        shutdown_requested[0] = True

    monkeypatch.setattr(st, "log_print", log_print_capture)

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolNeverReady(),
        run_once=False,
    )

    assert any("reason=scheduled_forced cycle=" in line for line in logs)
    assert any("reason=scheduled_forced_retry cycle=" in line for line in logs)
    assert any("forced two-phase archive maintenance" in line for line in logs)
  finally:
    shutdown_requested[0] = False


def test_continuous_backlog_triggers_forced_maintenance(monkeypatch):
  shutdown_requested[0] = False
  try:
    logs = []
    clock = {"t": 2000.0}
    backlog = ["/tmp/stats-%d" % i for i in range(100)]

    def fake_time():
      clock["t"] += 0.03
      return clock["t"]

    def fake_rescan(*_a, **_k):
      return list(backlog)

    class _NeverReady:
      def ready(self):
        return False

      def get(self):
        return [True]

    class _ArchivePoolNeverReady:
      def map_async(self, _fn, _items):
        return _NeverReady()

    monkeypatch.setattr(st.time, "time", fake_time)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_max_defer_seconds", lambda: 0.05)
    monkeypatch.setattr(st, "chunk_size", 5)
    monkeypatch.setattr(
        st,
        "build_archive_mapping",
        lambda files, *_a, **_k: {"/tmp/day.tar.zst": list(files[:1])},
    )
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(st, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    def log_print_capture(*args, **kwargs):
      line = " ".join(str(a) for a in args)
      logs.append(line)
      if "forced two-phase archive maintenance" in line:
        shutdown_requested[0] = True

    monkeypatch.setattr(st, "log_print", log_print_capture)

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolNeverReady(),
        run_once=False,
    )

    assert any("forced two-phase archive maintenance" in line for line in logs)
    assert any("Archive maintenance due but deferred" in line for line in logs)
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
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda *_a, **_k: False)
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
  stats_file, payload, need_archival, ingest_ok, parse_elapsed_s = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload[0] is stats_df
  assert payload[1] is proc_df
  assert need_archival is True
  assert ingest_ok is True
  assert parse_elapsed_s >= 0.0


def test_parse_payload_marks_fully_duplicate_file_for_archival(monkeypatch):
  target = "/tmp/stats-dup"
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("h1", "123"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "load_stats_file_lines", lambda *_a, **_k: (["100 job1 h1\n"], None))
  monkeypatch.setattr(st, "parse_first_timestamp_line", lambda _lines: ("100", "job1", "h1"))
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda *_a, **_k: True)
  monkeypatch.setattr(st.host_data, "objects", type("_Mgr", (), {
      "filter": staticmethod(lambda **_k: type("_QS", (), {
          "values_list": staticmethod(lambda *a, **k: type("_V", (), {"distinct": staticmethod(lambda: type("_I", (), {"iterator": staticmethod(lambda: iter([]))})())})())
      })())
  })())
  monkeypatch.setattr(st, "find_processing_start_index", lambda *_a, **_k: (-1, True))
  stats_file, payload, need_archival, ingest_ok, parse_elapsed_s = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload is None
  assert need_archival is True
  assert ingest_ok is True
  assert parse_elapsed_s >= 0.0


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
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {str(tmp_path / "day.tar.gz"): [target]})
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {str(tmp_path / "day.tar.gz"): [target]})
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_max_attempts", lambda: 2)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_base_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_sync_archive_retry_backoff_max_seconds", lambda: 0.0)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
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


def test_insert_host_data_individually_uses_force_insert():
  """Fallback inserts must not use Django.save() UPDATE path on existing pk (Timescale decompress limit)."""
  mock_inst = MagicMock()
  row_time = pd.Timestamp("2026-04-05 07:40:44.301268101+0000", tz="UTC")
  df = pd.DataFrame(
      [
          {
              "host": "i615-154.vista.tacc.utexas.edu",
              "jid": None,
              "type": "block",
              "event": "in_flight",
              "unit": "#",
              "time": row_time,
              "value": 0.0,
              "delta": 0.0,
              "arc": 0.0,
          }
      ]
  )
  with patch.object(st, "host_data_instance_from_stats_row", return_value=mock_inst):
    st._insert_host_data_individually(df)

  mock_inst.save.assert_called_once_with(force_insert=True)


def test_sync_timedb_uses_fixed_batch_sizes_not_adaptive_helpers():
  """Ingest uses fixed chunk and bulk_create batch sizes (no runtime tuning)."""
  assert st.chunk_size == 1000
  assert st.bulk_create_batch_size == 10000
  assert not hasattr(st, "_get_adaptive_bulk_create_batch_size")
  assert not hasattr(st, "_record_adaptive_batch_feedback")


def test_sync_timedb_supervisor_source_uses_watch_pool_not_raw_imap():
  """Ingest pool loops must use imap_unordered_watch_pool (OOM-safe)."""
  source_path = Path(st.__file__)
  text = source_path.read_text(encoding="utf-8")
  assert "ingest_pool.imap_unordered" not in text
  assert "imap_unordered_watch_pool" in text


def test_db_writer_stage_batch_size_uses_ini_cap(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_db_writer_stage_max_batch", lambda: 8)
  assert st._db_writer_stage_batch_size(1000, 2000) == 8
  monkeypatch.setattr(st.cfg, "get_sync_db_writer_stage_max_batch", lambda: 3)
  assert st._db_writer_stage_batch_size(1000, 2000) == 3


def test_cap_pending_stats_files_list_truncates():
  from hpcperfstats.dbload.sync_timedb_archive_helpers import (
      cap_pending_stats_file_list,
  )

  paths = ["/a/%d" % i for i in range(10)]
  capped = cap_pending_stats_file_list(paths, 4, log_fn=lambda *_a, **_k: None)
  assert capped == paths[:4]


def test_rescan_pending_stats_files_reuses_set_without_copy(monkeypatch):
  from hpcperfstats.dbload import sync_timedb_archive_helpers as helpers

  discovered = ["/pending/1", "/pending/2", "/done/1"]
  monkeypatch.setattr(
      helpers,
      "collect_stats_files_in_range",
      lambda *_a, **_k: list(discovered),
  )
  processed = {"/done/1"}
  result = helpers.rescan_pending_stats_files(
      "/arc", "all", None, ".hpc", processed)
  assert result == ["/pending/1", "/pending/2"]
  assert processed == {"/done/1"}


def test_ingest_pool_worker_exit_propagates_from_supervisor(monkeypatch):
  from hpcperfstats.dbload.multiprocessing_pool_health import (
      MultiprocessingWorkerExitError,
  )

  shutdown_requested[0] = False
  target = "/fake/stats-oom"

  def fake_rescan(*_a, **_k):
    if fake_rescan.calls == 0:
      fake_rescan.calls += 1
      return [target]
    return []
  fake_rescan.calls = 0

  def failing_watch_pool(pool, fn, iterable, *, context="", poll_timeout_s=None):
    del pool, fn, iterable, context, poll_timeout_s
    raise MultiprocessingWorkerExitError(
        "worker dead",
        dead_pids=(999,),
        context="test",
    )

  monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
  monkeypatch.setattr(st, "imap_unordered_watch_pool", failing_watch_pool)
  monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
  monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: False)
  monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
  monkeypatch.setattr(st.cfg, "get_sync_db_writer_combined_task", lambda: False)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1000)
  monkeypatch.setattr(st.cfg, "get_sync_supervisor_rss_limit_mb", lambda: 0)
  monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
  monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
  monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(
      st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.connections, "close_all", lambda: None)
  terminate_calls = []
  monkeypatch.setattr(
      st,
      "terminate_pool_bounded",
      lambda pool: terminate_calls.append(pool) or True,
  )

  class _Pool:
    pass

  ingest_pool = _Pool()

  class _SpawnCtx:
    def Pool(self, *args, **kwargs):
      del args, kwargs
      return ingest_pool

  monkeypatch.setattr(
      st.multiprocessing,
      "get_context",
      lambda _name: _SpawnCtx(),
  )

  archive_pool = _FakeArchivePool()
  archive_pool.__enter__()
  try:
    with pytest.raises(MultiprocessingWorkerExitError) as excinfo:
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
  assert excinfo.value.exit_code == 137
  assert terminate_calls


def test_combined_db_writer_task_uses_single_pool_path(monkeypatch):
  shutdown_requested[0] = False
  target = "/fake/stats-combined"

  def fake_rescan(*_a, **_k):
    if fake_rescan.calls == 0:
      fake_rescan.calls += 1
      return [target]
    return []
  fake_rescan.calls = 0

  watch_calls = []

  def capture_watch(pool, fn, iterable, *, context="", poll_timeout_s=None):
    del pool, poll_timeout_s
    paths = list(iterable)
    watch_calls.append((fn, context, paths))
    return (fn(path) for path in paths)

  combined_calls = {"n": 0}

  def fake_combined(_lock, path, stats_file_contents=None):
    del _lock, stats_file_contents
    combined_calls["n"] += 1
    return (path, True, True, 0.01)

  monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
  monkeypatch.setattr(st, "imap_unordered_watch_pool", capture_watch)
  monkeypatch.setattr(st, "_ingest_parse_and_write_file", fake_combined)
  monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: False)
  monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: True)
  monkeypatch.setattr(st.cfg, "get_sync_db_writer_combined_task", lambda: True)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1000)
  monkeypatch.setattr(st.cfg, "get_sync_supervisor_rss_limit_mb", lambda: 0)
  monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
  monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
  monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(
      st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.connections, "close_all", lambda: None)

  pool_count = {"n": 0}

  class _Pool:
    pass

  def fake_pool(*_a, **_k):
    pool_count["n"] += 1
    return _Pool()

  class _SpawnCtx:
    def Pool(self, *args, **kwargs):
      del args, kwargs
      return fake_pool()

  monkeypatch.setattr(
      st.multiprocessing,
      "get_context",
      lambda _name: _SpawnCtx(),
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

  assert combined_calls["n"] == 1
  assert watch_calls[0][1] == "sync_timedb ingest pool"
  assert pool_count["n"] == 1


def test_maybe_exit_on_supervisor_rss_limit_exits_137(monkeypatch, tmp_path):
  monkeypatch.setattr(st.cfg, "get_sync_supervisor_rss_limit_mb", lambda: 1)
  monkeypatch.setattr(st.cfg, "get_sync_supervisor_rss_check_every_n_chunks", lambda: 1)
  status = tmp_path / "status"
  status.write_text("VmRSS:\t2048 kB\n", encoding="utf-8")
  monkeypatch.setattr(st, "read_process_rss_bytes", lambda: 2048 * 1024)
  with pytest.raises(SystemExit) as excinfo:
    st._maybe_exit_on_supervisor_rss_limit(1)
  assert excinfo.value.code == 137


def test_sync_worker_db_task_closes_connections(monkeypatch):
  close_calls = []
  monkeypatch.setattr(st, "close_old_connections", lambda: close_calls.append("close_old"))
  monkeypatch.setattr(st.connections, "close_all", lambda: close_calls.append("close_all"))
  with st._sync_worker_db_task():
    pass
  assert close_calls == ["close_old", "close_all"]


def test_host_recent_timestamps_cached_skips_oversized_cache_entry(monkeypatch):
  from datetime import timezone

  st._HOST_ITIMES_CACHE.clear()
  monkeypatch.setattr(st.cfg, "get_sync_host_itimes_cache_max_timestamps_per_entry", lambda: 3)
  host = "node1"
  ts_low = datetime(2026, 1, 1, tzinfo=timezone.utc)
  ts_high = datetime(2026, 1, 3, tzinfo=timezone.utc)
  key = (host, int(ts_low.timestamp()), int(ts_high.timestamp()))

  class _FakeQS:
    def values_list(self, *_args, **_kwargs):
      return self

    def distinct(self):
      return self

    def iterator(self):
      for i in range(5):
        yield datetime(2026, 1, 2, 0, 0, i, tzinfo=timezone.utc)

  filter_calls = {"count": 0}

  def _fake_filter(**_kwargs):
    filter_calls["count"] += 1
    return _FakeQS()

  monkeypatch.setattr(st.host_data.objects, "filter", _fake_filter)
  result = st._host_recent_timestamps_cached(host, ts_low, ts_high)
  assert len(result) == 5
  assert key not in st._HOST_ITIMES_CACHE
  st._host_recent_timestamps_cached(host, ts_low, ts_high)
  assert filter_calls["count"] == 2
  assert key not in st._HOST_ITIMES_CACHE


def test_add_processed_path_prunes_file_states(monkeypatch, tmp_path):
  monkeypatch.setattr(st, "processed_files_max_size", 2)
  processed_files = set()
  processed_files_order = deque()
  checkpoint_entries = deque()
  file_states = {}
  checkpoint_path = str(tmp_path / "cp.json")
  paths = []
  for i in range(3):
    path = str(tmp_path / ("file%d" % i))
    Path(path).write_text("x", encoding="utf-8")
    paths.append(path)
    file_states[path] = st.SyncFileState.ARCHIVED
    st._add_processed_path(
        path,
        processed_files,
        processed_files_order,
        checkpoint_entries,
        checkpoint_path,
        file_states=file_states,
    )
  assert len(processed_files) == 2
  assert len(file_states) == 2
  assert paths[0] not in file_states
  assert paths[1] in file_states
  assert paths[2] in file_states


class _CapturingHygieneExecutor:
  """Records submitted callables instead of running them in a thread."""

  def __init__(self, *args, **kwargs):
    del args, kwargs
    self.submitted = []

  def submit(self, fn, *args, **kwargs):
    del args, kwargs
    self.submitted.append(fn)

    class _Future:
      def done(self):
        return True

      def result(self, timeout=None):
        del timeout
        return None

    return _Future()

  def shutdown(self, *args, **kwargs):
    del args, kwargs


def test_post_chunk_hygiene_scheduled_and_runs_seal_before_delete(monkeypatch):
  """A chunk that appends to tars schedules single-flight hygiene that runs the
  canonical seal -> remove-raw -> remove-tar pipeline, skips disqualified days,
  and disables auto-seal so only the dedicated seal step compresses."""
  shutdown_requested[0] = False
  try:
    target = "/fake/statsA"
    holder = {}
    events = []

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if not holder.get("served"):
        holder["served"] = True
        return [target]
      shutdown_requested[0] = True
      return []

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    class _NeverDone:
      def get(self):
        return None

    class _ArchivePool:
      def map_async(self, _fn, _items):
        return _NeverDone()

    def _factory(*_a, **_k):
      ex = _CapturingHygieneExecutor()
      holder["executor"] = ex
      return ex

    monkeypatch.setattr(st, "ThreadPoolExecutor", _factory)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(
        st, "build_archive_mapping",
        lambda *_a, **_k: {"/tmp/2024-01-01.tar.gz": [target]})
    monkeypatch.setattr(
        st, "seal_dirty_daily_archives",
        lambda *a, **k: events.append(("seal", k)))
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files",
        lambda *a, **k: events.append(("raw", k)))
    monkeypatch.setattr(
        st, "remove_verified_uncompressed_daily_tars",
        lambda *a, **k: events.append(("tar", k)))
    monkeypatch.setattr(
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 1)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _ArchivePool())

    executor = holder["executor"]
    assert executor.submitted, "post-chunk hygiene was not scheduled"

    # Preflight skip: no dirty-sealable day and no removable raw -> the body must
    # return before any seal/raw/.tar mutation (no redundant zstd).
    monkeypatch.setattr(st, "iter_daily_tar_paths", lambda *_a, **_k: [])
    monkeypatch.setattr(st, "should_seal_daily_tar", lambda *a, **k: False)
    events.clear()
    executor.submitted[0]()
    assert events == [], "hygiene mutated archives despite no eligible work"

    # Force the hygiene preflight to find a sealable, non-disqualified day, then
    # run the captured hygiene body in isolation and assert pipeline order.
    monkeypatch.setattr(st, "iter_daily_tar_paths", lambda *_a, **_k: ["/tmp/2024-01-02.tar"])
    monkeypatch.setattr(st, "should_seal_daily_tar", lambda *a, **k: True)
    events.clear()
    executor.submitted[0]()

    assert [name for name, _ in events] == ["seal", "raw", "tar"]
    seal_kwargs = events[0][1]
    raw_kwargs = events[1][1]
    assert "skip_daily_tar_paths" in seal_kwargs
    assert raw_kwargs.get("allow_auto_seal") is False
  finally:
    shutdown_requested[0] = False
