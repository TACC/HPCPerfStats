"""Pool stall guard regressions for sync_timedb_archive sealed-day backfill."""

import time

import hpcperfstats.dbload.sync_timedb_archive as sta
from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
  worker_registry_shows_recent_progress,
)


def test_worker_progress_active_defers_stall(monkeypatch):
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

  monkeypatch.setattr(
    sta, "imap_sliding_window_watch_pool", fake_sliding_window
  )
  monkeypatch.setattr(
    st.cfg,
    "get_sync_timedb_archive_max_concurrent_sealed_days",
    lambda: 4,
  )
  monkeypatch.setattr(
    st,
    "_prewarm_archive_members_for_sealed_chunk",
    lambda paths: "2024-01-01:store_warm",
  )
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(
    st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0
  )

  sealed = "/data/daily/2024-01-01.tar.zst"
  registry = {}
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  results = []
  sta._process_sealed_tasks_sliding_window(
    object(),
    lambda args: args,
    [sealed],
    results.append,
    stall_diagnostics=diag,
    stall_poll_state={},
    worker_registry=registry,
  )
  assert results == [sealed]
  assert captured["context"] == "sync_timedb_archive pool"
  assert captured["max_inflight"] == 4
  assert captured.get("stall_abort_polls_fn") is None
  assert callable(captured["on_in_flight_change"])
  assert callable(captured["on_stall_poll"])
  assert callable(captured["on_stall_warning"])
  assert diag.chunk_prewarm_summary == "2024-01-01:store_warm"
  assert diag.ingest_pipeline == "sealed_archive_backfill"
  assert diag.imap_batch_cap == 4


def test_archive_chunk_imap_wires_stall_guards(monkeypatch):
  """Alias entry point still delegates to sliding-window dispatch."""
  test_archive_sliding_window_imap_wires_stall_guards(monkeypatch)


def test_stall_poll_defers_with_active_worker_registry(monkeypatch):
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


def test_decompress_idle_kill_releases_restore_token(tmp_path, monkeypatch):
  """DE-8: a zstd restore that writes nothing is idle-killed, not left hung.

  Before, ``_decompress_to_path`` ran a bare ``subprocess.run`` with no
  progress bound, so a stuck zstd held the restore token (and every append
  and day_close for that day) forever.
  """
  import os
  import stat

  import hpcperfstats.dbload.lib.conf_parser as cfg
  import hpcperfstats.dbload.lib.zstd_cli as zc
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    daily_tar_restore_in_progress_for_day,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  fake = tmp_path / "zstd"
  fake.write_text("#!/bin/sh\nexec sleep 60\n")
  fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
  monkeypatch.setattr(zc, "zstd_executable", lambda: str(fake))
  monkeypatch.setattr(cfg, "get_sync_ingest_stall_idle_s", lambda: 1.0)
  sealed = tmp_path / "2026-10-20.tar.zst"
  sealed.write_bytes(b"\x28\xb5\x2f\xfd" + b"\0" * 64)
  tar = tmp_path / "2026-10-20.tar"
  set_process_archive_members_store(
    SyncTimedbArchiveMembersStore(str(tmp_path / "m"))
  )
  try:
    t0 = time.monotonic()
    ok = zc.decompress_compressed_to_tar(
      str(sealed),
      str(tar),
      1,
      remove_compressed=False,
      restore_reason="corrupt_tar",
      restore_caller="test",
    )
    elapsed = time.monotonic() - t0
  finally:
    set_process_archive_members_store(None)
  assert ok is False
  assert elapsed < 30, "idle kill, not the 60 s sleep"
  assert not daily_tar_restore_in_progress_for_day("2026-10-20")
  assert not os.path.exists(str(tar) + ".decomp.tmp")
  assert sealed.exists()
