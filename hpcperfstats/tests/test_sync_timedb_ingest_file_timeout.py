"""Size-proportional per-file ingest timeout resolver and wiring."""

from __future__ import annotations

import math
import signal
import tarfile
import threading
import time

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import (
  sync_timedb_ingest_timeout as ingest_timeout_mod,
)

_SIGALRM_AVAILABLE = hasattr(signal, "SIGALRM")


def _mib_bytes(mib):
  return int(mib) * 1024 * 1024


def _patch_stats_file_size_bytes(monkeypatch, fn):
  monkeypatch.setattr(st, "stats_file_size_bytes", fn)
  monkeypatch.setattr(ingest_timeout_mod, "stats_file_size_bytes", fn)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_parsing.stats_file_size_bytes",
      fn,
  )


# Slope keeps historical (86400−900)/30720 anchor; floor default is independent (3600).
_PER_MIB_DEFAULT = (86400.0 - 900.0) / 30720.0
_FLOOR_DEFAULT = 3600.0
_MAX_TIMEOUT_DEFAULT = 86400.0


def _default_timeout_getters(monkeypatch):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: _FLOOR_DEFAULT)
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_per_file_timeout_s_per_mib",
      lambda: _PER_MIB_DEFAULT,
  )
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: _MAX_TIMEOUT_DEFAULT,
  )


@pytest.mark.parametrize(
    ("size_bytes", "expected_timeout"),
    [
        (0, _FLOOR_DEFAULT),
        (_mib_bytes(66), _FLOOR_DEFAULT + 66.0 * _PER_MIB_DEFAULT),
        (_mib_bytes(2048), _FLOOR_DEFAULT + 2048.0 * _PER_MIB_DEFAULT),
        (_mib_bytes(512), _FLOOR_DEFAULT + 512.0 * _PER_MIB_DEFAULT),
        (_mib_bytes(5120), _FLOOR_DEFAULT + 5120.0 * _PER_MIB_DEFAULT),
        (_mib_bytes(30720), _MAX_TIMEOUT_DEFAULT),
        (_mib_bytes(35000), _MAX_TIMEOUT_DEFAULT),
    ],
)
def test_resolve_ingest_per_file_timeout_s_table(
    monkeypatch, tmp_path, size_bytes, expected_timeout,
):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  if size_bytes > 0:
    stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  resolved = st.resolve_ingest_per_file_timeout_s(str(stats_file))
  assert math.isclose(resolved, expected_timeout, rel_tol=0, abs_tol=0.05)


def test_resolve_ingest_per_file_timeout_s_disabled_when_floor_zero(monkeypatch, tmp_path):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 0.0)
  stats_file = tmp_path / "segment"
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: _mib_bytes(5120))
  assert st.resolve_ingest_per_file_timeout_s(str(stats_file)) == 0.0


def test_run_ingest_timed_uses_resolved_timeout(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: _mib_bytes(30720))
  seen = []

  def fake_setitimer(which, seconds):
    seen.append(float(seconds))

  monkeypatch.setattr(st.signal, "setitimer", fake_setitimer, raising=False)
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "clear_worker_stage", lambda: None, raising=False)
  monkeypatch.setattr(st, "_log_long_ingest_timeout_budget_if_needed", lambda *_a, **_k: None)

  st._run_ingest_timed(str(stats_file), "parse", lambda: "ok")
  assert any(math.isclose(value, 86400.0, rel_tol=0, abs_tol=0.01) for value in seen)


def test_ingest_per_file_timeout_log_min_default():
  assert st.INGEST_PER_FILE_TIMEOUT_LOG_MIN_S == 7200.0


def test_c672_017_class_budget_covers_slow_cohort_success(monkeypatch, tmp_path):
  """~14.3 MiB path that needed 2304.7s must clear under shipped floor 3600."""
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "c672-017-class"
  size_bytes = 14984928  # operator paste: c672-017
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  resolved = st.resolve_ingest_per_file_timeout_s(str(stats_file))
  assert resolved >= 2305.0
  assert resolved == pytest.approx(
      _FLOOR_DEFAULT + 15.0 * _PER_MIB_DEFAULT, abs=0.05,
  )


