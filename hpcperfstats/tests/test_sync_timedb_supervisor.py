"""Unit tests for sync_timedb supervisor loop (no real multiprocessing or DB)."""

from collections import deque

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
