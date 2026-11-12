"""The A18 sealed integrity sweep runs off the reconstruct-coordinator thread.

Production signature (hpcperfstats01 2026-11-11): the coordinator ran
``sealed_integrity_sweep_once`` inline; one check of 2026-08-02 (``zstd -t``
plus a full member stream of a multi-TB day on a saturated volume) took 5.6 h,
and the 60 s census, idle reconstruct, day_close enqueue, dead-letter readmit
and the 600 s progress report all stopped for that long (census gap 20199 s
ending the second the integrity census recorded the day).
"""

from __future__ import annotations

import threading
import time

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)


@pytest.fixture
def _idle_pass_env(monkeypatch):
  monkeypatch.setattr(qo, "_last_idle_reconstruct_mono", 0.0)
  monkeypatch.setattr(qo, "_discover_bg_is_busy", lambda: False)
  monkeypatch.setattr(qo, "_submit_background_discover", lambda *a, **k: None)
  monkeypatch.setattr(qo, "_full_rediscover_due", lambda *a, **k: False)
  monkeypatch.setattr(
    qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0
  )
  monkeypatch.setattr(qo, "_readmit_due_dead_letters", lambda *a, **k: 0)
  monkeypatch.setattr(qo, "sweep_dead_archive_scratch", lambda *a, **k: 0)
  monkeypatch.setattr(jq, "enqueue_list_job", lambda *a, **k: True)
  monkeypatch.setattr(jq, "claim_list_job", lambda *a, **k: None)
  release = threading.Event()
  started = threading.Event()
  calls: list[str] = []

  def _slow_sweep(daily_dir, *, log_fn=None):
    calls.append(daily_dir)
    started.set()
    assert release.wait(10.0)
    return None

  monkeypatch.setattr(qo, "sealed_integrity_sweep_once", _slow_sweep)
  yield release, started, calls
  release.set()
  thr = qo._integrity_sweep_thread
  if thr is not None:
    thr.join(5.0)


def _pass(**kw):
  return qo._idle_reconstruct_pass(
    object(),
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda *a, **k: None,
    mtime_days=1,
    **kw,
  )


def test_slow_integrity_sweep_does_not_block_the_idle_pass(_idle_pass_env):
  release, started, calls = _idle_pass_env
  t0 = time.monotonic()
  _pass()
  assert time.monotonic() - t0 < 2.0, "idle pass waited on the sweep"
  assert started.wait(5.0)
  thr = qo._integrity_sweep_thread
  assert thr is not None and thr.is_alive() and thr.daemon
  # A second pass while the sweep runs does not start another check.
  qo._last_idle_reconstruct_mono = 0.0
  _pass()
  assert calls == ["/daily"]
  assert qo._integrity_sweep_thread is thr
  release.set()
  thr.join(5.0)
  assert not thr.is_alive()
  # Once it finished, the next pass starts the next check.
  qo._last_idle_reconstruct_mono = 0.0
  _pass()
  assert started.wait(5.0)
  qo._integrity_sweep_thread.join(5.0)
  assert calls == ["/daily", "/daily"]


def test_run_once_force_pass_waits_for_the_sweep(_idle_pass_env):
  release, _started, calls = _idle_pass_env
  threading.Timer(0.3, release.set).start()
  t0 = time.monotonic()
  _pass(force=True)
  assert time.monotonic() - t0 >= 0.25, "force pass must finish the sweep"
  assert calls == ["/daily"]
  assert not qo._integrity_sweep_thread.is_alive()


def test_sweep_error_is_logged_not_swallowed(monkeypatch):
  def _boom(daily_dir, *, log_fn=None):
    raise OSError("EIO reading census")

  monkeypatch.setattr(qo, "sealed_integrity_sweep_once", _boom)
  logs: list[str] = []
  thr = qo._start_background_integrity_sweep(
    "/daily", lambda msg, **_k: logs.append(msg)
  )
  thr.join(5.0)
  assert any(
    "sealed integrity sweep error" in m and "EIO reading census" in m
    for m in logs
  )


def test_integrity_sweep_zstd_test_uses_archive_thread_count(
  monkeypatch, tmp_path
):
  from hpcperfstats.dbload.lib import (
    sync_timedb_archive_helpers as ah,
    sync_timedb_sealed_member_map as smm,
    zstd_cli,
  )

  (tmp_path / "2026-08-02.tar.zst").write_bytes(b"x")
  seen: list[int] = []
  monkeypatch.setattr(
    zstd_cli, "zstd_test", lambda path, threads: seen.append(threads)
  )
  monkeypatch.setattr(ah, "get_archive_zstd_thread_count", lambda: 4)
  monkeypatch.setattr(
    ah,
    "_scan_compressed_archive_members_and_readable",
    lambda path: (True, {"h/1": 1}),
  )
  monkeypatch.setattr(smm, "sealed_members_lookup", lambda *a, **k: None)
  monkeypatch.setattr(smm, "write_sealed_member_map", lambda *a, **k: None)
  result = smm.sealed_integrity_sweep_once(str(tmp_path), log_fn=None)
  assert result["status"] == "ok"
  assert seen == [4]
