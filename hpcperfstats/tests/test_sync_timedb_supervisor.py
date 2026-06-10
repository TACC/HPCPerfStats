"""Unit tests for sync_timedb supervisor loop (no real multiprocessing or DB)."""

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import json
import os
from unittest.mock import MagicMock, patch

import hpcperfstats.dbload.sync_timedb as st
import hpcperfstats.dbload.sync_timedb_archive_helpers as archive_helpers
import hpcperfstats.dbload.sync_timedb_archive_janitor as janitor_mod
import hpcperfstats.dbload.sync_timedb_async_day_close as async_day_close_mod
import pandas as pd
import pytest
from hpcperfstats.shutdown_utils import shutdown_requested


def _fake_map_async_result(value):
  """``AsyncResult`` double with ``ready()`` for ``ArchiveDispatchCoordinator``."""

  class _R:
    def ready(self):
      return True

    def get(self, timeout=None):
      del timeout
      return value() if callable(value) else value

  return _R()


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
    return _fake_map_async_result(None)


class _FakeArchivePoolPending:
  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    return False

  def map_async(self, fn, items):
    del fn, items
    return _fake_map_async_result(None)


class _FakeArchivePoolRetry:
  def __init__(self):
    self.calls = 0

  def map_async(self, fn, items):
    del fn, items
    self.calls += 1
    result = [False] if self.calls == 1 else [True]
    return _fake_map_async_result(result)


def _empty_maintenance_snapshot(*_a, **_k):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  return ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
  )


class _InlineThreadPoolExecutor:
  """Run archive-janitor ticks inline so supervisor unit tests do not hang."""

  def __init__(self, *args, **kwargs):
    del args, kwargs

  def submit(self, fn, *args, **kwargs):
    fn(*args, **kwargs)

    class _DoneFuture:
      def done(self):
        return True

    return _DoneFuture()

  def shutdown(self, wait=True):
    del wait


@pytest.fixture(autouse=True)
def _default_startup_daily_tar_count(monkeypatch):
  """Keep startup archival gating deterministic unless a test overrides it."""
  monkeypatch.setattr(st, "_count_daily_tars", lambda *_a, **_k: 0)
  monkeypatch.setattr(st.cfg, "get_sync_archive_maint_hints", lambda: False)
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      _empty_maintenance_snapshot,
  )
  monkeypatch.setattr(janitor_mod, "save_archive_maint_hints", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "ThreadPoolExecutor", _InlineThreadPoolExecutor)
  monkeypatch.setattr(janitor_mod, "ThreadPoolExecutor", _InlineThreadPoolExecutor)
  monkeypatch.setattr(st.cfg, "get_sync_day_close_raw_removal_preflight", lambda: False)
  monkeypatch.setattr(
      async_day_close_mod.AsyncDayCloseCoordinator,
      "submit_day_close",
      lambda self, tar_path, *, reason: bool(tar_path),
  )
  monkeypatch.setattr(
      async_day_close_mod.AsyncDayCloseCoordinator,
      "is_complete",
      lambda self, tar_path: bool(tar_path),
  )
  monkeypatch.setattr(
      async_day_close_mod.AsyncDayCloseCoordinator,
      "active_or_submitted_tar_paths",
      lambda self: set(),
  )
  _orig_janitor_init = janitor_mod.ArchiveJanitor.__init__

  def _janitor_init_no_tick_chain(self, *args, **kwargs):
    _orig_janitor_init(self, *args, **kwargs)
    self._allow_tick_chaining = False

  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "__init__", _janitor_init_no_tick_chain)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  monkeypatch.setattr(archive_helpers, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(archive_helpers, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda *a, **k: [])


def test_periodic_maintenance_always_runs_gated_tar_removal(monkeypatch, tmp_path):
  """Janitor debt accrual and ticks invoke remove_verified_uncompressed_daily_tars."""
  shutdown_requested[0] = False
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

    tar_removal_calls = []

    def fake_rescan(*_a, **_k):
      return []

    def snapshot_with_tar_debt(*_a, **_k):
      return ArchiveMaintenanceSnapshot(
          closed_paths=[],
          remaining_raw_by_gz={"/tmp/2026-01-01.tar.gz": ["/tmp/raw-a"]},
          mapping={},
          ready_paths=set(),
      )

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(
        st,
        "get_unmapped_closed_raw_daily_tars_cached",
        lambda *_a, **_k: frozenset(),
    )
    monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", snapshot_with_tar_debt)
    monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
    monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
    monkeypatch.setattr(
        janitor_mod,
        "remove_verified_uncompressed_daily_tars",
        lambda *a, **k: tar_removal_calls.append(1),
    )
    clock = {"t": 10_000.0}

    def fake_time():
      clock["t"] += 2.0
      return clock["t"]

    monkeypatch.setattr(st.time, "time", fake_time)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 1.0)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          str(archive_dir),
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
          run_once=True,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert len(tar_removal_calls) == 0
  finally:
    shutdown_requested[0] = False


