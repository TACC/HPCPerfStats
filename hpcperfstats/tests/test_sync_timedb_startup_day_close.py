"""Unit tests for startup checkpoint-driven async DAY_CLOSE preflight."""

import json
import os
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_startup_day_close import (
    PHASE_DONE,
    StartupDayClosePreflight,
    manifest_path,
)


def _make_closed_segment(tmp_path, arch_suffix, day):
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir(parents=True, exist_ok=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  return seg


def _make_preflight(tmp_path, async_coord, **kwargs):
  import zoneinfo

  defaults = {
      "archive_data_dir": str(tmp_path),
      "host_name_ext": "cluster.integration.test",
      "tgz_archive_dir": str(tmp_path / "daily"),
      "local_tz": zoneinfo.ZoneInfo("UTC"),
      "log_fn": MagicMock(),
      "async_day_close": async_coord,
      "get_disqualification_inputs": lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
      },
      "get_unmapped_closed_raw_tars": lambda: set(),
      "day_phases": lambda: {},
  }
  defaults.update(kwargs)
  preflight = StartupDayClosePreflight(**defaults)
  preflight.enabled = True
  return preflight


def test_startup_day_close_enqueues_async_for_checkpoint_complete_day(
    tmp_path,
    monkeypatch,
):
  day = date(2022, 7, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2022-07-01.tar"))
  open(tar_path, "wb").close()
  checkpoint_path = tmp_path / ".sync_timedb_state.json"
  checkpoint_path.write_text(
      json.dumps([{
          "path": str(seg),
          "size": seg.stat().st_size,
          "mtime": int(seg.stat().st_mtime),
      }])
  )
  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append((os.path.normpath(tar_path), reason))
      return True

    def active_or_submitted_tar_paths(self):
      return {t for t, _ in submitted}

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._discover_loop()
  assert submitted
  assert submitted[0][0] == tar_path
  assert submitted[0][1] == "startup_checkpoint_complete"
  assert preflight.phase() == PHASE_DONE
  assert os.path.isfile(manifest_path(str(tmp_path)))


def test_startup_day_close_budget_applies_after_scans_not_before(
    tmp_path, monkeypatch,
):
  day = date(2022, 7, 2)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2022-07-02.tar"))
  open(tar_path, "wb").close()
  checkpoint_path = tmp_path / ".sync_timedb_state.json"
  checkpoint_path.write_text(
      json.dumps([{
          "path": str(seg),
          "size": seg.stat().st_size,
          "mtime": int(seg.stat().st_mtime),
      }])
  )
  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def active_or_submitted_tar_paths(self):
      return set(submitted)

  scan_started = {"t": None}

  def slow_unprocessed(*_a, **_k):
    if scan_started["t"] is None:
      scan_started["t"] = __import__("time").time()
      __import__("time").sleep(0.05)
    return {}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      slow_unprocessed,
  )
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_scan_budget_seconds", lambda: 0.0)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._discover_loop()
  assert submitted == [tar_path]


def test_startup_discover_backoff_skips_scan_when_async_saturated(
    tmp_path, monkeypatch,
):
  scan_calls = {"n": 0}

  def counting_unprocessed(*_a, **_k):
    scan_calls["n"] += 1
    return {}

  class _SaturatedCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, *_a, **_k):
      raise AssertionError("submit should not run when saturated")

    def active_or_submitted_tar_paths(self):
      return {"/arch/2026-04-15.tar"}

    def entry_progress_snapshot(self, _tar):
      return {}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      counting_unprocessed,
  )
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_max_inflight", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _SaturatedCoord())
  with preflight._lock:
    preflight._manifest["pending_eligible"] = ["/arch/2026-04-22.tar"]
    preflight._manifest["pending_retry"] = []
  has_more = preflight._discover_slice()
  assert has_more is False
  assert scan_calls["n"] == 1
  assert preflight._manifest.get("last_progress") == "discover_backoff_async_saturated"


