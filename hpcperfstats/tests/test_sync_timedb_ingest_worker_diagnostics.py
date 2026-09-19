import threading

from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_worker_stage,
    count_worker_registry_entries,
    format_worker_stages_snapshot,
    record_worker_stage,
    set_worker_diagnostics_registry,
    update_worker_substage,
    worker_registry_key,
)


def test_worker_stage_registry_lifecycle():
  registry = {}
  set_worker_diagnostics_registry(registry)
  try:
    record_worker_stage("/tmp/host.example/12345", "parse")
    key = worker_registry_key()
    assert registry[key]["stage"] == "parse"
    update_worker_substage("duplicate_scan_streaming", lookup_mode="hget")
    assert registry[key]["substage"] == "duplicate_scan_streaming"
    assert registry[key]["lookup_mode"] == "hget"
    snapshot = format_worker_stages_snapshot(registry)
    assert key in snapshot
    assert "duplicate_scan_streaming:hget" in snapshot
    clear_worker_stage()
    assert key not in registry
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
    key = worker_registry_key()
    assert registry[key]["timeout_s"] == "975.1"
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


def test_record_worker_stage_distinct_keys_per_thread():
  """FT thread-pool peers must not overwrite each other's stage rows."""
  registry = {}
  set_worker_diagnostics_registry(registry)
  barrier = threading.Barrier(2)
  keys = []

  def worker(label: str) -> None:
    record_worker_stage("/tmp/%s" % label, "ingest", substage=label)
    keys.append(worker_registry_key())
    barrier.wait(timeout=5)
    barrier.wait(timeout=5)

  try:
    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert len(set(keys)) == 2
    assert count_worker_registry_entries(registry) == 2
    stages = {entry["substage"] for entry in registry.values()}
    assert stages == {"a", "b"}
  finally:
    set_worker_diagnostics_registry(None)


def test_registry_key_matches_alive_spawn_pid_prefix():
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      registry_key_matches_alive_pids,
      worker_registry_shows_member_match_wait,
  )

  assert registry_key_matches_alive_pids("42:99", {"42"}) is True
  registry = {
      "42:99": {
          "path": "/tmp/x",
          "stage": "ingest",
          "substage": "archive_member_lookup",
          "lookup_mode": "store_wait",
          "t0": __import__("time").monotonic(),
      },
  }
  assert worker_registry_shows_member_match_wait(
      registry, alive_pids={"42"},
  ) is True
  assert worker_registry_shows_member_match_wait(
      registry, alive_pids={"99"},
  ) is False
