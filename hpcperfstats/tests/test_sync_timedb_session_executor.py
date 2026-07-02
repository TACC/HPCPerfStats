"""Session executor wiring for startup coordinators."""

from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
)


def test_startup_archive_scan_coordinator_construct(tmp_path):
  coord = StartupArchiveScanCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      log_fn=lambda *_a, **_k: None,
  )
  assert coord.get_snapshot() is None