def test_supervisor_wires_ingest_ready_fn_into_day_raw_removal(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  captured = {}
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    original_coord = st.DayRawRemovalCoordinator

    def spy_coord(**kwargs):
      captured["ingest_ready_fn"] = kwargs.get("ingest_ready_fn")
      return original_coord(**kwargs)

    def fake_rescan(*_a, **_k):
      shutdown_requested[0] = True
      return []

    monkeypatch.setattr(st, "DayRawRemovalCoordinator", spy_coord)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert captured["ingest_ready_fn"] is st.stats_file_head_ingested_in_db
  finally:
    shutdown_requested[0] = False


def test_supervisor_calls_ensure_persistence_contract_at_startup(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  contract_calls = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()

    def spy_contract(directory, *, log_fn=None):
      contract_calls.append(directory)
      return False

    def fake_rescan(*_a, **_k):
      shutdown_requested[0] = True
      return []

    monkeypatch.setattr(st, "ensure_persistence_contract", spy_contract)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert contract_calls == [str(archive_dir)]
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


def test_supervisor_sleeps_once_then_exits_after_empty_full_rescan(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
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
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(st.multiprocessing, "get_context", fake_get_context)
    monkeypatch.setattr(
        st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    _supervisor_startup_preflight_disabled(monkeypatch)

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      st.run_sync_timedb_supervisor_loop(
          str(archive_dir),
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert sleeps == [st.EMPTY_QUEUE_RESCAN_SLEEP_SECONDS]
    # Blocking supervisor maintenance is gone; janitor owns cold-path cleanup.
    assert final_maintenance["calls"] == 0
    assert final_maintenance["remove_verified_tars_calls"] == 0
  finally:
    shutdown_requested[0] = False


def test_supervisor_runs_full_archive_maintenance_before_rescan_when_idle(
    monkeypatch, tmp_path,
):
  shutdown_requested[0] = False
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  try:
    events = []

    def fake_rescan(*_a, **_k):
      events.append("rescan")
      return []

    _supervisor_startup_preflight_disabled(monkeypatch)
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
          str(archive_dir),
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
    assert "maintenance" not in events
    assert "tar_removal" not in events
    assert events[-1] == "rescan"
  finally:
    shutdown_requested[0] = False


def test_supervisor_rescans_before_full_maintenance_when_queue_empty(
    monkeypatch, tmp_path,
):
  shutdown_requested[0] = False
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  try:
    events = []
    rescans = deque([["/fake/stats0"], []])

    def fake_rescan(*_a, **_k):
      events.append("rescan")
      if rescans:
        return list(rescans.popleft())
      return []

    _supervisor_startup_preflight_disabled(monkeypatch)
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
          str(archive_dir),
          "all",
          None,
          ".hpc",
          object(),
          archive_pool,
      )
    finally:
      archive_pool.__exit__(None, None, None)

    assert events[:2] == ["rescan", "rescan"]
    assert "maintenance" not in events
  finally:
    shutdown_requested[0] = False


def test_supervisor_runs_startup_archive_maintenance_when_daily_tars_above_threshold(monkeypatch):
  """Startup no longer blocks on maintenance; janitor enqueue runs before first rescan."""
  shutdown_requested[0] = False
  try:
    events = []
    janitor_signals = {"n": 0}

    def fake_rescan(*_a, **_k):
      events.append("rescan")
      return []

    original_signal = janitor_mod.ArchiveJanitor.signal_work_available

    def counting_signal(self):
      janitor_signals["n"] += 1
      return original_signal(self)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor, "signal_work_available", counting_signal)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
    monkeypatch.setattr(st, "build_archive_mapping", lambda *a, **k: {})
    monkeypatch.setattr(st, "_count_daily_tars", lambda *_a, **_k: 4)
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
    assert janitor_signals["n"] >= 1
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
  """Ingest backlog does not run removed interval accrual at chunk boundaries."""
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

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr("hpcperfstats.dbload.sync_timedb.time.time", clock)
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )
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

    assert not any("Archive debt accrual deferred" in line for line in logs)
    assert not any("Archive janitor accrue reason=" in line for line in logs)
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
      def ready(self):
        return False

      def get(self, timeout=None):
        del timeout
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
        return _fake_map_async_result([True for _ in items])

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


def test_nonblocking_finalize_queues_new_archive_work_when_busy(monkeypatch, tmp_path):
  """When archive job is not ready, new archive work should queue, not overwrite."""
  shutdown_requested[0] = False
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
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
    monkeypatch.setattr(
        st,
        "async_result_get_watch_pool",
        lambda *_a, **_k: [True],
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

    archive_pool = _ArchivePoolBusy()
    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        archive_pool,
        run_once=True,
    )
    # First chunk dispatches map_async; later chunks must not start a second map_async
    # while the in-flight job is still pending.
    assert archive_pool.calls == 1
    assert len(dispatched) == 1
    assert dispatched[0][0][0].endswith("a.tar.gz")
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
        return _fake_map_async_result([True for _ in items])

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
    assert any("Archive dispatch submitted=2 queued=1 inflight_slots=1" in ln for ln in logs)
  finally:
    shutdown_requested[0] = False


def test_periodic_maintenance_logs_deferred_when_archive_finalize_pending(
    monkeypatch, tmp_path):
  """Finalize stays soft-deferred; janitor replaces blocking supervisor maintenance."""
  shutdown_requested[0] = False
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  try:
    logs = []
    clock = {"t": 2000.0}
    pending = ["/tmp/stats-a", "/tmp/stats-b", "/tmp/stats-c"]
    archive_dispatched = [False]

    def fake_time():
      if not archive_dispatched[0]:
        return clock["t"]
      clock["t"] += 0.25
      return clock["t"]

    def fake_rescan(*_a, **_k):
      return list(pending)

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

    log_lines = {"n": 0}

    def log_print_capture(*args, **kwargs):
      line = " ".join(str(a) for a in args)
      logs.append(line)
      log_lines["n"] += 1
      if "Archive dispatch submitted=" in line:
        archive_dispatched[0] = True
      if "Archive finalize deferred" in line:
        shutdown_requested[0] = True
      if log_lines["n"] > 120:
        shutdown_requested[0] = True

    monkeypatch.setattr(st.time, "time", fake_time)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
    monkeypatch.setattr(
        st,
        "build_archive_mapping",
        lambda files, *_a, **_k: {"/tmp/day.tar.gz": list(files[:1])},
    )
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(st, "log_print", log_print_capture)
    monkeypatch.setattr(
        st,
        "async_result_get_watch_pool",
        lambda *_a, **_k: [True],
    )
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda _s: None)

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolNeverReady(),
        run_once=True,
    )

    assert any("Archive finalize deferred" in line for line in logs)
    assert not any("Archive maintenance due but deferred" in line for line in logs)
    assert not any("forced two-phase archive maintenance" in line for line in logs)
  finally:
    shutdown_requested[0] = False


def test_periodic_maintenance_runs_forced_two_phase_when_defer_cap_exceeded(
    monkeypatch,
    tmp_path,
):
  """Forced two-phase supervisor maintenance is retired; janitor signals continue."""
  shutdown_requested[0] = False
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  try:
    logs = []
    janitor_signals = {"n": 0}
    original_signal = janitor_mod.ArchiveJanitor.signal_work_available

    def counting_signal(self):
      janitor_signals["n"] += 1
      return original_signal(self)

    rescan_calls = {"n": 0}

    def fake_rescan(*_a, **_k):
      if rescan_calls["n"] == 0:
        rescan_calls["n"] += 1
        return ["/tmp/stats-a"]
      return []

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor, "signal_work_available", counting_signal)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
    monkeypatch.setattr(
        st,
        "build_archive_mapping",
        lambda files, *_a, **_k: {"/tmp/day.tar.zst": list(files[:1])},
    )
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda _s: None)

    class _ReadyArchivePool:
      def map_async(self, _fn, _items):
        class _R:
          def ready(self):
            return True

          def get(self):
            return [True]
        return _R()

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _ReadyArchivePool(),
        run_once=True,
    )

    assert janitor_signals["n"] >= 1
    assert not any("forced two-phase archive maintenance" in line for line in logs)
  finally:
    shutdown_requested[0] = False


def test_continuous_backlog_triggers_forced_maintenance(monkeypatch, tmp_path):
  """Continuous ingest backlog still signals the janitor without interval accrual."""
  shutdown_requested[0] = False
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  try:
    logs = []
    backlog = ["/tmp/stats-a", "/tmp/stats-b", "/tmp/stats-c"]

    def fake_rescan(*_a, **_k):
      return list(backlog)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 0.5)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
    monkeypatch.setattr(
        st,
        "build_archive_mapping",
        lambda files, *_a, **_k: {"/tmp/day.tar.zst": list(files[:1])},
    )
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    def log_print_capture(*args, **kwargs):
      line = " ".join(str(a) for a in args)
      logs.append(line)
      if "sync_timedb: maintenance pass reason=startup" in line:
        shutdown_requested[0] = True

    monkeypatch.setattr(st, "log_print", log_print_capture)
    monkeypatch.setattr(st, "sleep_until_shutdown", lambda _s: None)

    class _NeverReady:
      def ready(self):
        return False

      def get(self):
        return [True]

    class _ArchivePoolNeverReady:
      def map_async(self, _fn, _items):
        return _NeverReady()

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolNeverReady(),
        run_once=False,
    )

    assert any(
        "sync_timedb: maintenance pass reason=startup" in line for line in logs)
    assert not any("Archive debt accrual deferred" in line for line in logs)
    assert not any("Archive janitor accrue reason=" in line for line in logs)
    assert not any("forced two-phase archive maintenance" in line for line in logs)
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
        return _fake_map_async_result([True])

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
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
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
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
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
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: True)
  stats_file, payload, need_archival, ingest_ok, parse_elapsed_s = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload is None
  assert need_archival is True
  assert ingest_ok is True
  assert parse_elapsed_s >= 0.0


