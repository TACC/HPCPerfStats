from hpcperfstats.dbload import sync_timedb as st


def test_build_ingest_stall_log_suffix_includes_defer_and_pipeline(monkeypatch):
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0,
  )
  monkeypatch.setattr(
      st.cfg, "get_pipeline_overlap_mode", lambda: "ingest_priority",
  )
  monkeypatch.setattr(
      st, "_ingest_stall_defer_state", lambda _day, _state: (False, "redis_warm"),
  )
  diag = st.IngestStallDiagnostics()
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
  assert "ingest_pipeline=split_parse_write" in suffix
  assert "pipeline_overlap_mode=ingest_priority" in suffix
  assert "chunk_prewarm=2026-05-20:redis_warm" in suffix
  assert "imap_batch_cap=10" in suffix


def test_ingest_stall_defer_state_no_day_hint():
  defer_on, reason = st._ingest_stall_defer_state("", {})
  assert defer_on is False
  assert reason == "no_day_hint"


def test_build_ingest_stall_log_suffix_includes_worker_registry_counts(monkeypatch):
  import time

  registry = {
      "1001": {
          "path": "/data/host.example/1700000000",
          "stage": "parse",
          "substage": "duplicate_scan_streaming",
          "t0": time.monotonic(),
      },
  }
  diag = st.IngestStallDiagnostics()
  diag.worker_registry = registry
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(st.cfg, "get_pipeline_overlap_mode", lambda: "balanced")
  monkeypatch.setattr(st, "_ingest_stall_defer_state", lambda _d, _s: (False, "redis_warm"))
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
  assert "duplicate_scan_streaming" in suffix
