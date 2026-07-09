"""Regression tests for sync_timedb worker memory telemetry and cooperative recycle."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hpcperfstats.dbload.lib import sync_timedb_worker_memory as wm
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    _dead_worker_exitcode_is_recycle,
    reset_supervisor_retire_tracking_for_tests,
    retire_pool_worker_pid,
)


@pytest.fixture(autouse=True)
def _reset_worker_memory_state():
  wm.reset_worker_tasks_on_worker_for_tests()
  reset_supervisor_retire_tracking_for_tests()
  yield
  wm.reset_worker_tasks_on_worker_for_tests()
  reset_supervisor_retire_tracking_for_tests()


def test_worker_memory_batch_summary_silent_when_telemetry_off(monkeypatch, capsys):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_worker_memory_telemetry", lambda: False)
  acc = wm.WorkerMemoryBatchAccumulator()
  acc.record_completion(wm.REAP_KEEP, {"tasks_on_worker": 3, "rss_mib_after_release": 100.0})
  acc.maybe_flush(0)
  assert capsys.readouterr().out == ""


def test_worker_memory_batch_summary_logs_when_telemetry_on(monkeypatch, capsys):
  import hpcperfstats.dbload.lib.conf_parser as cfg
  import hpcperfstats.dbload.lib.process_memory as pm

  monkeypatch.setattr(cfg, "get_sync_ingest_worker_memory_telemetry", lambda: True)
  monkeypatch.setattr(
      cfg, "get_sync_ingest_worker_memory_telemetry_every_n_chunks", lambda: 1,
  )
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: True)
  monkeypatch.setattr(pm, "format_tree_rss_breakdown_mb", lambda *_a, **_k: {
      "tree_total_mb": 1000.0,
      "ingest_pool_mb": 800.0,
  })
  monkeypatch.setattr(wm, "compute_rss_recycle_threshold_mib", lambda: 3437.0)
  acc = wm.WorkerMemoryBatchAccumulator()
  acc.record_completion(wm.REAP_KEEP, {"tasks_on_worker": 12, "rss_mib_after_release": 200.0})
  acc.record_completion(wm.REAP_FAILURE, {"tasks_on_worker": 1, "rss_mib_after_release": 500.0})
  acc.record_completion(wm.REAP_RSS, {"tasks_on_worker": 5, "rss_mib_after_release": 4000.0})
  acc.record_completion(
      wm.REAP_GIANT,
      {"tasks_on_worker": 2, "rss_mib_after_release": 3000.0, "rss_recheck_fired": "yes"},
  )
  acc.maybe_flush(7)
  out = capsys.readouterr().out
  assert "sync_timedb worker_memory: event=batch_summary batch=7" in out
  assert "completions=4" in out
  assert "keep_worker=1" in out
  assert "retires_total=3" in out
  assert "retires_failure_reap=1" in out
  assert "retires_rss_reap=1" in out
  assert "retires_giant_reap=1" in out
  assert "failure_reap_pct=" in out
  assert "rss_reap_pct=" in out
  assert "giant_reap_pct=" in out
  assert "rss_recheck_fired=1" in out


def test_worker_memory_telemetry_every_n_chunks_throttles(monkeypatch, capsys):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_worker_memory_telemetry", lambda: True)
  monkeypatch.setattr(
      cfg, "get_sync_ingest_worker_memory_telemetry_every_n_chunks", lambda: 2,
  )
  acc = wm.WorkerMemoryBatchAccumulator()
  acc.record_completion(wm.REAP_KEEP, {})
  acc.maybe_flush(0)
  acc.record_completion(wm.REAP_KEEP, {})
  acc.maybe_flush(1)
  out = capsys.readouterr().out
  assert out.count("event=batch_summary") == 1


def test_classify_supervisor_reap_kind_priority(monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: True)
  monkeypatch.setattr(wm, "compute_rss_recycle_threshold_mib", lambda: 100.0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_ingest_timeout.is_giant_ingest_budget",
      lambda p: "giant" in str(p),
  )
  assert wm.classify_supervisor_reap_kind(
      ingest_ok=False,
      outcome="ingested",
      meta={"rss_mib_after_release": 50.0},
      path="/data/giant",
  ) == wm.REAP_FAILURE
  assert wm.classify_supervisor_reap_kind(
      ingest_ok=True,
      outcome="ingested",
      meta={"rss_mib_after_release": 50.0},
      path="/data/giant",
  ) == wm.REAP_GIANT
  assert wm.classify_supervisor_reap_kind(
      ingest_ok=True,
      outcome="db_skip",
      meta={"rss_mib_after_release": 200.0, "recycle_threshold_mib": 100.0},
      path="/data/small",
  ) == wm.REAP_RSS
  assert wm.classify_supervisor_reap_kind(
      ingest_ok=True,
      outcome="db_skip",
      meta={"rss_mib_after_release": 50.0, "recycle_threshold_mib": 100.0},
      path="/data/small",
  ) == wm.REAP_KEEP
  # RC-J: giant path budget must not force giant_reap on db_skip.
  assert wm.classify_supervisor_reap_kind(
      ingest_ok=True,
      outcome="db_skip",
      meta={"rss_mib_after_release": 50.0, "recycle_threshold_mib": 100.0},
      path="/data/giant",
  ) == wm.REAP_KEEP


def test_no_giant_reap_on_db_skip(monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: True)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_ingest_timeout.is_giant_ingest_budget",
      lambda p: True,
  )
  assert wm.classify_supervisor_reap_kind(
      ingest_ok=True,
      outcome="db_skip",
      meta={},
      path="/data/huge.stats",
  ) == wm.REAP_KEEP
  assert wm.should_supervisor_retire_worker(wm.REAP_KEEP) is False


def test_supervisor_retire_sigterm_counts_as_healthy_recycle(monkeypatch):
  retired = {"terminate": False, "is_alive": True}

  class _Proc:
    pid = 4242
    exitcode = None

    def is_alive(self):
      return retired["is_alive"]

    def terminate(self):
      retired["terminate"] = True
      retired["is_alive"] = False
      self.exitcode = -15

  worker = _Proc()
  pool = SimpleNamespace(_pool=[worker])
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health._reap_pool_worker_pids",
      lambda *_a, **_k: [],
  )
  assert retire_pool_worker_pid(pool, 4242, context="test_retire") is True
  assert retired["terminate"] is True
  assert _dead_worker_exitcode_is_recycle(worker, pool=pool) is True


def test_should_supervisor_retire_respects_maxtasksperchild(monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_ingest_recycle_worker_on_failure", lambda: True)
  assert wm.should_supervisor_retire_worker(wm.REAP_FAILURE) is False
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  assert wm.should_supervisor_retire_worker(wm.REAP_FAILURE) is True


def test_retire_skipped_missing_worker_pid_warn_only(monkeypatch, capsys):
  """Giant ingested meta without pid + maxtasks=0 → WARN only, never raise."""
  import hpcperfstats.dbload.lib.conf_parser as cfg
  import hpcperfstats.dbload.sync_timedb as st

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: True)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_ingest_timeout.is_giant_ingest_budget",
      lambda p: True,
  )
  retire_calls = []
  monkeypatch.setattr(
      st,
      "retire_pool_worker_pid",
      lambda *a, **k: retire_calls.append((a, k)),
  )
  result = (
      "/data/huge.stats",
      False,
      True,
      0.1,
      {"outcome": "ingested", "stats_rows": 1, "giant": "yes"},
  )
  reap_kind = st._handle_ingest_worker_memory_after_imap(
      pool=SimpleNamespace(_pool=[]),
      registry={},
      result=result,
      accumulator=None,
  )
  assert reap_kind == wm.REAP_GIANT
  assert retire_calls == []
  out = capsys.readouterr().out
  assert "retire skipped missing worker_pid" in out
  assert "likely_cause=meta_or_registry_gap" in out
  assert "reap_kind=giant_reap" in out


def test_db_skip_giant_does_not_retire(monkeypatch):
  """RC-J: db_skip + giant budget must not supervisor-retire."""
  import hpcperfstats.dbload.lib.conf_parser as cfg
  import hpcperfstats.dbload.sync_timedb as st

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: True)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_ingest_timeout.is_giant_ingest_budget",
      lambda p: True,
  )
  retire_calls = []
  monkeypatch.setattr(
      st,
      "retire_pool_worker_pid",
      lambda *a, **k: retire_calls.append((a, k)),
  )
  result = (
      "/data/huge.stats",
      False,
      True,
      0.1,
      {"outcome": "db_skip", "db_skip": "head_tail", "worker_pid": 999},
  )
  reap_kind = st._handle_ingest_worker_memory_after_imap(
      pool=SimpleNamespace(_pool=[]),
      registry={},
      result=result,
      accumulator=None,
  )
  assert reap_kind == wm.REAP_KEEP
  assert retire_calls == []


def test_should_defer_supervisor_retire_near_max_inflight(monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  acc = wm.WorkerMemoryBatchAccumulator()
  acc.retires_this_window = 8
  assert wm.should_defer_supervisor_retire(
      wm.REAP_GIANT,
      accumulator=acc,
      pending_inflight=24,
      max_inflight=24,
  ) is True
  assert wm.should_defer_supervisor_retire(
      wm.REAP_GIANT,
      accumulator=acc,
      pending_inflight=10,
      max_inflight=24,
  ) is False


def test_defer_retire_uses_live_inflight(monkeypatch):
  """RC-K: throttle uses live pending_inflight, not fake min(cap, thread_count)."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  acc = wm.WorkerMemoryBatchAccumulator()
  acc.retires_this_window = 8
  # Fake constant near-cap (old bug) would defer; live low inflight must not.
  fake_constant_near_cap = 24
  live_inflight = 3
  assert wm.should_defer_supervisor_retire(
      wm.REAP_RSS,
      accumulator=acc,
      pending_inflight=fake_constant_near_cap,
      max_inflight=24,
  ) is True
  assert wm.should_defer_supervisor_retire(
      wm.REAP_RSS,
      accumulator=acc,
      pending_inflight=live_inflight,
      max_inflight=24,
  ) is False


def test_validate_sync_ingest_pool_recycle_combo_refuses_unsafe(monkeypatch):
  """RC-X: maxtasks=0 + coop_giant must fail validation."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: True)
  msg = cfg.validate_sync_ingest_pool_recycle_combo()
  assert msg is not None
  assert "maxtasksperchild=0" in msg
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 1)
  assert cfg.validate_sync_ingest_pool_recycle_combo() is None
  monkeypatch.setattr(cfg, "get_sync_ingest_pool_maxtasksperchild", lambda: 0)
  monkeypatch.setattr(cfg, "get_sync_ingest_cooperative_recycle_after_giant", lambda: False)
  assert cfg.validate_sync_ingest_pool_recycle_combo() is None