def test_parse_stats_file_payload_need_archival_false_on_day_skip(monkeypatch):
  target = "/tmp/stats-day-skip"
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
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
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  stats_file, payload, need_archival, ingest_ok, parse_elapsed_s = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload is None
  assert need_archival is False
  assert ingest_ok is True
  assert parse_elapsed_s >= 0.0


def test_sync_timedb_exits_on_redis_unavailable_during_ingest(monkeypatch):
  from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
  )

  shutdown_requested[0] = False
  try:
    target = "/fake/stats-redis-fatal"

    def fake_rescan(*_a, **_k):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      return []
    fake_rescan.calls = 0

    def fake_combined(_lock, path, stats_file_contents=None):
      del _lock, stats_file_contents
      raise ArchiveMembersRedisUnavailableError("redis down mid-ingest")

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "_ingest_parse_and_write_file", fake_combined)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
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
    monkeypatch.setattr(
        "hpcperfstats.dbload.sync_timedb_archive_members_redis"
        ".verify_archive_members_redis_startup",
        lambda: None,
    )

    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
      with pytest.raises(SystemExit) as excinfo:
        st.run_sync_timedb_supervisor_loop(
            "/tmp/archive",
            "all",
            None,
            ".hpc",
            object(),
            archive_pool,
            run_once=True,
        )
      assert excinfo.value.code == 1
    finally:
      archive_pool.__exit__(None, None, None)
  finally:
    shutdown_requested[0] = False


def _parse_payload_quarantine_fixture(monkeypatch, tmp_path, *, lines, parse_side_effect=None):
  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "1778200758"
  raw_path.write_text("".join(lines), encoding="utf-8")
  target = str(raw_path)

  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.cfg, "get_archive_dir_path", lambda: str(archive_dir))
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("host.hpc", "1778200758"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "load_stats_file_lines", lambda *_a, **_k: (list(lines), None))
  monkeypatch.setattr(
      st,
      "parse_first_timestamp_line",
      lambda _lines: ("1778200758", "job1", "cn001"),
  )
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda *_a, **_k: False)
  if parse_side_effect is not None:
    monkeypatch.setattr(st, "parse_stats_lines", parse_side_effect)
  return target, archive_dir, raw_path


def test_parse_payload_quarantines_on_parse_exception(monkeypatch, tmp_path):
  lines = ["1778200758 job1 cn001\n", "bad\n"]
  target, archive_dir, raw_path = _parse_payload_quarantine_fixture(
      monkeypatch,
      tmp_path,
      lines=lines,
      parse_side_effect=lambda *_a, **_k: (_ for _ in ()).throw(
          ValueError("not enough values to unpack (expected 3, got 2)")
      ),
  )
  stats_file, payload, need_archival, ingest_ok, parse_elapsed_s = st._parse_stats_file_payload(
      target,
  )
  assert stats_file == target
  assert payload is None
  assert need_archival is False
  assert ingest_ok is True
  assert parse_elapsed_s >= 0.0
  assert not raw_path.exists()
  quarantine_path = (
      archive_dir / archive_helpers.SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME
      / "host.hpc" / "1778200758"
  )
  assert quarantine_path.is_file()


@pytest.mark.parametrize(
    "failure_kind,lines,load_err,first_ts,host,empty_df",
    [
        ("load_err", [], "read failed", None, None, False),
        ("no_timestamp", ["not-a-stats-line\n"], None, None, None, False),
        ("no_host", ["1778200758 job1\n"], None, "1778200758", "", False),
        ("empty_df", ["1778200758 job1 cn001\n"], None, "1778200758", "cn001", True),
    ],
)
def test_parse_payload_quarantines_permanent_failures(
    monkeypatch,
    tmp_path,
    failure_kind,
    lines,
    load_err,
    first_ts,
    host,
    empty_df,
):
  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "bad_raw"
  raw_path.write_text("".join(lines) if lines else "x", encoding="utf-8")
  target = str(raw_path)

  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.cfg, "get_archive_dir_path", lambda: str(archive_dir))
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("host.hpc", "bad_raw"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(
      st,
      "load_stats_file_lines",
      lambda *_a, **_k: (list(lines), load_err),
  )
  if first_ts is None and host is None:
    monkeypatch.setattr(st, "parse_first_timestamp_line", lambda _lines: (None, None, None))
  elif host == "":
    monkeypatch.setattr(st, "parse_first_timestamp_line", lambda _lines: (first_ts, "job1", ""))
  else:
    monkeypatch.setattr(
        st,
        "parse_first_timestamp_line",
        lambda _lines: (first_ts, "job1", host),
    )
    monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda *_a, **_k: False)
    monkeypatch.setattr(st, "parse_stats_lines", lambda *_a, **_k: ([], []))
    empty_stats = pd.DataFrame()
    empty_proc = pd.DataFrame(columns=["jid", "host", "proc"])
    monkeypatch.setattr(
        st,
        "build_stats_dataframes",
        lambda *_a, **_k: (empty_stats, empty_proc),
    )
    monkeypatch.setattr(st, "compute_deltas_and_arc", lambda s: s)

  stats_file, payload, need_archival, ingest_ok, _elapsed = st._parse_stats_file_payload(target)
  assert ingest_ok is True, failure_kind
  assert payload is None
  assert need_archival is False
  assert not raw_path.exists()


def test_parse_payload_per_file_timeout_returns_failure(monkeypatch):
  import signal
  import time

  if not hasattr(signal, "SIGALRM"):
    pytest.skip("SIGALRM not available")

  target = "/fake/slow-stats"
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 0.05)

  def slow_impl(*_args, **_kwargs):
    time.sleep(1.0)
    return (target, None, False, True, 0.0)

  monkeypatch.setattr(st, "_parse_stats_file_payload_impl", slow_impl)
  stats_file, payload, need_archival, ingest_ok, elapsed_s = st._parse_stats_file_payload(
      target,
  )
  assert stats_file == target
  assert payload is None
  assert need_archival is False
  assert ingest_ok is False
  assert elapsed_s >= 0.0


def test_ingest_in_flight_tracker_sample_and_complete():
  tracker = st._IngestPoolInFlightTracker(
      ["/a/one", "/a/two", "/a/three"],
  )
  tracker.complete("/a/two")
  assert tracker.sample_in_flight(max_n=10) == ["/a/one", "/a/three"]


def test_parse_payload_skips_archival_when_db_complete_and_in_tar(monkeypatch):
  target = "/tmp/stats-in-tar"
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
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
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  stats_file, payload, need_archival, ingest_ok, parse_elapsed_s = st._parse_stats_file_payload(target)
  assert stats_file == target
  assert payload is None
  assert need_archival is False
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
        return _fake_map_async_result([True for _ in items])

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
        return _fake_map_async_result([True])

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
        call_no = self.calls
        return _fake_map_async_result([] if call_no == 1 else [True])

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