def test_startup_discover_submits_up_to_startup_max_inflight_per_slice(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tars = []
  for day_num in range(7, 13):
    tar = os.path.normpath(str(daily_dir / ("2022-07-%02d.tar" % day_num)))
    open(tar, "wb").close()
    tars.append(tar)

  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def active_or_submitted_tar_paths(self):
      return set(submitted)

    def entry_progress_snapshot(self, _tar):
      return {}

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 4)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 300.0)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_remaining_raw_stats_by_daily_gz",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_ingest_complete_by_checkpoint",
      lambda *_a, **_k: list(tars),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_quiescent_tar_needs_day_close_at_startup",
      lambda *_a, **_k: [],
  )

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._discover_slice()
  assert len(submitted) == 4
  with preflight._lock:
    pending = list(preflight._manifest.get("pending_eligible") or [])
  assert len(pending) == 2


def test_startup_day_close_uses_accrual_snapshot_when_present(
    tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  collect_calls = {"n": 0}

  def boom_collect(*_a, **_k):
    collect_calls["n"] += 1
    return []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, maintenance_snapshot=None, **_k: (
          collect_calls.update({"used_snapshot": maintenance_snapshot is not None}) or {}
      ),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_remaining_raw_stats_by_daily_gz",
      lambda *_a, maintenance_snapshot=None, **_k: {},
  )
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  snapshot = ArchiveMaintenanceSnapshot(closed_paths=[], mapping={})
  preflight = _make_preflight(
      tmp_path,
      MagicMock(
          get_disqualified_daily_tars=lambda: set(),
          submit_day_close=lambda *_a, **_k: False,
          active_or_submitted_tar_paths=lambda: set(),
      ),
      get_startup_snapshot=lambda: snapshot,
  )
  preflight._discover_slice()
  assert collect_calls.get("used_snapshot") is True
  assert collect_calls["n"] == 0


def test_startup_day_close_shutdown_preserves_pending_eligible(
    tmp_path, monkeypatch,
):
  day_a = date(2022, 7, 10)
  day_b = date(2022, 7, 11)
  for d in (day_a, day_b):
    seg = _make_closed_segment(tmp_path, "cluster.integration.test", d)
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir(exist_ok=True)
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % d.isoformat())))
    open(tar_path, "wb").close()
    checkpoint_path = tmp_path / ".sync_timedb_state.json"
    checkpoint_path.write_text(
        json.dumps([{
            "path": str(seg),
            "size": seg.stat().st_size,
            "mtime": int(seg.stat().st_mtime),
        }])
    )

  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def active_or_submitted_tar_paths(self):
      return set(submitted)

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._manifest["pending_eligible"] = [
      os.path.normpath(str(tmp_path / "daily" / "2022-07-11.tar")),
  ]
  preflight._manifest["pending_retry"] = [
      os.path.normpath(str(tmp_path / "daily" / "2022-07-10.tar")),
  ]

  import hpcperfstats.dbload.lib.shutdown_utils as shutdown_utils

  original = shutdown_utils.shutdown_requested[0]
  try:
    shutdown_utils.shutdown_requested[0] = True
    preflight._discover_loop()
  finally:
    shutdown_utils.shutdown_requested[0] = original

  assert preflight.phase() != PHASE_DONE
  with open(manifest_path(str(tmp_path)), encoding="utf-8") as handle:
    saved = json.load(handle)
  assert saved.get("pending_eligible")
  assert saved.get("pending_retry")


def test_startup_day_close_multi_slice_submits_until_cap(
    tmp_path, monkeypatch,
):
  days = [date(2022, 7, 20), date(2022, 7, 21), date(2022, 7, 22)]
  tar_paths = []
  checkpoint_entries = []
  for d in days:
    seg = _make_closed_segment(tmp_path, "cluster.integration.test", d)
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir(exist_ok=True)
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % d.isoformat())))
    open(tar_path, "wb").close()
    tar_paths.append(tar_path)
    checkpoint_entries.append({
        "path": str(seg),
        "size": seg.stat().st_size,
        "mtime": int(seg.stat().st_mtime),
    })
  (tmp_path / ".sync_timedb_state.json").write_text(json.dumps(checkpoint_entries))

  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def active_or_submitted_tar_paths(self):
      return set(submitted)

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 120.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(cfg, "get_sync_day_close_max_inflight", lambda: 10)

  original_eligible = (
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_ingest_complete_by_checkpoint"
  )

  def shrinking_eligible(*_a, **_k):
    return [t for t in tar_paths if t not in submitted]

  monkeypatch.setattr(original_eligible, shrinking_eligible)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  assert preflight._discover_slice() is False
  assert len(submitted) == 1
  assert preflight._discover_slice() is False
  assert len(submitted) == 2
  assert not preflight._discover_slice()
  assert len(submitted) == 3
  preflight._discover_loop()
  assert preflight.phase() == PHASE_DONE


