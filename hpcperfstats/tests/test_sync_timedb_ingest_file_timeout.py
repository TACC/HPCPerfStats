"""Size-proportional per-file ingest timeout resolver and wiring."""

from __future__ import annotations

import math

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout_mod


def _mib_bytes(mib):
  return int(mib) * 1024 * 1024


def _patch_stats_file_size_bytes(monkeypatch, fn):
  monkeypatch.setattr(st, "stats_file_size_bytes", fn)
  monkeypatch.setattr(ingest_timeout_mod, "stats_file_size_bytes", fn)


def _default_timeout_getters(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_per_file_timeout_s_per_mib",
      lambda: 13500.0 / 5120.0,
  )
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0)


@pytest.mark.parametrize(
    ("size_bytes", "expected_timeout"),
    [
        (0, 900.0),
        (_mib_bytes(66), 900.0 + 66.0 * (13500.0 / 5120.0)),
        (_mib_bytes(342), 900.0 + 342.0 * (13500.0 / 5120.0)),
        (_mib_bytes(512), 2250.0),
        (_mib_bytes(3482), 900.0 + 3482.0 * (13500.0 / 5120.0)),
        (_mib_bytes(5120), 14400.0),
        (_mib_bytes(10240), 14400.0),
    ],
)
def test_resolve_ingest_per_file_timeout_s_table(
    monkeypatch, tmp_path, size_bytes, expected_timeout,
):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  if size_bytes > 0:
    stats_file.write_bytes(b"x" * size_bytes)
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  resolved = st.resolve_ingest_per_file_timeout_s(str(stats_file))
  assert math.isclose(resolved, expected_timeout, rel_tol=0, abs_tol=0.05)


def test_resolve_ingest_per_file_timeout_s_disabled_when_floor_zero(monkeypatch, tmp_path):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 0.0)
  stats_file = tmp_path / "segment"
  stats_file.write_bytes(b"x" * _mib_bytes(5120))
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: _mib_bytes(5120))
  assert st.resolve_ingest_per_file_timeout_s(str(stats_file)) == 0.0


def test_run_ingest_timed_uses_resolved_timeout(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  stats_file.write_bytes(b"x" * _mib_bytes(5120))
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: _mib_bytes(5120))
  seen = []

  def fake_setitimer(which, seconds):
    seen.append(float(seconds))

  monkeypatch.setattr(st.signal, "setitimer", fake_setitimer, raising=False)
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "clear_worker_stage", lambda: None)
  monkeypatch.setattr(st, "_log_long_ingest_timeout_budget_if_needed", lambda *_a, **_k: None)

  st._run_ingest_timed(str(stats_file), "parse", lambda: "ok")
  assert any(math.isclose(value, 14400.0, rel_tol=0, abs_tol=0.01) for value in seen)


def test_long_timeout_budget_logs_warning(monkeypatch, tmp_path, capsys):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  size_bytes = _mib_bytes(512)
  stats_file.write_bytes(b"x" * size_bytes)
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "update_worker_substage", lambda *_a, **_k: None)

  timeout_s = st.resolve_ingest_per_file_timeout_s(str(stats_file))
  assert timeout_s >= st.INGEST_PER_FILE_TIMEOUT_LOG_MIN_S
  st._log_long_ingest_timeout_budget_if_needed(str(stats_file), timeout_s)
  out = capsys.readouterr().out
  assert "WARN: ingest per-file timeout budget" in out
  assert str(size_bytes) in out
  assert "timeout_s=" in out


def test_long_timeout_budget_skips_small_files(monkeypatch, tmp_path, capsys):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  size_bytes = _mib_bytes(66)
  stats_file.write_bytes(b"x" * size_bytes)
  timeout_s = st.resolve_ingest_per_file_timeout_s(str(stats_file))
  assert timeout_s < st.INGEST_PER_FILE_TIMEOUT_LOG_MIN_S
  st._log_long_ingest_timeout_budget_if_needed(str(stats_file), timeout_s)
  assert "WARN: ingest per-file timeout budget" not in capsys.readouterr().out


def test_warn_if_pool_stall_wall_below_ingest_timeout_max(monkeypatch, capsys):
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 192)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 14400.0)
  st._warn_if_pool_stall_wall_below_ingest_timeout_max()
  out = capsys.readouterr().out
  assert "WARN: sync_pool stall ceiling wall" in out
  assert "sync_ingest_per_file_timeout_max_s=14400s" in out
  assert "sync_pool_stall_abort_after_timeouts ceiling to at least 2881" in out


def test_stall_abort_polls_for_batch_small_files(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 2881)
  small = tmp_path / "small"
  small.write_bytes(b"x" * 1024)
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: 1024)
  polls = st._stall_abort_polls_for_batch([str(small)])
  assert polls == 181


def test_stall_abort_polls_for_batch_large_file(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 2881)
  size_bytes = _mib_bytes(5120)
  large = tmp_path / "large"
  large.write_bytes(b"x" * min(size_bytes, 65536))
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  expected_timeout = st.resolve_ingest_per_file_timeout_s(str(large))
  polls = st._stall_abort_polls_for_batch([str(large)])
  assert expected_timeout == 14400.0
  assert polls == int(expected_timeout / 5.0) + 1


def test_stall_abort_polls_respects_ini_ceiling(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 100)
  size_bytes = _mib_bytes(5120)
  large = tmp_path / "large"
  large.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  assert st._stall_abort_polls_for_batch([str(large)]) == 100


def test_calendar_day_from_sealed_archive_path(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
      calendar_day_from_sealed_archive_path,
  )

  assert calendar_day_from_sealed_archive_path(
      str(tmp_path / "2024-03-15.tar.zst"),
  ) == "2024-03-15"
  assert calendar_day_from_sealed_archive_path(
      str(tmp_path / "2024-03-15.tar.gz"),
  ) == "2024-03-15"


def test_stall_abort_polls_for_sealed_archives_respects_ini_ceiling(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 100)
  from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout_mod

  monkeypatch.setattr(
      ingest_timeout_mod,
      "_redis_member_count_for_sealed_day",
      lambda _day: 500,
  )
  sealed = tmp_path / "2024-01-01.tar.zst"
  sealed.write_bytes(b"x")
  monkeypatch.setattr(ingest_timeout_mod.os.path, "getsize", lambda _p: 64 * 1024 * 1024)
  assert ingest_timeout_mod.stall_abort_polls_for_sealed_archives([str(sealed)]) == 100


def test_raise_if_ingest_per_file_deadline_uses_effective_timeout(monkeypatch):
  import time

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      reset_ingest_task_deadline_monotonic,
      reset_ingest_task_effective_timeout_s,
      set_ingest_task_deadline_monotonic,
      set_ingest_task_effective_timeout_s,
  )

  deadline_token = set_ingest_task_deadline_monotonic(time.monotonic() - 1.0)
  effective_token = set_ingest_task_effective_timeout_s(5183.0)
  try:
    with pytest.raises(st.IngestPerFileTimeoutError) as excinfo:
      st._raise_if_ingest_per_file_deadline_exceeded("/tmp/f", "db_write_host")
    assert excinfo.value.elapsed_s == 5183.0
  finally:
    reset_ingest_task_effective_timeout_s(effective_token)
    reset_ingest_task_deadline_monotonic(deadline_token)