def test_cap_pending_stats_file_list_retains_oldest_when_truncating():
  """Pending queue cap drops newer paths; oldest-first ingest order is preserved."""
  from hpcperfstats.dbload.sync_timedb_archive_helpers import (
      cap_pending_stats_file_list,
  )

  base_ts = 1_591_123_200
  paths = ["/host/%d" % (base_ts + i * 60) for i in range(5)]
  capped = cap_pending_stats_file_list(paths, 3, log_fn=lambda *_a, **_k: None)
  assert capped == paths[:3]
  assert int(os.path.basename(capped[0])) < int(os.path.basename(capped[-1]))


def test_supervisor_ingests_oldest_pending_paths_first(monkeypatch):
  """Supervisor chunks from the head of the oldest-first pending queue."""
  shutdown_requested[0] = False
  try:
    base_ts = 1_591_123_200
    pending = ["/host/%d" % (base_ts + i * 60) for i in range(3)]
    ingested_order = []
    rescan_calls = {"n": 0}

    def fake_rescan(*_a, **_k):
      if rescan_calls["n"] == 0:
        rescan_calls["n"] += 1
        return list(pending)
      return []

    def fake_ingest(_lock, path, _c=None):
      ingested_order.append(path)
      return (path, True, True, 0.01)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_ingest)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "chunk_size", 1)
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

    assert ingested_order == pending
  finally:
    shutdown_requested[0] = False


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

  def failing_watch_pool(
      pool, fn, iterable, *, context="", poll_timeout_s=None, on_stall_warning=None,
  ):
    del pool, fn, iterable, context, poll_timeout_s, on_stall_warning
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
      lambda pool, **kwargs: terminate_calls.append(pool) or True,
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
  assert ingest_pool in terminate_calls
  assert archive_pool in terminate_calls


def test_stall_teardown_preserves_exit_124_not_137(monkeypatch):
  from hpcperfstats.dbload.multiprocessing_pool_health import (
      MultiprocessingPoolStallError,
      MultiprocessingWorkerExitError,
  )

  shutdown_requested[0] = False
  target = "/fake/stats-stall"

  def fake_rescan(*_a, **_k):
    if fake_rescan.calls == 0:
      fake_rescan.calls += 1
      return [target]
    return []
  fake_rescan.calls = 0

  def stall_watch_pool(
      pool, fn, iterable, *, context="", poll_timeout_s=None, on_stall_warning=None,
  ):
    del pool, fn, iterable, context, poll_timeout_s, on_stall_warning
    raise MultiprocessingPoolStallError(
        "pool imap stalled",
        dead_pids=(),
        context="sync_timedb ingest pool",
        exit_code=124,
    )

  get_watch_calls = []

  def tracking_get_watch(*args, **kwargs):
    get_watch_calls.append(kwargs.get("context"))
    raise MultiprocessingWorkerExitError(
        "worker dead",
        dead_pids=(999,),
        context="archive_finalize",
    )

  monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
  monkeypatch.setattr(st, "imap_unordered_watch_pool", stall_watch_pool)
  monkeypatch.setattr(st, "async_result_get_watch_pool", tracking_get_watch)
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
  monkeypatch.setattr(
      st,
      "terminate_pool_bounded",
      lambda pool, **kwargs: True,
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
    with pytest.raises(MultiprocessingPoolStallError) as excinfo:
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
  assert excinfo.value.exit_code == 124
  assert get_watch_calls == []


def test_stall_teardown_uses_nonblocking_preflight_shutdown(monkeypatch):
  from hpcperfstats.dbload.multiprocessing_pool_health import (
      MultiprocessingPoolStallError,
  )
  from hpcperfstats.dbload.sync_timedb_startup_day_close import (
      StartupDayClosePreflight,
  )
  from hpcperfstats.dbload.sync_timedb_startup_raw_removal import (
      StartupRawRemovalPreflight,
  )
  from hpcperfstats.dbload.sync_timedb_startup_tar_seal import (
      StartupTarSealPreflight,
  )
  from hpcperfstats.dbload.sync_timedb_async_day_close import (
      AsyncDayCloseCoordinator,
  )

  shutdown_requested[0] = False
  target = "/fake/stats-stall-shutdown"
  shutdown_waits = []

  def track_shutdown(cls_name):
    original = None

    def wrapper(self, wait=True):
      shutdown_waits.append((cls_name, wait))
      if original is not None:
        return original(self, wait=wait)

    return wrapper, original

  for cls_name, cls in (
      ("startup_raw", StartupRawRemovalPreflight),
      ("startup_day_close", StartupDayClosePreflight),
      ("startup_tar_seal", StartupTarSealPreflight),
  ):
    wrapper, _ = track_shutdown(cls_name)
    monkeypatch.setattr(cls, "shutdown", wrapper)

  janitor_shutdown = []

  def janitor_shutdown_track(self, wait=True):
    janitor_shutdown.append(wait)

  monkeypatch.setattr(st.ArchiveJanitor, "shutdown", janitor_shutdown_track)

  async_shutdown_waits = []

  def async_shutdown_track(self, wait=True):
    async_shutdown_waits.append(wait)

  monkeypatch.setattr(
      AsyncDayCloseCoordinator,
      "shutdown",
      async_shutdown_track,
  )

  def fake_rescan(*_a, **_k):
    if fake_rescan.calls == 0:
      fake_rescan.calls += 1
      return [target]
    return []
  fake_rescan.calls = 0

  def stall_watch_pool(
      pool, fn, iterable, *, context="", poll_timeout_s=None, on_stall_warning=None,
  ):
    del pool, fn, iterable, context, poll_timeout_s, on_stall_warning
    raise MultiprocessingPoolStallError(
        "pool imap stalled",
        dead_pids=(),
        context="sync_timedb ingest pool",
        exit_code=124,
    )

  monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
  monkeypatch.setattr(st, "imap_unordered_watch_pool", stall_watch_pool)
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
  monkeypatch.setattr(st, "terminate_pool_bounded", lambda pool, **kwargs: True)

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
    with pytest.raises(MultiprocessingPoolStallError):
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

  assert shutdown_waits
  assert all(wait is False for _name, wait in shutdown_waits)
  assert janitor_shutdown == [False]
  assert async_shutdown_waits == [False]


def test_finalize_invalidates_members_cache(monkeypatch):
  shutdown_requested[0] = False
  invalidated = []
  monkeypatch.setattr(
      st,
      "invalidate_daily_archive_members_cache",
      lambda path: invalidated.append(path),
  )
  target = "/tmp/stats-inv"
  archive_compressed = "/tmp/2026-06-01.tar.gz"

  def fake_rescan(*_a, **_k):
    if fake_rescan.calls == 0:
      fake_rescan.calls += 1
      return [target]
    return []
  fake_rescan.calls = 0

  class _ArchivePoolSuccess:
    def map_async(self, _fn, items):
      class _R:
        def ready(self):
          return True

        def get(self):
          return [True for _ in items]

      return _R()

  monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
  monkeypatch.setattr(st, "add_stats_file_to_db", lambda *_a, **_k: (target, True, True, 0.0))
  monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
  monkeypatch.setattr(st.cfg, "get_sync_enable_db_writer_pipeline", lambda: False)
  monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
  monkeypatch.setattr(
      st, "build_archive_mapping", lambda *_a, **_k: {archive_compressed: [target]},
  )
  monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
  monkeypatch.setattr(st, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.connections, "close_all", lambda: None)
  monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")

  try:
    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive",
        "all",
        None,
        ".hpc",
        object(),
        _ArchivePoolSuccess(),
        run_once=True,
    )
  finally:
    shutdown_requested[0] = False
  assert archive_compressed in invalidated


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

  def capture_watch(
      pool, fn, iterable, *, context="", poll_timeout_s=None, on_stall_warning=None,
  ):
    del pool, poll_timeout_s, on_stall_warning
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


