"""Size-proportional per-file ingest timeout resolver and wiring."""

from __future__ import annotations

import math
import os
import signal
import tarfile
import threading
import time

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout_mod

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


def test_long_timeout_budget_logs_warning(monkeypatch, tmp_path, capsys):
  _default_timeout_getters(monkeypatch)
  stats_file = tmp_path / "segment"
  # Budget must exceed WARN min 7200: 3600 + MiB×per_mib ≥ 7200 → ≥~1294 MiB.
  size_bytes = _mib_bytes(1500)
  stats_file.write_bytes(b"x")
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
  assert polls == int(_FLOOR_DEFAULT / 5.0) + 1


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
  assert expected_timeout == 86400.0
  assert polls == int(expected_timeout / 5.0) + 1


def test_stall_abort_polls_scales_to_30gib_budget(monkeypatch, tmp_path):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 5.0)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 17320)
  size_bytes = _mib_bytes(30720)
  giant = tmp_path / "giant30g"
  giant.write_bytes(b"x")
  _patch_stats_file_size_bytes(monkeypatch, lambda _p: size_bytes)
  polls = st._stall_abort_polls_for_batch([str(giant)])
  assert polls == int(86400.0 / 5.0) + 1
  assert polls < 17320


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


def test_giant_trigger_budget_2gib_boundary(monkeypatch, tmp_path):
  """Trigger stays 6600s wall; with floor 3600, crossover is ~1078 MiB (not 2 GiB)."""
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_giant_pool_supplement_trigger_budget_s",
      lambda: 6600.0,
  )
  under = tmp_path / "under_trigger"
  at2g = tmp_path / "at2g"
  # 3600 + mib×per_mib >= 6600 → mib >= ceil(3000/per_mib) ≈ 1078
  under_mib = 1077
  assert _FLOOR_DEFAULT + under_mib * _PER_MIB_DEFAULT < 6600.0
  assert _FLOOR_DEFAULT + 2048.0 * _PER_MIB_DEFAULT >= 6600.0

  def _size_for(path):
    if "at2g" in str(path):
      return _mib_bytes(2048)
    return _mib_bytes(under_mib)

  _patch_stats_file_size_bytes(monkeypatch, _size_for)
  assert ingest_timeout_mod.is_giant_ingest_budget(str(under)) is False
  assert ingest_timeout_mod.is_giant_ingest_budget(str(at2g)) is True


def test_iter_giant_supplement_paths_exclude_normpath_variant(tmp_path, monkeypatch):
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 10**9,
  )
  path = str(tmp_path / "tail0")
  (tmp_path / "tail0").write_bytes(b"x" * 100)
  variant = os.path.join(str(tmp_path), "tail0", ".")
  picked = list(
      ingest_timeout_mod.iter_giant_supplement_paths(
          [path],
          exclude=[variant],
      ),
  )
  assert picked == []


def test_imap_pending_tail_excludes_chunk_paths(monkeypatch, tmp_path, capsys):
  """Giant supplement must not re-offer norms already in the non-prefix chunk."""
  import threading
  import time

  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      pending_minus_chunk,
  )
  from hpcperfstats.tests import test_multiprocessing_pool_health as mph_tests

  pool = mph_tests._ManualPool()
  host_a = tmp_path / "c637-051"
  host_b = tmp_path / "c637-062"
  host_a.mkdir()
  host_b.mkdir()
  chunk_path = str(host_a / "1780788583")
  tail_ok = str(host_b / "1780788584")
  (host_a / "1780788583").write_bytes(b"x" * 100)
  (host_b / "1780788584").write_bytes(b"x" * 100)
  # Non-prefix pending: head paths then chunk member mid-list.
  pending = [
      str(host_b / "head0"),
      chunk_path,
      tail_ok,
  ]
  (host_b / "head0").write_bytes(b"x" * 50)
  chunk = [chunk_path]
  pending_tail = pending_minus_chunk(pending, chunk)
  assert chunk_path not in pending_tail
  assert str(host_b / "head0") in pending_tail

  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_giant_pool_supplement_enabled", lambda: True)
  monkeypatch.setattr(st, "_effective_ingest_imap_inflight_cap", lambda _tc, _pc: 2)
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_trigger_budget_s", lambda: 100.0,
  )
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 10**9,
  )
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 0.01)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 100000)

  def _size_for(path):
    if path == chunk_path:
      return _mib_bytes(2048)
    return 100

  _patch_stats_file_size_bytes(monkeypatch, _size_for)
  tracker = st._IngestPoolInFlightTracker(chunk)
  gen = st._imap_ingest_paths_batched(
      pool,
      lambda path: path,
      chunk,
      thread_count=2,
      context="test pending_tail exclude chunk",
      tracker=tracker,
      chunk_counter=0,
      pending_count=len(pending),
      pending_tail=pending_tail,
  )

  def consumer():
    list(gen)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 2 and time.monotonic() < deadline:
    time.sleep(0.005)
  submitted = list(pool.inflight.values())
  assert chunk_path in submitted
  assert submitted.count(chunk_path) == 1
  assert any(p != chunk_path for p in submitted)
  for ar in list(pool.inflight):
    ar.finish()
  while pool.inflight and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  out = capsys.readouterr().out
  assert "duplicate dispatch suppressed path=c637-051/1780788583" not in out


