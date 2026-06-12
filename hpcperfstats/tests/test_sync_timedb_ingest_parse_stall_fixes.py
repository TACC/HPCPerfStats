import os
import time
from datetime import timezone

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload import sync_timedb_archive_janitor as janitor_mod
from hpcperfstats.dbload import sync_timedb_parsing as parsing
from hpcperfstats.dbload.sync_timedb_archive_janitor import ArchiveJanitor
from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
    IngestArchiveLookupBudgetExceededError,
    set_ingest_task_deadline_monotonic,
    reset_ingest_task_deadline_monotonic,
)


def _write_stats_file(path, line_count, *, host="node1", jid="1"):
  os.makedirs(os.path.dirname(path), exist_ok=True)
  base_ts = 1700000000
  with open(path, "w") as fd:
    for idx in range(line_count):
      fd.write("%d %s %s\n" % (base_ts + idx, jid, host))
      fd.write("schema line\n")


def test_db_complete_head_tail_skips_full_duplicate_scan(tmp_path, monkeypatch):
  stats_file = str(tmp_path / "host.example" / "1700000000")
  _write_stats_file(stats_file, 5000)
  duplicate_calls = {"n": 0}

  class _FakeSyncWorkerDbTask:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

  def fake_duplicate(*_a, **_k):
    duplicate_calls["n"] += 1
    return 0, True

  monkeypatch.setattr(st, "_sync_worker_db_task", lambda: _FakeSyncWorkerDbTask())
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda _h, _t: True)
  monkeypatch.setattr(
      st, "_timestamp_second_present_for_duplicate", lambda _h, _s, _t: True,
  )
  monkeypatch.setattr(st, "_duplicate_window_start_index", fake_duplicate)
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 * 1024 * 1024)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 0)

  result = st._parse_stats_file_payload_impl(stats_file)
  assert duplicate_calls["n"] == 0
  assert result[2] is False
  assert result[3] is True


def test_db_complete_tail_missing_falls_back_to_full_scan(tmp_path, monkeypatch):
  stats_file = str(tmp_path / "host.example" / "1700000100")
  _write_stats_file(stats_file, 200)
  duplicate_calls = {"n": 0}

  class _FakeSyncWorkerDbTask:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

  def fake_duplicate(*_a, **_k):
    duplicate_calls["n"] += 1
    return -1, True

  monkeypatch.setattr(st, "_sync_worker_db_task", lambda: _FakeSyncWorkerDbTask())
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda _h, _t: True)
  monkeypatch.setattr(
      st, "_timestamp_second_present_for_duplicate",
      lambda _h, unix_second, _t: unix_second == 1700000100,
  )
  monkeypatch.setattr(st, "_duplicate_window_start_index", fake_duplicate)
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 * 1024 * 1024)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 0)

  st._parse_stats_file_payload_impl(stats_file)
  assert duplicate_calls["n"] == 1


def test_stream_duplicate_scan_threshold_routes_large_file(tmp_path, monkeypatch):
  stats_file = str(tmp_path / "host.example" / "1700000200")
  _write_stats_file(stats_file, 10)
  os.truncate(stats_file, 10 * 1024 * 1024)

  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 * 1024 * 1024)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 4 * 1024 * 1024)
  routed = {"streaming": False}

  def fake_streaming(path):
    routed["streaming"] = True
    return (path, None, False, True, 0.0)

  monkeypatch.setattr(st, "_parse_stats_file_payload_impl_streaming", fake_streaming)
  st._parse_stats_file_payload_impl(stats_file)
  assert routed["streaming"] is True


def test_readlines_checks_monotonic_deadline(tmp_path):
  stats_file = str(tmp_path / "host.example" / "1700000300")
  _write_stats_file(stats_file, 2500)
  token = set_ingest_task_deadline_monotonic(time.monotonic() - 1.0)
  try:
    with pytest.raises(IngestArchiveLookupBudgetExceededError):
      parsing.load_stats_file_lines(stats_file)
  finally:
    reset_ingest_task_deadline_monotonic(token)


