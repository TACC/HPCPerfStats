"""Concurrent regression coverage for FT-safe process-local caches."""
from __future__ import annotations

import threading
import time
from pathlib import Path


def test_mark_entries_cache_concurrent_get_set_clear(tmp_path: Path) -> None:
  """
  Concurrent load/clear of mark entries must not raise or corrupt entries.
  """
  from hpcperfstats.dbload.lib.sync_timedb_mark_entries_cache import (
      clear_mark_entries_cache,
      load_cached_mark_entries,
      reset_mark_entries_cache_for_tests,
  )

  mark = tmp_path / "mark.json"
  mark.write_text('{"a": {"ok": true}}\n', encoding="utf-8")
  reset_mark_entries_cache_for_tests()
  errors: list[BaseException] = []
  barrier = threading.Barrier(8)

  def _worker(idx: int) -> None:
    try:
      barrier.wait(timeout=5)
      for _ in range(40):
        entries = load_cached_mark_entries(
            str(mark),
            load_uncached=lambda _p: {"a": {"ok": True}},
        )
        assert entries.get("a", {}).get("ok") is True
        if idx % 2 == 0:
          clear_mark_entries_cache(str(mark))
    except BaseException as exc:  # noqa: BLE001 — collect for assertion
      errors.append(exc)

  threads = [
      threading.Thread(target=_worker, args=(i,), name="mark-cache-%d" % i)
      for i in range(8)
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=30)
  assert not errors, errors
  reset_mark_entries_cache_for_tests()


def test_ingest_readiness_caches_expose_ft_safe_locks() -> None:
  """
  Head/path readiness caches must expose RLocks for free-threaded access.
  """
  import threading

  from hpcperfstats.dbload.lib import sync_timedb_ingest_readiness as ready

  assert isinstance(ready._HEAD_DB_CACHE_LOCK, type(threading.RLock()))
  assert isinstance(ready._PATH_READY_CACHE_LOCK, type(threading.RLock()))
  ready.reset_sync_ingest_readiness_caches()
  errors: list[BaseException] = []
  barrier = threading.Barrier(8)

  def _worker(idx: int) -> None:
    try:
      barrier.wait(timeout=5)
      for n in range(50):
        key = ("host%d" % (idx % 3), n % 10)
        now = time.time()
        with ready._HEAD_DB_CACHE_LOCK:
          ready._HEAD_DB_CACHE[key] = {"present": True, "checked_at": now}
          ready._trim_head_db_cache()
        fp = ("/tmp/p%d" % (idx % 4), n, n)
        with ready._PATH_READY_CACHE_LOCK:
          ready._PATH_READY_CACHE[fp] = {"ready": True, "checked_at": now}
          ready._trim_path_ready_cache()
        if n % 7 == 0:
          ready.reset_sync_ingest_readiness_caches()
    except BaseException as exc:  # noqa: BLE001
      errors.append(exc)

  threads = [
      threading.Thread(target=_worker, args=(i,), name="ready-cache-%d" % i)
      for i in range(8)
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=30)
  assert not errors, errors
  ready.reset_sync_ingest_readiness_caches()


def test_host_itimes_caches_expose_ft_safe_locks() -> None:
  """
  Host itimes caches must expose RLocks for free-threaded access.
  """
  import threading

  from hpcperfstats.dbload.lib import sync_timedb_host_itimes as itimes

  assert isinstance(itimes._HOST_ITIMES_CACHE_LOCK, type(threading.RLock()))
  assert isinstance(
      itimes._HOST_SECOND_PRESENT_CACHE_LOCK,
      type(threading.RLock()),
  )
  itimes.reset_host_itimes_caches()
  errors: list[BaseException] = []
  barrier = threading.Barrier(8)

  def _worker(idx: int) -> None:
    try:
      barrier.wait(timeout=5)
      for n in range(50):
        key = ("h%d" % (idx % 3), n, n + 1)
        now = time.time()
        with itimes._HOST_ITIMES_CACHE_LOCK:
          itimes._HOST_ITIMES_CACHE[key] = {
              "times": (n,),
              "checked_at": now,
          }
          if len(itimes._HOST_ITIMES_CACHE) > 10:
            drop = next(iter(itimes._HOST_ITIMES_CACHE))
            itimes._HOST_ITIMES_CACHE.pop(drop, None)
        with itimes._HOST_SECOND_PRESENT_CACHE_LOCK:
          itimes._HOST_SECOND_PRESENT_CACHE[("h", n)] = (True, now)
        if n % 11 == 0:
          itimes.reset_host_itimes_caches()
    except BaseException as exc:  # noqa: BLE001
      errors.append(exc)

  threads = [
      threading.Thread(target=_worker, args=(i,), name="itimes-cache-%d" % i)
      for i in range(8)
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=30)
  assert not errors, errors
  itimes.reset_host_itimes_caches()


def test_daily_archive_members_l1_cache_concurrent(monkeypatch, tmp_path: Path) -> None:
  """
  Concurrent L1 members get/set/invalidate/merge must not raise.
  """
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  helpers.clear_daily_archive_members_cache()
  day = tmp_path / "2024-01-01.tar.zst"
  day.write_bytes(b"x")
  monkeypatch.setattr(
      helpers,
      "_daily_archive_members_cache_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      helpers,
      "_daily_archive_members_cache_key",
      lambda canonical: (canonical, "id"),
  )
  monkeypatch.setattr(
      helpers,
      "normalize_daily_compressed_path",
      lambda path: str(path),
  )
  errors: list[BaseException] = []
  barrier = threading.Barrier(8)
  path = str(day)

  def _worker(idx: int) -> None:
    try:
      barrier.wait(timeout=5)
      for n in range(40):
        helpers._store_daily_archive_members_cache(
            path,
            {"m%d" % n: n + idx},
        )
        helpers._lookup_daily_archive_members_cache(path)
        helpers.merge_daily_archive_members_l1_cache(
            path,
            {"m%d" % n: n + 1},
        )
        if n % 5 == 0:
          helpers.invalidate_daily_archive_members_cache(path, reason="test")
        if n % 9 == 0:
          helpers.clear_daily_archive_members_cache()
    except BaseException as exc:  # noqa: BLE001
      errors.append(exc)

  threads = [
      threading.Thread(target=_worker, args=(i,), name="l1-cache-%d" % i)
      for i in range(8)
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=30)
  assert not errors, errors
  helpers.clear_daily_archive_members_cache()


def test_mark_entries_cache_exposes_ft_safe_lock() -> None:
  """Mark entries cache must expose an RLock for free-threaded access."""
  import threading

  from hpcperfstats.dbload.lib import sync_timedb_mark_entries_cache as mark

  assert isinstance(mark._ENTRIES_CACHE_LOCK, type(threading.RLock()))


def test_daily_archive_members_l1_exposes_ft_safe_lock() -> None:
  """Daily archive L1 members cache must expose an RLock."""
  import threading

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  assert isinstance(
      helpers._DAILY_ARCHIVE_MEMBERS_CACHE_LOCK,
      type(threading.RLock()),
  )
  assert isinstance(
      helpers._MUTABLE_TAR_AUTHORITY_MEMBERS_CACHE_LOCK,
      type(threading.RLock()),
  )