def test_startup_day_close_retries_after_disqualification_clears(
    tmp_path, monkeypatch,
):
  day = date(2022, 7, 30)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2022-07-30.tar"))
  open(tar_path, "wb").close()
  (tmp_path / ".sync_timedb_state.json").write_text(
      json.dumps([{
          "path": str(seg),
          "size": seg.stat().st_size,
          "mtime": int(seg.stat().st_mtime),
      }])
  )
  submitted = []
  disq = {tar_path}

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set(disq)

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def active_or_submitted_tar_paths(self):
      return set(submitted)

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  assert preflight._discover_slice() is True
  assert not submitted
  with open(manifest_path(str(tmp_path)), encoding="utf-8") as handle:
    saved = json.load(handle)
  assert tar_path in (saved.get("pending_retry") or [])

  disq.clear()
  assert preflight._discover_slice() is False
  assert submitted == [tar_path]


def test_startup_day_close_disqualify_once_per_slice(tmp_path, monkeypatch):
  """get_disqualified_daily_tars is amortized to one call per discover slice."""
  days = [date(2022, 8, d) for d in range(1, 13)]
  tar_paths = []
  checkpoint_entries = []
  for d in days:
    seg = _make_closed_segment(tmp_path, "cluster.integration.test", d)
    daily_dir = tmp_path / "daily"
    daily_dir.mkdir(exist_ok=True)
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % d.isoformat())))
    open(tar_path, "wb").close()
    tar_paths.append(tar_path)
    checkpoint_entries.append({
        "path": str(seg),
        "size": seg.stat().st_size,
        "mtime": int(seg.stat().st_mtime),
    })
  (tmp_path / ".sync_timedb_state.json").write_text(json.dumps(checkpoint_entries))

  disq_calls = {"n": 0}

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      disq_calls["n"] += 1
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      return True

    def active_or_submitted_tar_paths(self):
      return set()

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 120.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 12)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  log_fn = preflight.log_fn
  preflight._discover_slice()
  assert disq_calls["n"] == 1
  slice_logs = [
      str(call.args[0])
      for call in log_fn.call_args_list
      if call.args and "discover_slice:" in str(call.args[0])
  ]
  assert any("disqualified_n=0" in line for line in slice_logs)
  assert any("discover slice: submitted=" in str(call.args[0]) for call in log_fn.call_args_list if call.args)


def test_startup_day_close_submits_quiescent_tar_with_stale_unprocessed(
    tmp_path,
    monkeypatch,
):
  day = date(2022, 8, 1)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / (day.isoformat() + ".tar")))
  open(tar_path, "wb").close()
  stale_path = str(tmp_path / "stale-missing.raw")
  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append((os.path.normpath(tar_path), reason))
      return True

    def active_or_submitted_tar_paths(self):
      return {t for t, _ in submitted}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *a, **k: {tar_path: [stale_path]},
  )
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._discover_loop()
  assert submitted
  assert submitted[0][0] == tar_path
  assert submitted[0][1] == "startup_checkpoint_complete"


def test_startup_day_close_skips_quiescent_when_unprocessed_still_on_disk(
    tmp_path,
    monkeypatch,
):
  day = date(2022, 8, 2)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / (day.isoformat() + ".tar")))
  open(tar_path, "wb").close()
  on_disk_path = tmp_path / "still-there.raw"
  on_disk_path.write_text("raw")
  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append((os.path.normpath(tar_path), reason))
      return True

    def active_or_submitted_tar_paths(self):
      return {t for t, _ in submitted}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *a, **k: {tar_path: [str(on_disk_path)]},
  )
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._discover_loop()
  assert not submitted