def test_long_timeout_budget_no_warn_log(monkeypatch, tmp_path, capsys):
  """Large budgets update registry only — no WARN (SOP on outcome line)."""
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  size_bytes = _mib_bytes(1500)
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  substages = []
  monkeypatch.setattr(
      st, "update_worker_substage",
      lambda *a, **k: substages.append((a, k)),
  )

  timeout_s = st.resolve_ingest_per_file_timeout_s(str(stats_file))
  assert timeout_s >= st.INGEST_PER_FILE_TIMEOUT_LOG_MIN_S
  st._log_long_ingest_timeout_budget_if_needed(str(stats_file), timeout_s)
  out = capsys.readouterr().out
  assert "WARN: ingest per-file timeout budget" not in out
  assert substages
  assert substages[0][0][0] == "long_timeout_budget"


def test_long_timeout_budget_skips_small_files(monkeypatch, tmp_path, capsys):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  size_bytes = _mib_bytes(66)
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
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


def test_warn_if_pool_stall_wall_ok_at_shipped_defaults(monkeypatch, capsys):
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 17320)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 86400.0)
  st._warn_if_pool_stall_wall_below_ingest_timeout_max()
  assert "WARN: sync_pool stall ceiling wall" not in capsys.readouterr().out


def test_stall_abort_polls_for_batch_small_files(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 17320)
  small = tmp_path / "small"
  small.write_bytes(b"x" * 1024)
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: 1024)
  polls = st._stall_abort_polls_for_batch([str(small)])
  grace = ingest_timeout_mod.STALL_ABORT_GRACE_S
  assert polls == int((_FLOOR_DEFAULT + grace) / 5.0) + 1


def test_stall_abort_polls_for_batch_large_file(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 17320)
  size_bytes = _mib_bytes(30720)
  large = tmp_path / "large"
  large.write_bytes(b"x" * min(size_bytes, 65536))
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  expected_timeout = st.resolve_ingest_per_file_timeout_s(str(large))
  polls = st._stall_abort_polls_for_batch([str(large)])
  grace = ingest_timeout_mod.STALL_ABORT_GRACE_S
  assert expected_timeout == 86400.0
  assert polls == int((expected_timeout + grace) / 5.0) + 1


def test_stall_abort_polls_scales_to_30gib_budget(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 17320)
  size_bytes = _mib_bytes(30720)
  giant = tmp_path / "giant30g"
  giant.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  polls = st._stall_abort_polls_for_batch([str(giant)])
  grace = ingest_timeout_mod.STALL_ABORT_GRACE_S
  assert polls == int((86400.0 + grace) / 5.0) + 1
  assert polls < 17320


def test_stall_abort_polls_includes_grace_beyond_batch_max(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 900.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 17320)
  small = tmp_path / "small"
  small.write_bytes(b"x" * 1024)
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: 1024)
  batch_max = st.resolve_ingest_per_file_timeout_s(str(small))
  polls = ingest_timeout_mod.stall_abort_polls_for_paths([str(small)])
  grace = ingest_timeout_mod.STALL_ABORT_GRACE_S
  wall_s = polls * 5.0
  assert wall_s >= batch_max + grace - 5.0
  assert polls == int((batch_max + grace) / 5.0) + 1


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
  from hpcperfstats.dbload.lib import (
    sync_timedb_ingest_timeout as ingest_timeout_mod,
  )

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












































def test_ingest_remaining_count_never_negative():
  assert st._ingest_remaining_count(100, 150) == 0
  assert st._ingest_remaining_count(100, 99) == 0
  assert st._ingest_remaining_count(100, 50) == 49








def test_extend_ingest_task_deadline_monotonic():
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      extend_ingest_task_deadline_monotonic,
      get_ingest_task_deadline_monotonic,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )

  token = set_ingest_task_deadline_monotonic(100.0)
  try:
    extend_ingest_task_deadline_monotonic(0.0)
    assert get_ingest_task_deadline_monotonic() == 100.0
    extend_ingest_task_deadline_monotonic(1.5)
    assert get_ingest_task_deadline_monotonic() == 101.5
  finally:
    reset_ingest_task_deadline_monotonic(token)


