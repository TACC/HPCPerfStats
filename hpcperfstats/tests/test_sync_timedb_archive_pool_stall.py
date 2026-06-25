"""Pool stall guard regressions for sync_timedb_archive sealed-day backfill."""

import time


from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout_mod
import hpcperfstats.dbload.sync_timedb_archive as sta
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    worker_registry_shows_recent_progress,
)


def test_worker_progress_active_defers_stall(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  registry = {
      "12345": {
          "path": "/data/host.example/1700000000",
          "stage": "ingest",
          "substage": "parse:dataframes",
          "t0": time.monotonic(),
      },
  }

  defer_on, reason = st._ingest_stall_defer_state(
      "",
      {},
      stall_diagnostics=st.IngestStallDiagnostics(),
      consecutive_timeouts=100,
      pool=None,
      sample=[],
  )
  assert not defer_on

  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  diag.ingest_pipeline = "sealed_archive_backfill"
  defer_on, reason = st._ingest_stall_defer_state(
      "",
      {},
      stall_diagnostics=diag,
      consecutive_timeouts=100,
      pool=None,
      sample=["/data/daily/2024-01-01.tar.zst"],
  )
  assert defer_on
  assert reason == "worker_progress_active"


def test_worker_registry_shows_recent_progress_ignores_dispatch_placeholders():
  registry = {
      "dispatch:/tmp/day.tar.zst": {
          "path": "/tmp/day.tar.zst",
          "stage": "dispatched",
          "t0": time.monotonic() - 9999.0,
      },
  }
  assert not worker_registry_shows_recent_progress(registry)


def test_archive_sliding_window_imap_wires_stall_guards(monkeypatch):
  captured = {}

  def fake_sliding_window(pool, fn, iterable, **kwargs):
    captured.update(kwargs)
    captured["iterable"] = list(iterable)
    yield sealed

  monkeypatch.setattr(sta, "imap_sliding_window_watch_pool", fake_sliding_window)
  monkeypatch.setattr(
      st.cfg,
      "get_sync_timedb_archive_max_concurrent_sealed_days",
      lambda: 4,
  )
  monkeypatch.setattr(
      st,
      "_prewarm_archive_members_redis_for_sealed_chunk",
      lambda paths: "2024-01-01:redis_warm",
  )
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 2881)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0)
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_per_file_timeout_s_per_mib",
      lambda: 13500.0 / 5120.0,
  )

  sealed = "/data/daily/2024-01-01.tar.zst"
  registry = {}
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  results = []
  sta._process_sealed_tasks_sliding_window(
      object(),
      lambda args: args,
      [("lock", sealed)],
      results.append,
      stall_diagnostics=diag,
      stall_poll_state={},
      worker_registry=registry,
  )
  assert results == [sealed]
  assert captured["context"] == "sync_timedb_archive pool"
  assert captured["max_inflight"] == 4
  assert callable(captured["stall_abort_polls_fn"])
  assert callable(captured["on_in_flight_change"])
  assert callable(captured["on_stall_poll"])
  assert callable(captured["on_stall_warning"])
  assert diag.chunk_prewarm_summary == "2024-01-01:redis_warm"
  assert diag.ingest_pipeline == "sealed_archive_backfill"
  assert diag.imap_batch_cap == 4


def test_archive_chunk_imap_wires_stall_guards(monkeypatch):
  """Alias entry point still delegates to sliding-window dispatch."""
  test_archive_sliding_window_imap_wires_stall_guards(monkeypatch)


def test_stall_poll_defers_with_active_worker_registry(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  registry = {
      "99999": {
          "path": "/spool/host/1700000000",
          "stage": "ingest",
          "t0": time.monotonic(),
      },
  }
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  diag.ingest_pipeline = "sealed_archive_backfill"
  poll_fn = st._make_ingest_stall_poll_fn(
      None,
      {},
      stall_diagnostics=diag,
      day_hint_from_sample_fn=st._calendar_day_hint_from_sealed_paths,
  )
  assert poll_fn(
      500,
      "sync_timedb_archive pool",
      {"active_pool": None, "in_flight_sample": ["/tmp/2024-01-01.tar.zst"]},
  )


def test_stall_abort_polls_for_sealed_archives_scales_with_size(monkeypatch, tmp_path):
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 2881)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_per_file_timeout_s_per_mib",
      lambda: 13500.0 / 5120.0,
  )
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0)
  monkeypatch.setattr(
      ingest_timeout_mod,
      "_redis_member_count_for_sealed_day",
      lambda _day: 0,
  )

  small = tmp_path / "2024-01-01.tar.zst"
  small.write_bytes(b"x")
  large = tmp_path / "2024-06-01.tar.zst"
  large.write_bytes(b"x")
  member_count = 200

  def fake_getsize(path):
    path = str(path)
    if path == str(small):
      return 32 * 1024 * 1024
    if path == str(large):
      return 32 * 1024 * 1024 * member_count
    raise OSError(path)

  monkeypatch.setattr(ingest_timeout_mod.os.path, "getsize", fake_getsize)
  small_polls = ingest_timeout_mod.stall_abort_polls_for_sealed_archives([str(small)])
  assert small_polls >= 181
  large_polls = ingest_timeout_mod.stall_abort_polls_for_sealed_archives(
      [str(large)],
      member_counts={str(large): member_count},
  )
  assert large_polls > small_polls
  assert large_polls <= 2881