def test_chunk_paths_deduped_before_imap(monkeypatch, tmp_path):
  """Duplicate full paths in a chunk must yield one pool submit."""
  from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
      dedupe_ingest_paths_preserve_order,
  )
  from hpcperfstats.tests import test_multiprocessing_pool_health as mph_tests

  path = str(tmp_path / "host" / "1781085150")
  (tmp_path / "host").mkdir()
  (tmp_path / "host" / "1781085150").write_bytes(b"x")
  unique, duplicate_n, _sample = dedupe_ingest_paths_preserve_order(
      [path, path, path],
  )
  assert len(unique) == 1
  assert duplicate_n == 2

  pool = mph_tests._ManualPool()
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st, "_effective_ingest_imap_inflight_cap", lambda _tc, _pc: 4)
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 0.01)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 100000)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_giant_pool_supplement_enabled", lambda: False)
  tracker = st._IngestPoolInFlightTracker(unique)
  gen = st._imap_ingest_paths_batched(
      pool,
      lambda p: p,
      unique,
      thread_count=2,
      context="test chunk dedupe",
      tracker=tracker,
      chunk_counter=0,
      pending_count=3,
  )
  import threading
  import time

  def consumer():
    list(gen)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 1 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count == 1
  for ar in list(pool.inflight):
    ar.finish()
  thread.join(timeout=2.0)


def test_iter_giant_supplement_paths_skips_at_or_above_large_max_bytes(
    monkeypatch, tmp_path,
):
  """Paths at/above large_max are never selected; soft max alone still allows second pass."""
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 1024,
  )
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_giant_pool_supplement_large_max_bytes",
      lambda: 1024,
  )
  small = tmp_path / "small"
  large = tmp_path / "large"
  small.write_bytes(b"x" * 512)
  large.write_bytes(b"x" * 2048)

  def _size_for(path):
    return 512 if "small" in str(path) else 2048

  _patch_stats_file_size_bytes(monkeypatch, _size_for)
  picked = list(
      ingest_timeout_mod.iter_giant_supplement_paths(
          [str(small), str(large)],
          limit=10,
      ),
  )
  assert picked == [str(small)]


def test_iter_giant_supplement_paths_two_pass_prefers_under_soft_max(
    monkeypatch, tmp_path,
):
  """First pass <1 GiB soft max; second pass fills remaining slots from [soft, large)."""
  _default_timeout_getters(monkeypatch)
  soft = 1024
  large_max = 8192
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: soft,
  )
  monkeypatch.setattr(
      st.cfg,
      "get_sync_ingest_giant_pool_supplement_large_max_bytes",
      lambda: large_max,
  )
  under = tmp_path / "under"
  mid = tmp_path / "mid"
  too_big = tmp_path / "toobig"
  under.write_bytes(b"x")
  mid.write_bytes(b"x")
  too_big.write_bytes(b"x")

  def _size_for(path):
    name = os.path.basename(str(path))
    if name == "under":
      return soft - 1
    if name == "mid":
      return soft + 10
    return large_max

  _patch_stats_file_size_bytes(monkeypatch, _size_for)
  picked = list(
      ingest_timeout_mod.iter_giant_supplement_paths(
          [str(mid), str(under), str(too_big)],
          limit=10,
      ),
  )
  assert picked == [str(under), str(mid)]
  # Dedupe: same path must not appear twice across passes.
  assert len(picked) == len(set(os.path.normpath(p) for p in picked))
  limited = list(
      ingest_timeout_mod.iter_giant_supplement_paths(
          [str(mid), str(under), str(too_big)],
          limit=1,
      ),
  )
  assert limited == [str(under)]


