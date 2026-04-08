"""Unit tests for sync_timedb supervisor loop (no real multiprocessing or DB)."""

from collections import deque
from pathlib import Path
import json

import hpcperfstats.dbload.sync_timedb as st
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


def test_supervisor_sleeps_when_empty_then_ingests_then_sleeps(monkeypatch):
  shutdown_requested[0] = False
  try:
    rescans = deque([[], ["/fake/stats0"], []])

    def fake_rescan(*a, **k):
      if rescans:
        return list(rescans.popleft())
      return []

    sleeps = []

    def fake_sleep(secs):
      sleeps.append(secs)
      if len(sleeps) >= 2:
        shutdown_requested[0] = True

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
    monkeypatch.setattr(st, "seal_dirty_daily_archives", lambda *a, **k: None)
    monkeypatch.setattr(
        st, "remove_verified_archived_raw_files", lambda *a, **k: None)
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

    assert sleeps == [
        st.EMPTY_QUEUE_RESCAN_SLEEP_SECONDS,
        st.EMPTY_QUEUE_RESCAN_SLEEP_SECONDS,
    ]
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

    def fake_rescan(_directory, _start, _end, _ext, processed_files):
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

    def fake_rescan(_directory, _start, _end, _ext, processed_files):
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
