"""Unit tests for startup checkpoint-driven async DAY_CLOSE preflight."""

import json
import os
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.sync_timedb_startup_day_close import (
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

    def submit_day_close(self, tar_path, *, reason):
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