def test_giant_supplement_replenish_uses_supplement_queue_excludes_inflight(
    monkeypatch, tmp_path, capsys,
):
  """RC-D: dry frozen tail mid-imap refreshes up to supplement_queue, excludes in-flight."""
  import threading
  import time

  from hpcperfstats.tests import test_multiprocessing_pool_health as mph_tests

  pool = mph_tests._ManualPool()
  refill_a = str(tmp_path / "refill_a")
  refill_b = str(tmp_path / "refill_b")
  (tmp_path / "refill_a").write_bytes(b"x" * 100)
  (tmp_path / "refill_b").write_bytes(b"x" * 100)
  chunk = ["giant0"]
  replenish_calls = []

  def _replenish(exclude):
    replenish_calls.append(set(exclude or ()))
    return [refill_a, refill_b]

  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_giant_pool_supplement_enabled", lambda: True)
  monkeypatch.setattr(st, "_effective_ingest_imap_inflight_cap", lambda _tc, _pc: 3)
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_trigger_budget_s", lambda: 100.0,
  )
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 10**9,
  )
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 0.01)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 100000)

  def _size_for(path):
    base = os.path.basename(str(path))
    if base.startswith("giant"):
      return _mib_bytes(2048)
    return 100

  _patch_stats_file_size_bytes(monkeypatch, _size_for)
  tracker = st._IngestPoolInFlightTracker(chunk)
  gen = st._imap_ingest_paths_batched(
      pool,
      lambda path: path,
      chunk,
      thread_count=3,
      context="test supplement replenish",
      tracker=tracker,
      chunk_counter=0,
      pending_count=10,
      pending_tail=[],
      replenish_pending_tail_fn=_replenish,
  )

  def consumer():
    list(gen)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 3 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert replenish_calls, "expected mid-imap replenish when pending_tail empty"
  assert any(
      os.path.normpath("giant0") in call or "giant0" in call
      for call in replenish_calls
  ) or any(
      any("giant0" in os.path.basename(str(p)) for p in call)
      for call in replenish_calls
  )
  # Exclude must include the in-flight giant (normpath).
  assert any(
      os.path.normpath("giant0") in {os.path.normpath(str(p)) for p in call}
      for call in replenish_calls
  )
  submitted = list(pool.inflight.values())
  assert refill_a in submitted or refill_b in submitted
  for ar in list(pool.inflight):
    ar.finish()
  while pool.inflight and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  captured = capsys.readouterr().out
  assert "giant pool supplement replenish" in captured


