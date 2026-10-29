"""A failed seal must show zstd's error and must not look like an ok stage.

hpcperfstats04 2026-10-28: the 08-22 and 08-23 seals failed with zstd exit
status 27 seven times. The log kept only ``returned non-zero exit status 27``
(the captured stderr was dropped) and day-close still logged
``stage=seal result=ok``, so post_seal_verify failed for hours with no cause
in the log.
"""

from __future__ import annotations

import inspect
import subprocess

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as ah,
  sync_timedb_queue_orchestrator as qo,
)


def _seal_kwargs(logs):
  return {
    "zstd_threads": 1,
    "compress_level": 1,
    "keep_uncompressed_tar": True,
    "log_fn": lambda msg, **_k: logs.append(msg),
    "remaining_raw_by_gz": None,
    "force_remove_uncompressed_tar": False,
  }


def _fail_seal(*_a, **_k):
  raise subprocess.CalledProcessError(
    27, ["zstd"], stderr="zstd: error 27 : Read error : Input/output error"
  )


def test_seal_failure_log_carries_the_zstd_stderr(monkeypatch):
  monkeypatch.setattr(ah, "atomic_seal_tar_to_zst", _fail_seal)
  logs: list[str] = []
  ok = ah._seal_one_daily_tar(
    "/d/2026-08-23.tar", "/d/2026-08-23.tar.zst", "", **_seal_kwargs(logs)
  )
  assert ok is False
  assert any("Read error : Input/output error" in m for m in logs)
  assert any(m.startswith("Seal failed for /d/2026-08-23.tar") for m in logs)


def test_seal_success_returns_true(monkeypatch):
  monkeypatch.setattr(ah, "atomic_seal_tar_to_zst", lambda *_a, **_k: {})
  monkeypatch.setattr(
    ah, "drop_legacy_gz_if_equivalent_to_zst", lambda *_a, **_k: None
  )
  ok = ah._seal_one_daily_tar(
    "/d/2026-08-23.tar", "/d/2026-08-23.tar.zst", "", **_seal_kwargs([])
  )
  assert ok is True


def test_seal_dirty_returns_the_tar_paths_that_failed(monkeypatch, tmp_path):
  tar = tmp_path / "2026-08-23.tar"
  tar.write_bytes(b"x")
  monkeypatch.setattr(ah, "should_seal_daily_tar", lambda *_a, **_k: True)
  monkeypatch.setattr(ah, "atomic_seal_tar_to_zst", _fail_seal)
  failed = ah.seal_dirty_daily_archives(
    str(tmp_path),
    local_tz=None,
    zstd_threads=1,
    compress_level=1,
    keep_uncompressed_tar=True,
    idle_seconds=0,
    seal_immediately_if_dirty=True,
    log_fn=lambda *_a, **_k: None,
  )
  assert failed == [str(tar)]


def test_day_close_seal_stage_reports_the_failure():
  src = inspect.getsource(qo._run_day_close_job)
  assert src.count('reason="seal_failed"') == 2
