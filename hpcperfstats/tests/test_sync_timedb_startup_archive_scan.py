"""Unit tests for startup archive scan single-flight coordinator."""

import threading
import time
from unittest.mock import MagicMock


from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
    copy_archive_maintenance_snapshot,
)


def _make_snapshot(*, closed_paths=None, mapping=None):
  return ArchiveMaintenanceSnapshot(
      closed_paths=list(closed_paths or ["/raw/a/1", "/raw/b/2"]),
      mapping=dict(mapping or {"/daily/2022-01-01.tar": ["/raw/a/1"]}),
      remaining_raw_by_gz={"/daily/2022-01-01.tar": ["/raw/b/2"]},
  )


def test_copy_archive_maintenance_snapshot_deep_copies_lists():
  snap = _make_snapshot()
  copied = copy_archive_maintenance_snapshot(snap)
  snap.mapping["/daily/2022-01-01.tar"].append("/raw/c/3")
  snap.remaining_raw_by_gz["/daily/2022-01-01.tar"].append("/raw/d/4")
  assert "/raw/c/3" not in copied.mapping["/daily/2022-01-01.tar"]
  assert "/raw/d/4" not in copied.remaining_raw_by_gz["/daily/2022-01-01.tar"]


def test_publish_deep_copy_isolated_from_mutations():
  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  snap = _make_snapshot()
  coord.publish(snap, from_janitor=True)
  published = coord.get_snapshot()
  snap.mapping["/daily/2022-01-01.tar"].append("/mutated")
  assert "/mutated" not in published.mapping["/daily/2022-01-01.tar"]


def test_coordinator_wait_never_returns_none_parallel_build(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan.SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS",
      0.5,
  )
  collect_calls = {"n": 0}

  def slow_build():
    collect_calls["n"] += 1
    time.sleep(0.05)
    return _make_snapshot()

  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  results = []
  errors = []
  ready = threading.Barrier(4)

  def waiter():
    try:
      ready.wait(timeout=2.0)
      snap = coord.wait_for_snapshot(allow_build=True, build_fn=slow_build)
      results.append(snap)
    except Exception as exc:
      errors.append(exc)

  threads = [threading.Thread(target=waiter) for _ in range(3)]
  for thread in threads:
    thread.start()
  ready.wait(timeout=2.0)
  for thread in threads:
    thread.join(timeout=5.0)
  assert not errors
  assert len(results) == 3
  assert all(result is not None for result in results)
  assert collect_calls["n"] == 1


def test_janitor_begin_build_blocks_fallback_collect(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan.SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS",
      0.5,
  )
  collect_calls = {"n": 0}

  def fallback_build():
    collect_calls["n"] += 1
    return _make_snapshot()

  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  coord.note_startup_maintenance_pending()
  coord.begin_build()

  def waiter():
    coord.wait_for_snapshot(allow_build=True, build_fn=fallback_build)

  thread = threading.Thread(target=waiter)
  thread.start()
  time.sleep(0.1)
  assert collect_calls["n"] == 0
  coord.publish(_make_snapshot(), from_janitor=True)
  thread.join(timeout=3.0)
  assert collect_calls["n"] == 0
  assert coord.get_snapshot() is not None


def test_startup_single_archive_scan_shared_across_preflights(monkeypatch):
  """Three preflight-style waiters share one janitor publish (one collect)."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan.SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS",
      3.0,
  )
  collect_calls = {"n": 0}

  def fallback_build():
    collect_calls["n"] += 1
    return _make_snapshot(closed_paths=["/only/one/collect"])

  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
      log_fn=MagicMock(),
  )
  coord.note_startup_maintenance_pending()
  snapshots = []

  def preflight_wait():
    snapshots.append(
        coord.wait_for_snapshot(allow_build=True, build_fn=fallback_build),
    )

  threads = [threading.Thread(target=preflight_wait) for _ in range(3)]
  for thread in threads:
    thread.start()
  time.sleep(0.05)
  coord.begin_build()
  time.sleep(0.05)
  coord.publish(_make_snapshot(closed_paths=["/janitor/publish"]), from_janitor=True)
  for thread in threads:
    thread.join(timeout=5.0)
  assert collect_calls["n"] == 0
  assert len(snapshots) == 3
  assert all(s.closed_paths == ["/janitor/publish"] for s in snapshots)


def test_preflight_wait_never_fallback_builds(monkeypatch):
  """Preflight-style wait (allow_build=False) must not run fallback collect."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan.SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS",
      0.2,
  )
  collect_calls = {"n": 0}

  def fallback_build():
    collect_calls["n"] += 1
    return _make_snapshot()

  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  coord.note_startup_maintenance_pending()
  coord.begin_build()

  result = {"snap": None, "error": None}

  def preflight_wait():
    try:
      result["snap"] = coord.wait_for_snapshot(
          allow_build=False,
          build_fn=fallback_build,
      )
    except Exception as exc:
      result["error"] = exc

  thread = threading.Thread(target=preflight_wait)
  thread.start()
  time.sleep(0.05)
  assert collect_calls["n"] == 0
  coord.publish(_make_snapshot(closed_paths=["/janitor/only"]), from_janitor=True)
  thread.join(timeout=3.0)
  assert result["error"] is None
  assert result["snap"] is not None
  assert collect_calls["n"] == 0


def test_startup_heavy_maintenance_lifecycle():
  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  coord.note_startup_maintenance_pending()
  assert not coord.is_startup_heavy_maintenance_idle()
  coord.mark_startup_heavy_maintenance_started()
  assert not coord.is_startup_heavy_maintenance_idle()
  coord.mark_startup_heavy_maintenance_finished()
  assert coord.is_startup_heavy_maintenance_idle()


def test_publish_does_not_clear_startup_heavy_gate():
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  coord.note_startup_maintenance_pending()
  empty = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
  )
  coord.publish(empty, from_janitor=True)
  assert not coord.is_startup_heavy_maintenance_idle()
  coord.mark_startup_heavy_maintenance_finished()
  assert coord.is_startup_heavy_maintenance_idle()


def test_wait_for_startup_maintenance_idle_unblocks_on_finish():
  coord = StartupArchiveScanCoordinator(
      archive_data_dir="/tmp",
      host_name_ext="cluster.test",
      tgz_archive_dir="/tmp/daily",
  )
  coord.note_startup_maintenance_pending()
  coord.mark_startup_heavy_maintenance_started()
  result = {"idle": None}

  def finish():
    time.sleep(0.05)
    coord.mark_startup_heavy_maintenance_finished()

  thread = threading.Thread(target=finish)
  thread.start()
  result["idle"] = coord.wait_for_startup_maintenance_idle(timeout_s=5.0)
  thread.join(timeout=3.0)
  assert result["idle"] is True
