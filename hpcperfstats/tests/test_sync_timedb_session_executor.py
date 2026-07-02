"""Session executor wiring for startup coordinators."""

from hpcperfstats.dbload.lib.sync_timedb_startup_day_close import (
    StartupDayClosePreflight,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
)


def test_startup_day_close_preflight_uses_session_executor(monkeypatch, tmp_path):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_day_close.cfg.get_sync_startup_day_close_preflight",
      lambda: True,
  )
  preflight = StartupDayClosePreflight(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      async_day_close=None,
      get_disqualification_inputs=lambda: {},
      get_unmapped_closed_raw_tars=lambda: set(),
      day_phases=lambda: {},
      get_startup_snapshot=lambda: None,
      get_accrual_remaining_raw_by_gz=lambda: None,
      tail_ingest_coordinator=None,
  )
  assert preflight.enabled is True


def test_startup_archive_scan_coordinator_construct(tmp_path):
  coord = StartupArchiveScanCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      log_fn=lambda *_a, **_k: None,
  )
  assert coord.get_snapshot() is None