def test_parse_last_timestamp_line_streaming_reads_tail_only(tmp_path):
  stats_file = str(tmp_path / "host.example" / "1700000400")
  _write_stats_file(stats_file, 50)
  t, jid, host = parsing.parse_last_timestamp_line_streaming(stats_file)
  assert int(float(t)) == 1700000000 + 49
  assert jid == "1"
  assert host == "node1"


def test_completed_ingest_calendar_days_detects_drained_day():
  chunk = ["/data/h/1577836800"]
  pending_before = ["/data/h/1577836800", "/data/h/1577923200"]
  pending_after = ["/data/h/1577923200"]
  days = st._completed_ingest_calendar_days(
      chunk_paths=chunk,
      pending_before=pending_before,
      pending_after=pending_after,
  )
  assert days == ["2020-01-01"]


def test_completed_ingest_calendar_days_skips_same_day_pending():
  chunk = ["/data/h/1577836800"]
  pending_before = ["/data/h/1577836800", "/data/h/1577840400"]
  pending_after = ["/data/h/1577840400"]
  days = st._completed_ingest_calendar_days(
      chunk_paths=chunk,
      pending_before=pending_before,
      pending_after=pending_after,
  )
  assert days == []


def _make_janitor(tmp_path):
  from unittest.mock import MagicMock

  return ArchiveJanitor(
      archive_data_dir=str(tmp_path),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=timezone.utc,
      log_fn=MagicMock(),
      get_disqualified_daily_tars=lambda: set(),
      get_ingest_backlog_high=lambda: False,
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
      ingest_ready_fn=None,
      archive_stats_files_fn=None,
      day_raw_removal_coordinator=None,
  )


def test_janitor_light_pass_on_every_n_chunks_no_heavy(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tmp_path)
  build_calls = {"n": 0}

  def counting_build(*_a, **_k):
    build_calls["n"] += 1
    raise AssertionError("heavy collect should not run")

  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", counting_build)
  monkeypatch.setattr(janitor, "_log_day_close_candidate_report", lambda **_k: None)
  monkeypatch.setattr(janitor, "_submit_scheduled_day_close_from_snapshot", lambda **_k: set())
  janitor.run_light_maintenance_pass(reason="every_n_chunks")
  assert build_calls["n"] == 0


def test_heavy_snapshot_startup_adopts_coordinator(monkeypatch, tmp_path):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
  from hpcperfstats.dbload.sync_timedb_startup_archive_scan import (
      StartupArchiveScanCoordinator,
  )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tmp_path)
  coord = StartupArchiveScanCoordinator(
      archive_data_dir=str(tmp_path),
      host_name_ext="example",
      tgz_archive_dir=str(daily_dir),
  )
  existing = ArchiveMaintenanceSnapshot(
      closed_paths=["/tmp/raw-a"],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
      first_timestamp_by_path={},
      head_identity_by_path={},
  )
  coord.publish(existing, from_janitor=True)
  janitor.startup_snapshot_coordinator = coord
  build_calls = {"n": 0}

  def counting_build(*_a, **_k):
    build_calls["n"] += 1
    return existing

  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", counting_build)
  monkeypatch.setattr(janitor, "_log_day_close_candidate_report", lambda **_k: None)
  monkeypatch.setattr(janitor, "_submit_scheduled_day_close_from_snapshot", lambda **_k: set())
  monkeypatch.setattr(janitor, "_trim_accrual_snapshot_memory", lambda: None)
  janitor.run_heavy_maintenance_pass(reason="startup")
  assert build_calls["n"] == 0
  with janitor._accrual_snapshot_lock:
    assert janitor._accrual_snapshot.closed_paths == ["/tmp/raw-a"]


def test_heavy_snapshot_on_day_ingest_complete_builds(monkeypatch, tmp_path):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tmp_path)
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=["/tmp/raw-b"],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
      first_timestamp_by_path={},
      head_identity_by_path={},
  )
  build_calls = {"n": 0}

  def counting_build(*_a, **_k):
    build_calls["n"] += 1
    return snapshot

  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", counting_build)
  monkeypatch.setattr(janitor, "_log_day_close_candidate_report", lambda **_k: None)
  monkeypatch.setattr(janitor, "_submit_scheduled_day_close_from_snapshot", lambda **_k: set())
  monkeypatch.setattr(janitor, "_trim_accrual_snapshot_memory", lambda: None)
  janitor.run_heavy_maintenance_pass(reason="day_ingest_complete:2020-01-01")
  assert build_calls["n"] == 1