def test_discover_done_true_while_pending_eligible_only(tmp_path):
  preflight = _make_preflight(
      tmp_path,
      MagicMock(
          get_disqualified_daily_tars=lambda: set(),
          submit_day_close=lambda *_a, **_k: True,
          active_or_submitted_tar_paths=lambda: set(),
          is_complete=lambda _tar: False,
      ),
  )
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2022-09-01.tar"))
  with preflight._lock:
    preflight._manifest["phase"] = PHASE_DONE
    preflight._manifest["pending_eligible"] = [tar_path]
    preflight._manifest["pending_retry"] = []
  assert preflight.discover_done() is True


def test_discover_done_false_while_pending_retry(tmp_path):
  preflight = _make_preflight(
      tmp_path,
      MagicMock(
          get_disqualified_daily_tars=lambda: set(),
          submit_day_close=lambda *_a, **_k: True,
          active_or_submitted_tar_paths=lambda: set(),
          is_complete=lambda _tar: False,
      ),
  )
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2022-09-01.tar"))
  with preflight._lock:
    preflight._manifest["phase"] = PHASE_DONE
    preflight._manifest["pending_eligible"] = []
    preflight._manifest["pending_retry"] = [tar_path]
  assert preflight.discover_done() is False


def test_boot_reconcile_resumes_when_phase_done_and_eligible_remain(
    tmp_path,
    monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_startup_day_close import (
      _save_manifest,
  )

  day = date(2026, 4, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-04-22.tar"))
  open(tar_path, "wb").close()
  (tmp_path / ".sync_timedb_state.json").write_text(
      json.dumps([{
          "path": str(seg),
          "size": seg.stat().st_size,
          "mtime": int(seg.stat().st_mtime),
      }])
  )
  submitted = []

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append((os.path.normpath(tar_path), reason))
      return True

    def active_or_submitted_tar_paths(self):
      return {t for t, _ in submitted}

    def is_complete(self, _tar_path):
      return False

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._manifest["phase"] = PHASE_DONE
  preflight._manifest["completed_at"] = 1.0
  _save_manifest(preflight._manifest_path, preflight._manifest)
  assert preflight._needs_boot_reconcile() is True
  preflight.start_async_discover_and_close()
  preflight._discover_future.result(timeout=30)
  assert submitted
  assert submitted[0][0] == tar_path


def test_boot_reconcile_when_phase_done_and_async_still_incomplete(
    tmp_path,
    monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-04-22.tar"))
  open(tar_path, "wb").close()

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, *_a, **_k):
      return True

    def active_or_submitted_tar_paths(self):
      return {tar_path}

    def is_complete(self, _tar_path):
      return False

  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._manifest["phase"] = PHASE_DONE
  preflight._manifest["completed_at"] = 1.0
  assert preflight._needs_boot_reconcile() is True


def test_discover_slice_enqueues_tail_eligible_before_done(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2022-08-01.tar"))
  open(tar_path, "wb").close()
  raw_path = tmp_path / "raw_seg"
  raw_path.write_bytes(b"x")
  (tmp_path / ".sync_timedb_state.json").write_text(
      json.dumps([{
          "path": str(raw_path),
          "size": raw_path.stat().st_size,
          "mtime": int(raw_path.stat().st_mtime),
      }])
  )
  enqueued = []

  class _FakeTailCoord:
    enabled = True

    def enqueue_tail_day(self, tar_norm, paths):
      enqueued.append((os.path.normpath(tar_norm), list(paths)))
      return True

    def note_deferred_above_max(self, tar_norm, path_count, max_files):
      del tar_norm, path_count, max_files

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      del tar_path, reason, disqualified_daily_tars
      return False

    def active_or_submitted_tar_paths(self):
      return set()

  monkeypatch.setattr(cfg, "get_sync_startup_tail_ingest_max_files", lambda: 100)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_ingest_complete_by_checkpoint",
      lambda *_a, **_k: [],
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_quiescent_tar_needs_day_close_at_startup",
      lambda *_a, **_k: [],
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, **_k: {tar_path: [str(raw_path)]},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_remaining_raw_stats_by_daily_gz",
      lambda *_a, **_k: {},
  )

  preflight = _make_preflight(
      tmp_path,
      _FakeCoord(),
      tail_ingest_coordinator=_FakeTailCoord(),
  )
  assert preflight.discover_done() is False
  preflight._discover_slice()
  assert enqueued == [(tar_path, [str(raw_path)])]
  assert preflight.discover_done() is False


def test_discover_loop_done_with_async_saturated_pending_eligible(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_paths = []
  for day_num in (1, 2, 3):
    tar = os.path.normpath(str(daily_dir / ("2022-08-%02d.tar" % day_num)))
    open(tar, "wb").close()
    tar_paths.append(tar)

  class _SaturatedCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, *_a, **_k):
      raise AssertionError("submit should not run when saturated at slice start")

    def active_or_submitted_tar_paths(self):
      return set(tar_paths[:2])

    def entry_progress_snapshot(self, _tar):
      return {}

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_max_inflight", lambda: 2)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, **_k: {},
  )

  preflight = _make_preflight(tmp_path, _SaturatedCoord())
  with preflight._lock:
    preflight._manifest["pending_eligible"] = [tar_paths[2]]
    preflight._manifest["pending_retry"] = []
  preflight._discover_loop()
  assert preflight.phase() == PHASE_DONE
  assert preflight.discover_done() is True
  assert preflight.pending_eligible_count() == 1


def test_discover_defers_submit_while_tail_pending(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_a = os.path.normpath(str(daily_dir / "2022-08-01.tar"))
  tar_b = os.path.normpath(str(daily_dir / "2022-08-02.tar"))
  for tar in (tar_a, tar_b):
    open(tar, "wb").close()

  submitted = []

  class _FakeTailCoord:
    enabled = True

    def pending_count(self):
      return 1

    def enqueue_tail_day(self, *_a, **_k):
      return True

    def note_deferred_above_max(self, *_a, **_k):
      return None

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, tar_path, *, reason, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def active_or_submitted_tar_paths(self):
      return set(submitted)

    def entry_progress_snapshot(self, _tar):
      return {}

  monkeypatch.setattr(cfg, "get_sync_startup_day_close_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_sync_startup_day_close_days_per_slice", lambda: 5)
  monkeypatch.setattr(cfg, "get_sync_day_close_candidate_report", lambda: False)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_remaining_raw_stats_by_daily_gz",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_ingest_complete_by_checkpoint",
      lambda *_a, **_k: [tar_a, tar_b],
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_quiescent_tar_needs_day_close_at_startup",
      lambda *_a, **_k: [],
  )

  preflight = _make_preflight(
      tmp_path,
      _FakeCoord(),
      tail_ingest_coordinator=_FakeTailCoord(),
  )
  preflight._discover_slice()
  assert submitted == []
  assert preflight.pending_eligible_count() == 2


def test_boot_reconcile_false_when_only_pending_eligible_handoff(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-04-22.tar"))
  open(tar_path, "wb").close()

  class _FakeCoord:
    def get_disqualified_daily_tars(self):
      return set()

    def submit_day_close(self, *_a, **_k):
      return True

    def active_or_submitted_tar_paths(self):
      return set()

    def is_complete(self, _tar_path):
      return True

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "build_unprocessed_raw_by_daily_tar",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_ingest_complete_by_checkpoint",
      lambda *_a, **_k: [],
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close."
      "days_quiescent_tar_needs_day_close_at_startup",
      lambda *_a, **_k: [],
  )

  preflight = _make_preflight(tmp_path, _FakeCoord())
  preflight._manifest["phase"] = PHASE_DONE
  preflight._manifest["completed_at"] = 1.0
  preflight._manifest["pending_eligible"] = [tar_path]
  preflight._manifest["pending_retry"] = []
  assert preflight._needs_boot_reconcile() is False