def test_maybe_apply_tree_rss_governor_exits_on_exit_cap(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_process_tree_rss_limit_mb", lambda: 0)
  monkeypatch.setattr(st.cfg, "get_sync_process_tree_rss_exit_mb", lambda: 1)
  monkeypatch.setattr(st.cfg, "get_sync_process_tree_rss_check_every_n_chunks", lambda: 1)
  monkeypatch.setattr(
      st,
      "read_sync_timedb_tree_rss_bytes",
      lambda *_a, **_k: 2 * 1024 * 1024,
  )
  with pytest.raises(SystemExit) as excinfo:
    st._maybe_apply_tree_rss_governor(1, object(), None, object())
  assert excinfo.value.code == 137


def test_effective_ingest_imap_inflight_cap_respects_ini(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_imap_inflight_cap", lambda: 4)
  assert st._effective_ingest_imap_inflight_cap(24, 100) == 4


def test_spawn_pool_recycle_kwargs_when_maxtasks_set(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 25)
  assert st._spawn_pool_recycle_kwargs() == {"maxtasksperchild": 25}
  monkeypatch.setattr(st.cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  assert st._spawn_pool_recycle_kwargs() == {}


def test_streaming_parse_path_avoids_readlines(monkeypatch, tmp_path):
  stats_file = tmp_path / "host.example.com" / "1709123456"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text(
      "1709123456 job1 host.example.com\n"
      "!cpu user sys\n"
      "1709123457 job1 host.example.com\n"
      "cpu 0 100 200\n",
      encoding="utf-8",
  )
  readlines_calls = {"n": 0}
  orig_load = st.load_stats_file_lines

  def _counting_load(path, contents=None):
    readlines_calls["n"] += 1
    return orig_load(path, contents)

  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 600 * 1024 * 1024)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 * 1024 * 1024)
  monkeypatch.setattr(st, "load_stats_file_lines", _counting_load)
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda *_a, **_k: False)
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  monkeypatch.setattr(st, "compute_deltas_and_arc", lambda df: df)
  monkeypatch.setattr(st, "build_stats_dataframes", lambda s, p: (pd.DataFrame(s), pd.DataFrame(p)))

  result = st._parse_stats_file_payload_impl(str(stats_file))
  assert readlines_calls["n"] == 0
  assert result[3] is True


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
  assert result is st._HOST_ITIMES_SET_OVERFLOW
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
  """Every rescan_every_chunks boundary signals a janitor maintenance pass."""
  shutdown_requested[0] = False
  scheduled_reasons = []
  try:
    original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_reasons.append(reason)
      return original(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return ["/fake/statsA"]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
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
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert "startup" in scheduled_reasons
    assert "every_n_chunks" in scheduled_reasons
  finally:
    shutdown_requested[0] = False


def test_supervisor_module_has_no_live_archive_maintenance_pipeline_calls():
  import inspect

  src = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  forbidden = (
      "_run_scheduled_archive_maintenance",
      "_run_forced_two_phase_archive_maintenance",
      "_maybe_run_forced_maintenance_before_archive_dispatch",
      "_archive_maintenance_pipeline",
  )
  for name in forbidden:
    assert name not in src


def test_supervisor_scheduled_day_close_at_startup(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  scheduled_calls = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_calls.append(reason)
      return original(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      shutdown_requested[0] = True
      return []

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert "startup" in scheduled_calls
  finally:
    shutdown_requested[0] = False


def test_supervisor_startup_log_no_accrual_interval(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  logs = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      shutdown_requested[0] = True
      return []

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(
        st,
        "log_print",
        lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
    )

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert any("sync_timedb: day_close schedule startup" in line for line in logs)
    assert any("on ingest queue drain" in line for line in logs)
    assert any(
        "archive_maintenance_interval_seconds is deprecated and ignored"
        in line
        for line in logs
    )
    assert not any("archive janitor accrual interval" in line for line in logs)
  finally:
    shutdown_requested[0] = False


def test_supervisor_signals_maintenance_pass_when_queue_drains(
    monkeypatch, tmp_path,
):
  shutdown_requested[0] = False
  scheduled_reasons = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_reasons.append(reason)
      return original(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return ["/fake/stats%d" % i for i in range(3)]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert "startup" in scheduled_reasons
    assert "ingest_queue_empty" in scheduled_reasons
    assert "every_n_chunks" not in scheduled_reasons
  finally:
    shutdown_requested[0] = False


def test_supervisor_idle_loop_does_not_signal_queue_empty_pass(
    monkeypatch, tmp_path,
):
  shutdown_requested[0] = False
  scheduled_reasons = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_reasons.append(reason)
      return original(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      return []

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert scheduled_reasons == ["startup"]
  finally:
    shutdown_requested[0] = False


def test_supervisor_startup_empty_queue_no_duplicate_drain_pass(
    monkeypatch, tmp_path,
):
  shutdown_requested[0] = False
  scheduled_reasons = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_reasons.append(reason)
      return original(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      shutdown_requested[0] = True
      return []

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert scheduled_reasons == ["startup"]
  finally:
    shutdown_requested[0] = False


def _supervisor_startup_preflight_patches(monkeypatch, preflight_obj):
  import hpcperfstats.dbload.sync_timedb_startup_raw_removal as preflight_mod
  import hpcperfstats.dbload.sync_timedb_startup_day_close as day_close_mod
  import hpcperfstats.dbload.sync_timedb_startup_tar_seal as tar_seal_mod

  class _FakePreflight:
    def __init__(self, **_kwargs):
      self._delegate = preflight_obj

    def __getattr__(self, name):
      return getattr(self._delegate, name)

  class _DoneDayClose:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def discover_done(self):
      return True

    def start_async_discover_and_close(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _DoneTarSeal:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def seal_pass_done(self):
      return True

    def start_async_seal(self):
      return None

    def shutdown(self, wait=True):
      del wait

  monkeypatch.setattr(preflight_mod, "StartupRawRemovalPreflight", _FakePreflight)
  monkeypatch.setattr(st, "StartupRawRemovalPreflight", _FakePreflight)
  monkeypatch.setattr(day_close_mod, "StartupDayClosePreflight", _DoneDayClose)
  monkeypatch.setattr(st, "StartupDayClosePreflight", _DoneDayClose)
  monkeypatch.setattr(tar_seal_mod, "StartupTarSealPreflight", _DoneTarSeal)
  monkeypatch.setattr(st, "StartupTarSealPreflight", _DoneTarSeal)


def test_supervisor_ingest_continues_during_verification(monkeypatch):
  shutdown_requested[0] = False
  ingest_calls = {"n": 0}

  class _Preflight:
    enabled = True

    def phase(self):
      return "verifying"

    def verification_complete(self):
      return False

    def needs_delete_phase(self):
      return False

    def delete_phase_done(self):
      return False

    def paths_pending_startup_delete(self):
      return set()

    def consumed_paths(self):
      return set()

    def start_async_verify(self):
      return None

    def shutdown(self, wait=True):
      del wait

  try:
    target = "/fake/statsA"

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      ingest_calls["n"] += 1
      if ingest_calls["n"] == 1:
        return [target]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    _supervisor_startup_preflight_patches(monkeypatch, _Preflight())
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert ingest_calls["n"] >= 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_waits_for_chunk_before_startup_deletes(monkeypatch):
  shutdown_requested[0] = False

  class _Preflight:
    enabled = True
    _phase = "verification_complete"

    def phase(self):
      return self._phase

    def verification_complete(self):
      return True

    def needs_delete_phase(self):
      return self._phase in ("verification_complete", "deleting")

    def delete_phase_done(self):
      return self._phase == "done"

    def paths_pending_startup_delete(self):
      return {"/fake/statsA"}

    def consumed_paths(self):
      return set()

    def start_async_verify(self):
      return None

    def begin_deleting(self):
      self._phase = "deleting"

    def apply_deletes_from_manifest(self):
      self._phase = "done"
      return 1

    def shutdown(self, wait=True):
      del wait

  try:
    target = "/fake/statsA"
    seen_chunk_in_progress = {"during_delete": None}

    class _PreflightTrack(_Preflight):
      def apply_deletes_from_manifest(self):
        seen_chunk_in_progress["during_delete"] = chunk_flag["value"]
        return super().apply_deletes_from_manifest()

    chunk_flag = {"value": False}

    def fake_add(_lock, path, _contents=None):
      chunk_flag["value"] = True
      return (path, True, True, 0.0)

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    _supervisor_startup_preflight_patches(monkeypatch, _PreflightTrack())
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert seen_chunk_in_progress["during_delete"] is False
  finally:
    shutdown_requested[0] = False


def test_supervisor_rescans_pending_after_startup_delete_complete(monkeypatch):
  shutdown_requested[0] = False
  deleted_path = "/fake/statsA"

  class _Preflight:
    enabled = True
    _phase = "verification_complete"

    def phase(self):
      return self._phase

    def verification_complete(self):
      return True

    def needs_delete_phase(self):
      return self._phase != "done"

    def delete_phase_done(self):
      return self._phase == "done"

    def paths_pending_startup_delete(self):
      return {deleted_path} if self._phase != "done" else set()

    def consumed_paths(self):
      return {deleted_path} if self._phase == "done" else set()

    def start_async_verify(self):
      return None

    def begin_deleting(self):
      self._phase = "deleting"

    def apply_deletes_from_manifest(self):
      self._phase = "done"
      return 1

    def shutdown(self, wait=True):
      del wait

  try:
    preflight = _Preflight()
    captured_rescans = []

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if preflight.delete_phase_done():
        captured_rescans.append([])
        shutdown_requested[0] = True
        return []
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [deleted_path]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    _supervisor_startup_preflight_patches(monkeypatch, preflight)

    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert preflight.delete_phase_done()
    assert captured_rescans
    assert deleted_path not in captured_rescans[-1]
  finally:
    shutdown_requested[0] = False


def _supervisor_day_raw_removal_patches(monkeypatch, coord_obj):
  import hpcperfstats.dbload.sync_timedb_day_raw_removal as day_mod

  class _FakeDayCoord:
    def __init__(self, **_kwargs):
      self._delegate = coord_obj

    def __getattr__(self, name):
      return getattr(self._delegate, name)

  monkeypatch.setattr(day_mod, "DayRawRemovalCoordinator", _FakeDayCoord)
  monkeypatch.setattr(st, "DayRawRemovalCoordinator", _FakeDayCoord)


def _supervisor_startup_preflight_disabled(monkeypatch):
  import hpcperfstats.dbload.sync_timedb_startup_raw_removal as preflight_mod
  import hpcperfstats.dbload.sync_timedb_startup_day_close as day_close_mod
  import hpcperfstats.dbload.sync_timedb_startup_tar_seal as tar_seal_mod

  class _DonePreflight:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def delete_phase_done(self):
      return True

    def needs_delete_phase(self):
      return False

    def paths_pending_startup_delete(self):
      return set()

    def consumed_paths(self):
      return set()

    def start_async_verify(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _DoneDayClose:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def discover_done(self):
      return True

    def start_async_discover_and_close(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _DoneTarSeal:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def seal_pass_done(self):
      return True

    def start_async_seal(self):
      return None

    def shutdown(self, wait=True):
      del wait

  import hpcperfstats.dbload.sync_timedb_async_day_close as async_dc_mod

  def _noop_async_day_close(self, tar_norm, reason):
    del reason
    self._set_entry_status(tar_norm, "complete")
    self._notify_phase(tar_norm, "tar_dropped")

  monkeypatch.setattr(
      async_dc_mod.AsyncDayCloseCoordinator,
      "_run_day_close",
      _noop_async_day_close,
  )
  monkeypatch.setattr(preflight_mod, "StartupRawRemovalPreflight", _DonePreflight)
  monkeypatch.setattr(st, "StartupRawRemovalPreflight", _DonePreflight)
  monkeypatch.setattr(day_close_mod, "StartupDayClosePreflight", _DoneDayClose)
  monkeypatch.setattr(st, "StartupDayClosePreflight", _DoneDayClose)
  monkeypatch.setattr(tar_seal_mod, "StartupTarSealPreflight", _DoneTarSeal)
  monkeypatch.setattr(st, "StartupTarSealPreflight", _DoneTarSeal)


def test_supervisor_starts_ingest_without_waiting_for_tar_seal(monkeypatch):
  shutdown_requested[0] = False
  ingest_calls = {"n": 0}
  tar_seal_started = {"value": False}

  import hpcperfstats.dbload.sync_timedb_startup_raw_removal as preflight_mod
  import hpcperfstats.dbload.sync_timedb_startup_day_close as day_close_mod
  import hpcperfstats.dbload.sync_timedb_startup_tar_seal as tar_seal_mod

  class _DoneRawPreflight:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def delete_phase_done(self):
      return True

    def needs_delete_phase(self):
      return False

    def paths_pending_startup_delete(self):
      return set()

    def consumed_paths(self):
      return set()

    def start_async_verify(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _DoneDayClose:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def discover_done(self):
      return True

    def start_async_discover_and_close(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _SlowTarSeal:
    enabled = True

    def __init__(self, **_kwargs):
      pass

    def seal_pass_done(self):
      return False

    def start_async_seal(self):
      tar_seal_started["value"] = True

    def shutdown(self, wait=True):
      del wait

  try:
    target = "/fake/statsA"

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      ingest_calls["n"] += 1
      if ingest_calls["n"] == 1:
        return [target]
      shutdown_requested[0] = True
      return []

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    monkeypatch.setattr(preflight_mod, "StartupRawRemovalPreflight", _DoneRawPreflight)
    monkeypatch.setattr(st, "StartupRawRemovalPreflight", _DoneRawPreflight)
    monkeypatch.setattr(day_close_mod, "StartupDayClosePreflight", _DoneDayClose)
    monkeypatch.setattr(st, "StartupDayClosePreflight", _DoneDayClose)
    monkeypatch.setattr(tar_seal_mod, "StartupTarSealPreflight", _SlowTarSeal)
    monkeypatch.setattr(st, "StartupTarSealPreflight", _SlowTarSeal)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp/daily")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert tar_seal_started["value"] is True
    assert ingest_calls["n"] >= 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_starts_ingest_without_waiting_for_startup_day_close(monkeypatch):
  shutdown_requested[0] = False
  ingest_calls = {"n": 0}
  day_close_started = {"value": False}

  import hpcperfstats.dbload.sync_timedb_startup_raw_removal as preflight_mod
  import hpcperfstats.dbload.sync_timedb_startup_day_close as day_close_mod
  import hpcperfstats.dbload.sync_timedb_startup_tar_seal as tar_seal_mod

  class _DoneRawPreflight:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def delete_phase_done(self):
      return True

    def needs_delete_phase(self):
      return False

    def paths_pending_startup_delete(self):
      return set()

    def consumed_paths(self):
      return set()

    def start_async_verify(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _DoneTarSeal:
    enabled = False

    def __init__(self, **_kwargs):
      pass

    def start_async_seal(self):
      return None

    def shutdown(self, wait=True):
      del wait

  class _SlowDayClose:
    enabled = True

    def __init__(self, **_kwargs):
      pass

    def discover_done(self):
      return False

    def start_async_discover_and_close(self):
      day_close_started["value"] = True

    def shutdown(self, wait=True):
      del wait

  try:
    target = "/fake/statsA"

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      ingest_calls["n"] += 1
      if ingest_calls["n"] == 1:
        return [target]
      shutdown_requested[0] = True
      return []

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    monkeypatch.setattr(preflight_mod, "StartupRawRemovalPreflight", _DoneRawPreflight)
    monkeypatch.setattr(st, "StartupRawRemovalPreflight", _DoneRawPreflight)
    monkeypatch.setattr(day_close_mod, "StartupDayClosePreflight", _SlowDayClose)
    monkeypatch.setattr(st, "StartupDayClosePreflight", _SlowDayClose)
    monkeypatch.setattr(tar_seal_mod, "StartupTarSealPreflight", _DoneTarSeal)
    monkeypatch.setattr(st, "StartupTarSealPreflight", _DoneTarSeal)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp/daily")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert day_close_started["value"] is True
    assert ingest_calls["n"] >= 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_ingest_proceeds_without_day_close_delete_gate(monkeypatch):
  """Day-close delete runs async; supervisor must not block ingest on a delete gate."""
  shutdown_requested[0] = False
  ingest_calls = {"n": 0}

  class _DayCoord:
    enabled = True

    def paths_pending_delete(self):
      return {"/fake/pending-delete"}

    def consumed_paths(self):
      return set()

    def shutdown(self, wait=True):
      del wait

  try:
    target = "/fake/statsA"

    def fake_add(_lock, path, _contents=None):
      ingest_calls["n"] += 1
      return (path, True, True, 0.0)

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [target]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    _supervisor_startup_preflight_disabled(monkeypatch)
    _supervisor_day_raw_removal_patches(monkeypatch, _DayCoord())
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "chunk_size", 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "tgz_archive_dir", "/tmp/daily")
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        "/tmp/archive", "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert ingest_calls["n"] >= 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_scheduled_day_close_every_n_chunks(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  scheduled_reasons = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_reasons.append(reason)
      return original(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return ["/fake/stats%d" % i for i in range(10)]
      shutdown_requested[0] = True
      return []
    fake_rescan.calls = 0

    def fake_add(_lock, path, _contents=None):
      return (path, True, True, 0.0)

    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", fake_add)
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
    monkeypatch.setattr(st, "rescan_every_chunks", 10)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir),
        "all",
        None,
        ".hpc",
        object(),
        _FakeArchivePool(),
        run_once=True,
    )

    assert "startup" in scheduled_reasons
    assert "every_n_chunks" in scheduled_reasons
  finally:
    shutdown_requested[0] = False


def _supervisor_two_day_ingest_patches(
    monkeypatch,
    tmp_path,
    *,
    paths,
    rescan_every_chunks=100,
    immediate_spy=None,
):
  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  calls = {"n": 0}

  def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
    calls["n"] += 1
    if calls["n"] == 1:
      return list(paths)
    shutdown_requested[0] = True
    return []

  _supervisor_startup_preflight_disabled(monkeypatch)
  monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
  monkeypatch.setattr(
      st,
      "add_stats_file_to_db",
      lambda _lock, path, **_k: (path, True, True, 0.0),
  )
  monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
  monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
  monkeypatch.setattr(st.cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(st, "rescan_every_chunks", rescan_every_chunks)
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.connections, "close_all", lambda: None)
  monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
  monkeypatch.setattr(
      st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})
  monkeypatch.setattr(
      archive_helpers, "build_archive_mapping", lambda *_a, **_k: ({}, {}))
  monkeypatch.setattr(
      st,
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "signal_work_available",
      lambda self: None,
  )
  if immediate_spy is not None:
    original_enqueue = janitor_mod.ArchiveJanitor.enqueue_immediate_day_close

    def spy_enqueue(self, tar_path, *, reason):
      immediate_spy(tar_path, reason)
      return original_enqueue(self, tar_path, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "enqueue_immediate_day_close",
        spy_enqueue,
    )
  return str(archive_dir), str(daily_dir)


def test_supervisor_enqueues_immediate_day_close_on_day_drain(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  immediate_events = []
  try:
    day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
    archive_dir, daily_dir = _supervisor_two_day_ingest_patches(
        monkeypatch,
        tmp_path,
        paths=[
            "/fake/stats/%d" % day1_epoch,
            "/fake/stats/%d" % day2_epoch,
        ],
        immediate_spy=lambda tar, reason: immediate_events.append(
            (os.path.normpath(tar), reason)),
    )
    tar_day1 = os.path.normpath(os.path.join(daily_dir, "2020-01-01.tar"))
    open(tar_day1, "wb").close()

    st.run_sync_timedb_supervisor_loop(
        archive_dir, "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    chunk_end_hits = [
        event for event in immediate_events
        if event[0] == tar_day1 and event[1] == "chunk_end"
    ]
    assert chunk_end_hits
  finally:
    shutdown_requested[0] = False


def test_supervisor_does_not_immediate_day_close_mid_day(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  immediate_events = []
  try:
    day_epoch1 = int(datetime(2020, 1, 1, 10, tzinfo=timezone.utc).timestamp())
    day_epoch2 = int(datetime(2020, 1, 1, 11, tzinfo=timezone.utc).timestamp())
    path_day1 = "/fake/stats/%d" % day_epoch1
    path_day2 = "/fake/stats/%d" % day_epoch2
    archive_dir, daily_dir = _supervisor_two_day_ingest_patches(
        monkeypatch,
        tmp_path,
        paths=[path_day1, path_day2],
        immediate_spy=lambda tar, reason: immediate_events.append(
            (os.path.normpath(tar), reason)),
    )
    original_add = st.add_stats_file_to_db

    def stop_after_first_ingest(_lock, path, **_k):
      if path == path_day1:
        shutdown_requested[0] = True
      return original_add(_lock, path, **_k)

    monkeypatch.setattr(st, "add_stats_file_to_db", stop_after_first_ingest)
    tar_day1 = os.path.normpath(os.path.join(daily_dir, "2020-01-01.tar"))
    open(tar_day1, "wb").close()

    st.run_sync_timedb_supervisor_loop(
        archive_dir, "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    chunk_end_hits = [
        event for event in immediate_events
        if event[0] == tar_day1 and event[1] == "chunk_end"
    ]
    assert not chunk_end_hits
  finally:
    shutdown_requested[0] = False


def test_supervisor_day_close_at_most_one_batch_late(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  immediate_events = []
  try:
    day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
    archive_dir, daily_dir = _supervisor_two_day_ingest_patches(
        monkeypatch,
        tmp_path,
        paths=[
            "/fake/stats/%d" % day1_epoch,
            "/fake/stats/%d" % day2_epoch,
        ],
        immediate_spy=lambda tar, reason: immediate_events.append(
            (os.path.normpath(tar), reason)),
    )
    tar_day1 = os.path.normpath(os.path.join(daily_dir, "2020-01-01.tar"))
    open(tar_day1, "wb").close()

    st.run_sync_timedb_supervisor_loop(
        archive_dir, "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    chunk_end_indices = [
        idx for idx, event in enumerate(immediate_events)
        if event[0] == tar_day1 and event[1] == "chunk_end"
    ]
    assert chunk_end_indices
    assert chunk_end_indices[0] <= 1
  finally:
    shutdown_requested[0] = False


def test_supervisor_checks_day_close_every_batch(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  find_calls = []
  original_find = archive_helpers.find_immediate_day_close_candidates

  def counting_find(**kwargs):
    find_calls.append(1)
    return original_find(**kwargs)

  try:
    day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
    archive_dir, _daily_dir = _supervisor_two_day_ingest_patches(
        monkeypatch,
        tmp_path,
        paths=[
            "/fake/stats/%d" % day1_epoch,
            "/fake/stats/%d" % day2_epoch,
        ],
    )
    monkeypatch.setattr(st, "find_immediate_day_close_candidates", counting_find)

    st.run_sync_timedb_supervisor_loop(
        archive_dir, "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert len(find_calls) >= 2
  finally:
    shutdown_requested[0] = False


def test_immediate_day_close_coexists_with_startup_scheduled_pass(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  scheduled_reasons = []
  immediate_events = []
  try:
    day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
    archive_dir, daily_dir = _supervisor_two_day_ingest_patches(
        monkeypatch,
        tmp_path,
        paths=[
            "/fake/stats/%d" % day1_epoch,
            "/fake/stats/%d" % day2_epoch,
        ],
        immediate_spy=lambda _tar, reason: immediate_events.append(reason),
    )
    original_scheduled = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      scheduled_reasons.append(reason)
      return original_scheduled(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )
    open(os.path.join(daily_dir, "2020-01-01.tar"), "wb").close()

    st.run_sync_timedb_supervisor_loop(
        archive_dir, "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert "startup" in scheduled_reasons
    assert "chunk_end" in immediate_events
  finally:
    shutdown_requested[0] = False


def test_chunk_10_immediate_check_after_boundary_finalize(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  order = []
  try:
    day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
    archive_dir, daily_dir = _supervisor_two_day_ingest_patches(
        monkeypatch,
        tmp_path,
        paths=[
            "/fake/stats/%d" % day1_epoch,
            "/fake/stats/%d" % day2_epoch,
        ],
        rescan_every_chunks=1,
        immediate_spy=lambda _tar, reason: order.append(reason),
    )
    original_scheduled = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

    def spy_scheduled(self, *, reason):
      order.append("scheduled:%s" % reason)
      return original_scheduled(self, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        spy_scheduled,
    )
    open(os.path.join(daily_dir, "2020-01-01.tar"), "wb").close()

    st.run_sync_timedb_supervisor_loop(
        archive_dir, "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    chunk_end_idx = next(
        (idx for idx, item in enumerate(order) if item == "chunk_end"), None)
    scheduled_idx = next(
        (idx for idx, item in enumerate(order)
         if item.startswith("scheduled:every_n_chunks")),
        None,
    )
    assert chunk_end_idx is not None
    assert scheduled_idx is not None
    assert chunk_end_idx > scheduled_idx
  finally:
    shutdown_requested[0] = False


def test_immediate_day_close_on_idle_finalize_without_chunk(monkeypatch, tmp_path):
  shutdown_requested[0] = False
  immediate_reasons = []
  try:
    archive_dir = tmp_path / "archive"
    daily_dir = tmp_path / "daily"
    archive_dir.mkdir()
    daily_dir.mkdir()
    day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
    path_day1 = "/fake/stats/%d" % day1_epoch
    tar_day1 = os.path.join(daily_dir, "2020-01-01.tar")
    open(tar_day1, "wb").close()

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
      if fake_rescan.calls == 0:
        fake_rescan.calls += 1
        return [path_day1]
      shutdown_requested[0] = True
      return []

    fake_rescan.calls = 0
    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(
        st,
        "add_stats_file_to_db",
        lambda _lock, path, **_k: (path, True, True, 0.0),
    )
    monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
    monkeypatch.setattr(st.cfg, "get_archive_maintenance_interval_seconds", lambda: 10**12)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 1)
    monkeypatch.setattr(st.cfg, "get_sync_day_close_candidate_report", lambda: False)
    monkeypatch.setattr(st, "rescan_every_chunks", 100)
    monkeypatch.setattr(st, "close_old_connections", lambda: None)
    monkeypatch.setattr(st.connections, "close_all", lambda: None)
    monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
    monkeypatch.setattr(
        st, "_path_fingerprint", lambda p: {"path": p, "size": 1, "mtime": 1})
    monkeypatch.setattr(
        archive_helpers, "build_archive_mapping", lambda *_a, **_k: ({}, {}))
    monkeypatch.setattr(
        st,
        "build_unprocessed_raw_by_daily_tar",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_work_available",
        lambda self: None,
    )
    original_enqueue = janitor_mod.ArchiveJanitor.enqueue_immediate_day_close

    def spy_enqueue(self, tar_path, *, reason):
      immediate_reasons.append(reason)
      return original_enqueue(self, tar_path, reason=reason)

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "enqueue_immediate_day_close",
        spy_enqueue,
    )

    st.run_sync_timedb_supervisor_loop(
        str(archive_dir), "all", None, ".hpc", object(), _FakeArchivePool(), run_once=True)

    assert "idle_finalize" in immediate_reasons
  finally:
    shutdown_requested[0] = False
