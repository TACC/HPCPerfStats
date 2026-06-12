import os
import time

from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
    clear_worker_stage,
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