@pytest.mark.skipif(not _SIGALRM_AVAILABLE, reason="SIGALRM not available")
def test_suspend_sigalrm_extends_deadline_monotonic(monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      get_ingest_task_deadline_monotonic,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_sigalrm import (
      suspend_ingest_sigalrm_for_populate_wait,
  )

  base = time.monotonic() + 2.0
  token = set_ingest_task_deadline_monotonic(base)
  arm_calls = []
  original_setitimer = signal.setitimer

  def counting_setitimer(which, seconds, interval=0.0):
    arm_calls.append(float(seconds))
    return original_setitimer(which, seconds, interval)

  monkeypatch.setattr(signal, "setitimer", counting_setitimer)

  signal.signal(signal.SIGALRM, lambda *_a: None)
  signal.setitimer(signal.ITIMER_REAL, 2.0)
  try:
    with suspend_ingest_sigalrm_for_populate_wait():
      time.sleep(0.15)
    extended = get_ingest_task_deadline_monotonic()
    assert extended is not None
    assert extended > base
    assert math.isclose(extended - base, 0.15, abs_tol=0.08)
    assert any(value > 0.0 for value in arm_calls)
  finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
    reset_ingest_task_deadline_monotonic(token)


@pytest.mark.skipif(not _SIGALRM_AVAILABLE, reason="SIGALRM not available")
def test_ingest_populate_wait_survives_sigalrm(monkeypatch, tmp_path):
  """Short per-file SIGALRM must not fire during Redis populate wait."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _daily_archive_members_cache_key,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    build_archive_members_redis_keys,
    request_archive_members_populate_and_wait,
    reset_archive_members_redis_client_for_tests,
  )
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import (
    FakeRedis,
  )

  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 0.15)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s_per_mib", lambda: 0.0)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 0.15)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_cache_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )

  fake = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  reset_archive_members_redis_client_for_tests()

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  cache_key = _daily_archive_members_cache_key(str(day_gz))
  keys = build_archive_members_redis_keys(cache_key)
  fake.set(keys.lock_key, "tok:999999997", ex=30)
  fake.set(keys.complete_key, "0")

  populate_done = threading.Event()

  def _finish_populate():
    time.sleep(0.35)
    fake.hset(keys.hash_key, mapping={"host/raw": "4"})
    fake.set(keys.complete_key, "1")
    populate_done.set()

  threading.Thread(target=_finish_populate, daemon=True).start()
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "clear_worker_stage", lambda: None, raising=False)
  monkeypatch.setattr(st, "_log_long_ingest_timeout_budget_if_needed", lambda *_a, **_k: None)

  stats_file = tmp_path / "segment"
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: 1024)

  result = st._run_ingest_timed(
      str(stats_file),
      "ingest",
      lambda: request_archive_members_populate_and_wait(str(day_gz)),
  )
  assert populate_done.wait(timeout=2.0)
  assert result.get("host/raw") == 4


@pytest.mark.skipif(not _SIGALRM_AVAILABLE, reason="SIGALRM not available")
def test_parse_still_times_out_without_populate_wait(monkeypatch, tmp_path):
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 0.1)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s_per_mib", lambda: 0.0)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 0.1)
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "clear_worker_stage", lambda: None, raising=False)
  monkeypatch.setattr(st, "_log_long_ingest_timeout_budget_if_needed", lambda *_a, **_k: None)

  stats_file = tmp_path / "segment"
  stats_file.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: 1024)

  with pytest.raises(st.IngestPerFileTimeoutError) as excinfo:
    st._run_ingest_timed(
        str(stats_file),
        "parse",
        lambda: time.sleep(0.4),
    )
  assert excinfo.value.stage == "parse"


def test_ingest_timeout_during_streaming_parse_not_quarantined(monkeypatch, tmp_path):
  """SIGALRM / IngestPerFileTimeoutError inside streaming parse must not DLO."""
  stats_file = tmp_path / "host.hpc" / "1784000000"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("x", encoding="utf-8")
  target = str(stats_file)
  quarantine_calls = []

  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("host.hpc", "1784000000"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(st, "_should_stream_stats_file", lambda *_a, **_k: True)
  monkeypatch.setattr(
      st,
      "_resolve_streaming_ingest_start",
      lambda *_a, **_k: (False, (0, True)),
  )

  def boom(*_a, **_k):
    raise st.IngestPerFileTimeoutError(target, "ingest", 933.4)

  monkeypatch.setattr(st, "parse_stats_file_streaming", boom)
  monkeypatch.setattr(
      st,
      "_quarantine_failed_ingest_parse",
      lambda path, error_detail=None: quarantine_calls.append((path, error_detail)) or True,
  )
  monkeypatch.setattr(st, "update_worker_substage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "record_worker_stage", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "_log_long_ingest_timeout_budget_if_needed", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "_log_ingest_per_file_timeout", lambda *_a, **_k: None)
  # Disable SIGALRM wrapper so the raised timeout is caught by the outer handler.
  monkeypatch.setattr(st.cfg, "get_sync_ingest_per_file_timeout_s", lambda: 0.0)

  result = st._parse_stats_file_payload(target)
  (
      out_path,
      payload,
      need_archival,
      ingest_ok,
      elapsed_s,
      meta,
  ) = st._unpack_parse_payload_result(result)
  assert out_path == target
  assert payload is None
  assert need_archival is False
  assert ingest_ok is False
  assert meta.get("outcome") == "timeout"
  assert meta.get("archive_skip") == "timeout"
  assert elapsed_s >= 0.0
  assert quarantine_calls == []
  assert stats_file.exists()


def test_parse_exception_still_quarantines_non_timeout(monkeypatch, tmp_path):
  """Ordinary parse ValueError still routes to DLO quarantine."""
  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "bad_raw"
  raw_path.write_text("1778200758 job1 cn001\n", encoding="utf-8")
  target = str(raw_path)

  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.cfg, "get_archive_dir_path", lambda: str(archive_dir))
  monkeypatch.setattr(st, "parse_stats_file_path", lambda _p: ("host.hpc", "bad_raw"))
  monkeypatch.setattr(st, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(
      st,
      "load_stats_file_lines",
      lambda *_a, **_k: (["1778200758 job1 cn001\n"], None),
  )
  monkeypatch.setattr(
      st,
      "parse_first_timestamp_line",
      lambda _lines: ("1778200758", "job1", "cn001"),
  )
  monkeypatch.setattr(st, "head_timestamp_present_in_db", lambda *_a, **_k: False)
  monkeypatch.setattr(st, "update_worker_substage", lambda *_a, **_k: None)

  def boom(*_a, **_k):
    raise ValueError("corrupt stats line")

  monkeypatch.setattr(st, "parse_stats_lines", boom)

  (
      out_path,
      payload,
      need_archival,
      ingest_ok,
      _elapsed,
      meta,
  ) = st._unpack_parse_payload_result(st._parse_stats_file_payload(target))
  assert out_path == target
  assert payload is None
  assert need_archival is False
  assert ingest_ok is True
  assert meta.get("outcome") == "quarantine"
  assert "corrupt stats line" in str(meta.get("fail_reason") or "")
  assert not raw_path.exists()


def test_log_ingest_per_file_timeout_includes_size_and_rate(capsys, tmp_path):
  target = tmp_path / "segment"
  target.write_bytes(b"x" * 1000)
  exc = st.IngestPerFileTimeoutError(str(target), "ingest", 10.0)
  assert exc.size_bytes == 1000
  st._log_ingest_per_file_timeout(exc)
  out = capsys.readouterr().out
  assert "size_bytes=1000" in out
  assert "bytes_per_s=100" in out
  assert "elapsed=10.0s" in out
  assert "stage=ingest" in out


def test_suspend_sigalrm_for_non_work_extends_deadline_monotonic(monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      get_ingest_task_deadline_monotonic,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_sigalrm import (
      suspend_ingest_sigalrm_for_non_work_wait,
  )

  if not _SIGALRM_AVAILABLE:
    pytest.skip("SIGALRM not available")

  base = time.monotonic() + 2.0
  token = set_ingest_task_deadline_monotonic(base)
  arm_calls = []
  original_setitimer = signal.setitimer

  def counting_setitimer(which, seconds, interval=0.0):
    arm_calls.append(float(seconds))
    return original_setitimer(which, seconds, interval)

  monkeypatch.setattr(signal, "setitimer", counting_setitimer)
  signal.signal(signal.SIGALRM, lambda *_a: None)
  signal.setitimer(signal.ITIMER_REAL, 2.0)
  try:
    with suspend_ingest_sigalrm_for_non_work_wait():
      time.sleep(0.15)
    extended = get_ingest_task_deadline_monotonic()
    assert extended is not None
    assert extended > base
    assert math.isclose(extended - base, 0.15, abs_tol=0.08)
    assert any(value > 0.0 for value in arm_calls)
  finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
    reset_ingest_task_deadline_monotonic(token)


def test_write_lock_wait_extends_deadline_and_accumulates_timing(monkeypatch):
  """Manager acquire wait must extend deadline and count into db_shard_lock_s."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      get_ingest_task_deadline_monotonic,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )

  class _SlowLock:
    def acquire(self):
      time.sleep(0.12)

    def release(self):
      return None

  st._reset_ingest_write_timing()
  base = time.monotonic() + 5.0
  token = set_ingest_task_deadline_monotonic(base)
  try:
    with st._held_ingest_write_lock(_SlowLock(), "/tmp/seg", "proc"):
      time.sleep(0.05)
    snap = st._snapshot_ingest_write_timing()
    extended = get_ingest_task_deadline_monotonic()
    assert extended is not None
    assert extended > base
    assert snap["db_shard_lock_s"] >= 0.08
    assert snap["postgres_s"] >= 0.03
  finally:
    reset_ingest_task_deadline_monotonic(token)
    st._reset_ingest_write_timing()


def test_ingest_file_outcome_timing_breakdown_tokens(monkeypatch):
  """Outcome log must emit parse / lock / postgres / elapsed / timeout_s."""
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 1657207171)
  outcome = st.IngestFileOutcome(
      path="/archive/host/seg",
      elapsed_s=12.5,
      ingest_ok=True,
      need_archival=False,
      outcome="ingested",
      parse_elapsed_s=1.5,
      db_shard_lock_s=3.25,
      postgres_s=4.0,
      timeout_s=8000.2,
      stats_rows=10,
      proc_rows=2,
  )
  logged = []

  def _capture(*args, **kwargs):
    del kwargs
    logged.append(" ".join(str(a) for a in args))

  import hpcperfstats.dbload.sync_timedb as mod

  old = mod.log_print
  mod.log_print = _capture
  try:
    st._log_ingest_file_outcome(outcome)
  finally:
    mod.log_print = old
  assert logged
  joined = " ".join(logged)
  assert "parse_elapsed_s=1.5" in joined
  assert "db_shard_lock_s=3.2" in joined
  assert "postgres_s=4.0" in joined
  assert "elapsed_s=12.5" in joined
  assert "timeout_s=8000.2" in joined
  assert "size_bytes=1657207171" in joined


def test_timeout_s_on_outcome_from_meta():
  """Packed meta timeout_s flows into IngestFileOutcome."""
  outcome = st._ingest_file_outcome_from_worker(
      "/p",
      False,
      False,
      10.0,
      {
          "outcome": "timeout",
          "fail_reason": "write",
          "timeout_s": 7200.0,
          "db_shard_lock_s": 1.0,
          "postgres_s": 2.0,
      },
  )
  assert outcome.timeout_s == 7200.0
  assert outcome.fail_reason == "write"
