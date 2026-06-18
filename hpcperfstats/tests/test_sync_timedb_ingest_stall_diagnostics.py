import os

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    clear_dispatch_worker_stages,
    seed_dispatch_worker_stages,
)


def test_clear_dispatch_worker_stages_removes_placeholders():
  registry = {}
  path = "/data/host.example/1700000000"
  seed_dispatch_worker_stages(registry, [path])
  key = "dispatch:%s" % os.path.normpath(path)
  assert key in registry
  clear_dispatch_worker_stages(registry, [path])
  assert key not in registry


def test_add_stats_file_to_db_records_worker_entry_before_ingest(monkeypatch):
  recorded = []

  def fake_record(path, stage, *, substage=None, lookup_mode=None):
    recorded.append((path, stage, substage, lookup_mode))

  monkeypatch.setattr(st, "record_worker_stage", fake_record)
  monkeypatch.setattr(
      st,
      "_run_ingest_timed",
      lambda stats_file, stage, fn, **kwargs: fn(),
  )
  monkeypatch.setattr(
      st,
      "_add_stats_file_to_db_impl",
      lambda *_a, **_k: ("/tmp/f", True, True, 0.0),
  )
  st.add_stats_file_to_db(object(), "/tmp/f")
  assert recorded[0] == ("/tmp/f", "ingest", "worker_entry", None)


def test_raise_if_ingest_per_file_deadline_exceeded_raises(monkeypatch):
  import time

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      reset_ingest_task_deadline_monotonic,
      reset_ingest_task_effective_timeout_s,
      set_ingest_task_deadline_monotonic,
      set_ingest_task_effective_timeout_s,
  )

  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  deadline_token = set_ingest_task_deadline_monotonic(time.monotonic() - 1.0)
  effective_token = set_ingest_task_effective_timeout_s(900.0)
  try:
    with pytest.raises(st.IngestPerFileTimeoutError) as excinfo:
      st._raise_if_ingest_per_file_deadline_exceeded("/tmp/f", "db_write_host")
    assert excinfo.value.stage == "db_write_host"
    assert excinfo.value.path == "/tmp/f"
    assert excinfo.value.elapsed_s == 900.0
  finally:
    reset_ingest_task_effective_timeout_s(effective_token)
    reset_ingest_task_deadline_monotonic(deadline_token)


def test_build_ingest_stall_log_suffix_includes_defer_and_pipeline(monkeypatch):
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0,
  )
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0,
  )
  monkeypatch.setattr(
      st.cfg, "get_pipeline_overlap_mode", lambda: "ingest_priority",
  )
  monkeypatch.setattr(
      st, "_ingest_stall_defer_state", lambda _day, _state, **kwargs: (False, "redis_warm"),
  )
  diag = st.IngestStallDiagnostics()
  diag.current_imap_batch_max_timeout_s = 900.0
  diag.dynamic_stall_abort_after_polls = 181
  diag.dynamic_stall_wall_s = 905.0
  diag.ingest_pipeline = "split_parse_write"
  diag.imap_batch_cap = 10
  diag.chunk_batch_size = 200
  diag.current_imap_batch_size = 10
  diag.chunk_prewarm_summary = "2026-05-20:redis_warm"
  suffix = st._build_ingest_stall_log_suffix(
      sample=["/data/host.example/1716163200"],
      day_hint="2026-05-20",
      stall_diagnostics=diag,
      progress_state={},
      alive_workers=16,
      consecutive=60,
      poll_timeout_s=5.0,
  )
  assert "stall_defer=off defer_reason=redis_warm" in suffix
  assert "sync_ingest_per_file_timeout_s=900.0" in suffix
  assert "sync_ingest_per_file_timeout_max_s=14400.0" in suffix
  assert "effective_ingest_timeout_s=-" in suffix
  assert "ingest_pipeline=split_parse_write" in suffix
  assert "pipeline_overlap_mode=ingest_priority" in suffix
  assert "chunk_prewarm=2026-05-20:redis_warm" in suffix
  assert "imap_batch_cap=10" in suffix
  assert "batch_max_ingest_timeout_s=900.0" in suffix
  assert "dynamic_stall_abort_after=181" in suffix
  assert "dynamic_stall_wall_s=905" in suffix


def test_ingest_stall_defer_state_no_day_hint():
  defer_on, reason = st._ingest_stall_defer_state("", {})
  assert defer_on is False
  assert reason == "no_day_hint"


def test_ingest_stall_defer_long_budget_when_effective_exceeds_stall_wall(monkeypatch):
  import time

  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 192)
  registry = {
      "1001": {
          "path": "/data/host.example/1700000000",
          "stage": "parse",
          "substage": "head",
          "timeout_s": "14400.0",
          "t0": time.monotonic(),
      },
  }
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  diag.current_imap_batch_max_timeout_s = 900.0
  defer_on, reason = st._ingest_stall_defer_state(
      "",
      {},
      stall_diagnostics=diag,
      consecutive_timeouts=100,
  )
  assert defer_on is True
  assert reason == "long_ingest_budget"


def test_ingest_stall_defer_long_budget_off_when_effective_matches_batch(monkeypatch):
  import time

  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 192)
  registry = {
      "1001": {
          "path": "/data/host.example/1700000000",
          "stage": "parse",
          "substage": "head",
          "timeout_s": "900.0",
          "t0": time.monotonic(),
      },
  }
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  diag.current_imap_batch_max_timeout_s = 900.0
  defer_on, reason = st._ingest_stall_defer_state(
      "",
      {},
      stall_diagnostics=diag,
      consecutive_timeouts=200,
  )
  assert defer_on is False
  assert reason == "no_day_hint"


def test_build_ingest_stall_log_suffix_includes_worker_registry_counts(monkeypatch):
  import time

  registry = {
      "1001": {
          "path": "/data/host.example/1700000000",
          "stage": "parse",
          "substage": "duplicate_scan_streaming",
          "timeout_s": "5183.0",
          "t0": time.monotonic(),
      },
  }
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0)
  monkeypatch.setattr(st.cfg, "get_pipeline_overlap_mode", lambda: "balanced")
  monkeypatch.setattr(
      st, "_ingest_stall_defer_state", lambda _d, _s, **kwargs: (False, "redis_warm"),
  )
  suffix = st._build_ingest_stall_log_suffix(
      sample=[
          "/data/host.example/1700000000",
          "/data/host.example/1700000001",
      ],
      day_hint="2026-05-20",
      stall_diagnostics=diag,
      progress_state={},
      alive_workers=16,
      consecutive=60,
      poll_timeout_s=5.0,
  )
  assert "worker_registry_n=1" in suffix
  assert "in_flight_n=2" in suffix
  assert "worker_registry_gap=1" in suffix
  assert "effective_ingest_timeout_s=5183.0" in suffix
  assert "duplicate_scan_streaming" in suffix