def test_is_heavy_maintenance_reason():
  assert janitor_mod._is_heavy_maintenance_reason("startup") is True
  assert janitor_mod._is_heavy_maintenance_reason("day_ingest_complete:2020-01-01") is True
  assert janitor_mod._is_heavy_maintenance_reason("every_n_chunks") is False


def test_heavy_maintenance_deferred_when_ingest_in_flight(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tmp_path)
  janitor.get_ingest_pool_in_flight_count = lambda: 4
  build_calls = {"n": 0}
  signal_calls = []

  def counting_build(*_a, **_k):
    build_calls["n"] += 1
    raise AssertionError("heavy collect should not run")

  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", counting_build)
  monkeypatch.setattr(
      janitor,
      "signal_scheduled_maintenance_pass",
      lambda *, reason: signal_calls.append(reason),
  )
  janitor.run_heavy_maintenance_pass(reason="day_ingest_complete:2020-01-01")
  assert build_calls["n"] == 0
  assert signal_calls == ["day_ingest_complete:2020-01-01"]


def test_combined_ingest_uses_single_parse_timer(monkeypatch, tmp_path):
  stats_file = str(tmp_path / "host.example" / "1700000300")
  _write_stats_file(stats_file, 5)
  alarm_calls = {"n": 0}
  original_setitimer = None
  if hasattr(__import__("signal"), "setitimer"):
    import signal as signal_mod

    original_setitimer = signal_mod.setitimer

    def counting_setitimer(which, seconds, interval=0.0):
      if which == signal_mod.ITIMER_REAL and seconds > 0:
        alarm_calls["n"] += 1
      return original_setitimer(which, seconds, interval)

    monkeypatch.setattr(signal_mod, "setitimer", counting_setitimer)

  class _FakeSyncWorkerDbTask:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

  class _FakeLock:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

  monkeypatch.setattr(st, "_sync_worker_db_task", lambda: _FakeSyncWorkerDbTask())
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda _h, _t: True)
  monkeypatch.setattr(
      st, "_timestamp_second_present_for_duplicate", lambda _h, _s, _t: True,
  )
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 * 1024 * 1024)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 0)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(st, "_write_stats_payload_to_db", lambda *_a, **_k: (_a[1], False, True))

  st.add_stats_file_to_db(_FakeLock(), stats_file)
  if original_setitimer is not None:
    assert alarm_calls["n"] == 1


def test_tail_window_skips_full_duplicate_scan(tmp_path, monkeypatch):
  stats_file = str(tmp_path / "host.example" / "1700000400")
  _write_stats_file(stats_file, 4000)
  duplicate_calls = {"n": 0}

  class _FakeSyncWorkerDbTask:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

  def fake_duplicate(*_a, **_k):
    duplicate_calls["n"] += 1
    return 0, True

  monkeypatch.setattr(st, "_sync_worker_db_task", lambda: _FakeSyncWorkerDbTask())
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda _h, _t: True)
  monkeypatch.setattr(
      st, "_try_db_complete_head_tail_fast_path", lambda *_a, **_k: None,
  )
  monkeypatch.setattr(st, "_host_recent_timestamps_cached", lambda *_a, **_k: set())
  monkeypatch.setattr(
      st,
      "tail_window_timestamps_all_present_streaming",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(st, "_duplicate_window_start_index", fake_duplicate)
  monkeypatch.setattr(st, "raw_stats_path_needs_tar_append", lambda *_a, **_k: False)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_max_file_read_bytes", lambda: 512 * 1024 * 1024)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_stream_duplicate_scan_bytes", lambda: 100_000)

  result = st._parse_stats_file_payload_impl_streaming(stats_file)
  assert duplicate_calls["n"] == 0
  assert result[3] is True


def test_seed_dispatch_worker_stages_increases_registry_count():
  from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
      count_worker_registry_entries,
      seed_dispatch_worker_stages,
  )

  registry = {}
  seed_dispatch_worker_stages(registry, ["/a/1", "/b/2"])
  assert count_worker_registry_entries(registry) == 2