def test_build_giant_supplement_pending_tail_uses_supplement_queue(monkeypatch, tmp_path):
  """Startup reservoir ceiling is supplement_queue (= queue * multiplier), not bare queue."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(st.cfg, "get_sync_ingest_giant_pool_supplement_enabled", lambda: True)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_queue_max_size", lambda: 3000)
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_queue_multiplier", lambda: 2,
  )
  assert st.cfg.get_sync_ingest_giant_pool_supplement_queue_size() == 6000
  base = []
  for i in range(50):
    p = tmp_path / f"base-{i:04d}"
    p.write_bytes(b"x")
    base.append(str(p))
  closed = [f"/virtual/closed/{i:05d}" for i in range(7000)]
  real_isfile = helpers.os.path.isfile

  def _isfile(path):
    s = str(path)
    if s.startswith("/virtual/closed/"):
      return True
    return real_isfile(path)

  monkeypatch.setattr(helpers.os.path, "isfile", _isfile)
  capped = helpers.build_giant_supplement_pending_tail(
      base,
      closed_paths=closed,
      supplement_queue=st.cfg.get_sync_ingest_giant_pool_supplement_queue_size(),
      log_fn=None,
  )
  assert len(capped) == 6000


def test_ingest_pool_tracker_batch_seen_excludes_redispatch(tmp_path, monkeypatch):
  monkeypatch.setattr(
      ingest_timeout_mod.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 10**9,
  )
  monkeypatch.setattr(
      ingest_timeout_mod.cfg,
      "get_sync_ingest_giant_pool_supplement_large_max_bytes",
      lambda: 10**9,
  )
  tail0 = str(tmp_path / "tail0")
  tail1 = str(tmp_path / "tail1")
  chunk0 = str(tmp_path / "chunk0")
  (tmp_path / "tail0").write_bytes(b"x" * 100)
  (tmp_path / "tail1").write_bytes(b"x" * 100)
  (tmp_path / "chunk0").write_bytes(b"x" * 100)
  tracker = st._IngestPoolInFlightTracker([chunk0])
  tracker.note_dispatched(tail0)
  tracker.complete(tail0)
  assert tracker.in_flight_count() == 1
  assert os.path.normpath(tail0) in tracker.batch_seen_paths()
  picked = list(
      ingest_timeout_mod.iter_giant_supplement_paths(
          [tail0, tail1],
          limit=2,
          exclude=tracker.batch_seen_paths(),
      ),
  )
  assert picked == [tail1]


def test_ingest_remaining_count_never_negative():
  assert st._ingest_remaining_count(100, 150) == 0
  assert st._ingest_remaining_count(100, 99) == 0
  assert st._ingest_remaining_count(100, 50) == 49


def test_log_ingest_file_outcome_supplement_annotation(capsys, monkeypatch):
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 100)
  outcome = st.IngestFileOutcome(
      path="/data/tail0",
      elapsed_s=0.3,
      ingest_ok=True,
      need_archival=False,
      outcome="db_skip",
      db_skip="head_tail",
  )
  st._log_ingest_file_outcome(outcome, remaining=0, supplement=True)
  out = capsys.readouterr().out
  assert "supplement=yes" in out
  assert "remaining=0" in out
  assert "-5 remaining" not in out


def test_giant_supplement_begin_log(monkeypatch, tmp_path, capsys):
  import threading
  import time

  from hpcperfstats.tests import test_multiprocessing_pool_health as mph_tests

  pool = mph_tests._ManualPool()
  tail_path = str(tmp_path / "tail0")
  (tmp_path / "tail0").write_bytes(b"x" * 100)
  chunk = ["giant0", "giant1"]
  _default_timeout_getters(monkeypatch)
  monkeypatch.setattr(st.cfg, "get_sync_ingest_giant_pool_supplement_enabled", lambda: True)
  monkeypatch.setattr(st, "_effective_ingest_imap_inflight_cap", lambda _tc, _pc: 4)
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_trigger_budget_s", lambda: 100.0,
  )
  monkeypatch.setattr(
      st.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 10**9,
  )
  monkeypatch.setattr(st.cfg, "get_sync_pool_poll_timeout_s", lambda: 0.01)
  monkeypatch.setattr(st.cfg, "get_sync_pool_stall_abort_after_timeouts", lambda: 100000)

  def _size_for(path):
    base = os.path.basename(str(path))
    if base.startswith("giant"):
      return _mib_bytes(2048)
    return 100

  _patch_stats_file_size_bytes(monkeypatch, _size_for)
  tracker = st._IngestPoolInFlightTracker(chunk)
  gen = st._imap_ingest_paths_batched(
      pool,
      lambda path: path,
      chunk,
      thread_count=4,
      context="test supplement begin",
      tracker=tracker,
      chunk_counter=0,
      pending_count=10,
      pending_tail=[tail_path],
  )

  def consumer():
    list(gen)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 3 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count >= 3
  for ar in list(pool.inflight):
    if pool.inflight.get(ar) not in chunk:
      ar.finish()
  while pool.inflight and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)

  captured = capsys.readouterr().out
  assert "giant pool supplement begin" in captured
  assert "pending_tail_n=" in captured
  assert "in_flight_giants=" in captured


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
  from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

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
