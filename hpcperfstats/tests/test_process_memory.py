"""Tests for process_memory tree RSS helpers."""

from types import SimpleNamespace

import hpcperfstats.process_memory as pm


class _FakeProc:
  def __init__(self, pid, alive=True, rss_kb=1024):
    self.pid = pid
    self._alive = alive
    self._rss_kb = rss_kb

  def is_alive(self):
    return self._alive


def test_sum_pool_worker_rss_bytes_skips_dead_workers(monkeypatch):
  pool = SimpleNamespace(_pool=[
      _FakeProc(100, alive=True, rss_kb=2048),
      _FakeProc(101, alive=False, rss_kb=4096),
  ])
  monkeypatch.setattr(pm, "read_process_rss_bytes", lambda pid: 2048 * 1024 if pid == 100 else 0)
  assert pm.sum_pool_worker_rss_bytes(pool) == 2048 * 1024


def test_read_sync_timedb_tree_rss_bytes_sums_components(monkeypatch):
  monkeypatch.setattr(pm, "read_process_rss_bytes", lambda pid=None: 100 * 1024 * 1024)
  monkeypatch.setattr(pm, "sum_pool_worker_rss_bytes", lambda pool: 50 * 1024 * 1024 if pool else 0)
  ingest = object()
  db_writer = object()
  archive = object()
  total = pm.read_sync_timedb_tree_rss_bytes(ingest, db_writer, archive)
  assert total == 250 * 1024 * 1024


def test_read_cgroup_memory_current_bytes_parses_file(monkeypatch):
  monkeypatch.setattr(
      pm,
      "_read_cgroup_memory_file",
      lambda name: 4096 if name == "memory.current" else None,
  )
  assert pm.read_cgroup_memory_current_bytes() == 4096


def test_read_cgroup_memory_events_from_raw(monkeypatch):
  monkeypatch.setattr(
      pm,
      "_read_cgroup_memory_events_raw",
      lambda: "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n",
  )
  assert pm.read_cgroup_memory_events() == {
      "low": 0,
      "high": 0,
      "max": 0,
      "oom": 0,
      "oom_kill": 0,
      "oom_group_kill": 0,
  }


def test_format_tree_rss_breakdown_mb(monkeypatch):
  monkeypatch.setattr(pm, "read_process_rss_bytes", lambda pid=None: 10 * 1024 * 1024)
  monkeypatch.setattr(pm, "sum_pool_worker_rss_bytes", lambda pool: 20 * 1024 * 1024)
  breakdown = pm.format_tree_rss_breakdown_mb(object(), object(), object())
  assert breakdown["supervisor_mb"] == 10.0
  assert breakdown["ingest_pool_mb"] == 20.0
  assert breakdown["tree_total_mb"] == 70.0
