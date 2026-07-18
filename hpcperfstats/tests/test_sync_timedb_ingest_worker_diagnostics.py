import os

from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_worker_stage,
    count_worker_registry_entries,
    format_worker_stages_snapshot,
    record_worker_stage,
    set_worker_diagnostics_registry,
    update_worker_substage,
)


def test_worker_stage_registry_lifecycle():
  registry = {}
  set_worker_diagnostics_registry(registry)
  try:
    record_worker_stage("/tmp/host.example/12345", "parse")
    pid = str(os.getpid())
    assert registry[pid]["stage"] == "parse"
    update_worker_substage("duplicate_scan_streaming", lookup_mode="hget")
    assert registry[pid]["substage"] == "duplicate_scan_streaming"
    assert registry[pid]["lookup_mode"] == "hget"
    snapshot = format_worker_stages_snapshot(registry)
    assert pid in snapshot
    assert "duplicate_scan_streaming:hget" in snapshot
    clear_worker_stage()
    assert pid not in registry
  finally:
    set_worker_diagnostics_registry(None)


def test_format_worker_stages_snapshot_empty():
  assert format_worker_stages_snapshot(None) == "-"
  assert format_worker_stages_snapshot({}) == "-"


def test_format_worker_stages_prefers_ingest_over_populate():
  registry = {
      "167": {
          "path": "/data/daily/2026-06-02.tar.zst",
          "stage": "populate_queue_wait",
          "t0": 100.0,
      },
      "142": {
          "path": "/data/host.example/1784310055",
          "stage": "ingest",
          "substage": "parse",
          "t0": 100.0,
      },
  }
  snapshot = format_worker_stages_snapshot(
      registry,
      prefer_paths=["/data/host.example/1784310055"],
  )
  assert snapshot.index("1784310055") < snapshot.index("populate_queue_wait")


def test_record_worker_stage_timeout_s_roundtrip():
  registry = {}
  set_worker_diagnostics_registry(registry)
  try:
    record_worker_stage("/tmp/a", "ingest", timeout_s=975.1)
    pid = str(os.getpid())
    assert registry[pid]["timeout_s"] == "975.1"
  finally:
    set_worker_diagnostics_registry(None)


def test_count_worker_registry_entries():
  registry = {}
  set_worker_diagnostics_registry(registry)
  try:
    record_worker_stage("/tmp/a", "parse")
    assert count_worker_registry_entries(registry) == 1
    clear_worker_stage()
    assert count_worker_registry_entries(registry) == 0
  finally:
    set_worker_diagnostics_registry(None)


def test_apply_ingest_pool_worker_init_sets_process_registry():
  registry = {}
  apply_ingest_pool_worker_init("sync_timedb.py", "ingest-pool", registry)
  try:
    from multiprocessing import current_process

    assert getattr(current_process(), "_hpc_worker_diagnostics_registry", None) is registry
    record_worker_stage("/tmp/b", "parse")
    assert count_worker_registry_entries(registry) == 1
  finally:
    set_worker_diagnostics_registry(None)
