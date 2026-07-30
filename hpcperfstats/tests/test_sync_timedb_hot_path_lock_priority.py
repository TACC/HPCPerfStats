"""Regression tests for ingest-wins janitor lock priority."""
from __future__ import annotations

import io
import os
import tarfile
import threading
import time
from unittest.mock import MagicMock

import pytest

from hpcperfstats.dbload.lib.file_locking import (
    file_read_lock_wait,
    try_file_write_lock,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import ArchiveJanitor
from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
    DayCloseYieldError,
    JanitorDeferTracker,
    clear_day_close_yield,
    daily_tar_janitor_mutation_should_defer,
    day_close_yield_event_set,
    day_close_yield_requested,
    signal_day_close_yield,
)
from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers
from hpcperfstats.dbload.lib import sync_timedb_archive_members_redis as members_redis
from hpcperfstats.dbload.lib import zstd_cli


def _write_tar_with_dupes(tar_path):
  with tarfile.open(tar_path, "w") as tf:
    data_a = io.BytesIO(b"a" * 10)
    info_a = tarfile.TarInfo(name="member.dat")
    info_a.size = 10
    tf.addfile(info_a, data_a)
    data_b = io.BytesIO(b"b" * 20)
    info_b = tarfile.TarInfo(name="member.dat")
    info_b.size = 20
    tf.addfile(info_b, data_b)


def _make_janitor(**kwargs):
  defaults = {
      "archive_data_dir": kwargs.pop("archive_data_dir", "/tmp/archive"),
      "host_name_ext": ".hpc",
      "tgz_archive_dir": kwargs.pop("tgz_archive_dir", "/tmp/daily"),
      "local_tz": __import__("datetime").timezone.utc,
      "log_fn": MagicMock(),
      "get_disqualified_daily_tars": lambda: set(),
      "get_pending_stats_count": lambda: 0,
      "get_idle_seconds": lambda: 0.0,
  }
  defaults.update(kwargs)
  janitor = ArchiveJanitor(**defaults)
  janitor._persist_hints = MagicMock()
  return janitor


def test_janitor_defer_tracker_write_lock_sticky_backoff():
  """write_lock_contended sets sticky backoff so ticks skip re-pop thrash."""
  tracker = JanitorDeferTracker()
  tar = "/tmp/daily/2026-06-08.tar"
  tracker.record_defer(tar, reason="write_lock_contended")
  entry = tracker._by_tar[os.path.normpath(tar)]
  assert float(entry["write_lock_until"]) > time.time()
  assert tracker.write_lock_backoff_active(tar) is True
  assert tracker.write_lock_backoff_skip_tars([tar]) == {os.path.normpath(tar)}
  # Expired window → not active.
  assert tracker.write_lock_backoff_active(
      tar, now=float(entry["write_lock_until"]) + 1.0,
  ) is False
  # Other reasons do not set write_lock sticky window.
  tracker2 = JanitorDeferTracker()
  tracker2.record_defer(tar, reason="populate_active")
  assert tracker2.write_lock_backoff_active(tar) is False


def test_close_one_day_preflight_defers_write_lock_before_pre_seal(
    monkeypatch, tmp_path,
):
  """Contended write lock must defer before pre_seal / dup scan (Track A)."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-08.tar"))
  _write_tar_with_dupes(tar_path)
  log_fn = MagicMock()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir), log_fn=log_fn)
  pre_seal_n = {"n": 0}
  dup_n = {"n": 0}

  class _Coord:
    def pre_seal_verification_complete(self, _tar):
      return False

    def run_pre_seal_verify_sync(self, _tar):
      pre_seal_n["n"] += 1
      return True

    def should_handoff_before_seal(self, _tar):
      return False

  janitor.day_raw_removal_coordinator = _Coord()

  def _count_dup(_p):
    dup_n["n"] += 1
    return True

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_janitor"
      ".tar_has_duplicate_file_members",
      _count_dup,
  )

  @__import__("contextlib").contextmanager
  def _contended(_path):
    raise TimeoutError("contended")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.file_locking.try_file_write_lock",
      _contended,
  )
  result = janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert result is False
  assert pre_seal_n["n"] == 0
  assert dup_n["n"] == 0
  defer_logs = [
      str(c)
      for c in log_fn.call_args_list
      if c and "day_close defer" in str(c) and "preflight" in str(c)
  ]
  assert defer_logs
  assert "write_lock_contended" in defer_logs[-1]
  assert janitor._defer_tracker.write_lock_backoff_active(tar_path) is True


def test_fill_skips_write_lock_backoff_tar_without_starting_day(monkeypatch, tmp_path):
  """Sticky backoff: heap tar is not submitted while write_lock_until active."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-08.tar"))
  open(tar_path, "wb").close()
  log_fn = MagicMock()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir), log_fn=log_fn)
  janitor._defer_tracker.record_defer(tar_path, reason="write_lock_contended")
  assert janitor._defer_tracker.write_lock_backoff_active(tar_path)
  janitor._enqueue_day_close(tar_path, persist=False)
  monkeypatch.setattr(janitor, "get_day_close_allowed", lambda: True)
  monkeypatch.setattr(janitor, "get_startup_ingest_gate_cleared", lambda: True)
  monkeypatch.setattr(janitor, "_discover_and_enqueue_ready_day_close", lambda **_k: None)
  monkeypatch.setattr(janitor, "_run_tick_lock_cleanup", lambda: 0)
  monkeypatch.setattr(janitor, "_effective_tick_budget", lambda: 5.0)
  submit_n = {"n": 0}

  def _no_submit(*_a, **_k):
    submit_n["n"] += 1
    raise AssertionError("must not submit during write_lock backoff")

  monkeypatch.setattr(janitor, "_submit_day_close_debt", _no_submit)
  janitor._run_tick_body()
  assert submit_n["n"] == 0
  tick_logs = [
      str(c)
      for c in log_fn.call_args_list
      if c and "Archive janitor tick done" in str(c)
  ]
  assert tick_logs
  assert "days_started=0" in tick_logs[-1]


def test_try_file_write_lock_raises_when_read_lock_held(tmp_path):
  target = tmp_path / "2026-06-05.tar"
  target.write_bytes(b"x")
  release = threading.Event()

  def _hold_reader():
    with file_read_lock_wait(str(target), timeout_seconds=2):
      release.wait(timeout=5)

  t = threading.Thread(target=_hold_reader, daemon=True)
  t.start()
  time.sleep(0.05)
  with pytest.raises(TimeoutError):
    with try_file_write_lock(str(target)):
      pass
  release.set()
  t.join(timeout=2)


def test_janitor_defers_dedupe_when_populate_active(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-05.tar"))
  _write_tar_with_dupes(tar_path)
  log_fn = MagicMock()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir), log_fn=log_fn)
  dedupe_called = {"n": 0}

  def _dedupe_stub(*_a, **_k):
    dedupe_called["n"] += 1
    return True

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_janitor"
      ".tar_has_duplicate_file_members",
      lambda _p: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_janitor"
      ".dedupe_tar_keep_largest_file_per_member",
      _dedupe_stub,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".archive_members_populate_shows_progress_for_day",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(janitor, "_seal_one_day", lambda *_a, **_k: True)

  result = janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert result is False
  assert dedupe_called["n"] == 0
  defer_logs = [
      str(c)
      for c in log_fn.call_args_list
      if c and "day_close defer" in str(c)
  ]
  assert defer_logs
  assert "populate_active" in defer_logs[-1]


def test_dedupe_yields_mid_mutation_before_replace(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-05.tar"))
  _write_tar_with_dupes(tar_path)
  before = open(tar_path, "rb").read()
  def _yield_immediately(*_a, **_k):
    raise DayCloseYieldError(tar_path, phase="dedupe", reason="ingest_tar_hot")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation"
      ".check_day_close_yield_or_continue",
      _yield_immediately,
  )
  with pytest.raises(DayCloseYieldError):
    helpers.dedupe_tar_keep_largest_file_per_member(
        tar_path,
        tgz_archive_dir=str(daily_dir),
    )
  assert open(tar_path, "rb").read() == before
  assert not os.path.isfile("%s.dedupe.tmp" % tar_path)


def test_janitor_defer_cap_still_defers_when_populate_active(monkeypatch, tmp_path):
  """F5: defer_cap_exceeded must not skip hot/populate checks."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-05.tar"))
  tracker = JanitorDeferTracker()
  for _ in range(3):
    tracker.record_defer(tar_path)
  assert tracker.defer_cap_exceeded(tar_path) is True
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".archive_members_populate_shows_progress_for_day",
      lambda *_a, **_k: True,
  )
  defer, reason = daily_tar_janitor_mutation_should_defer(
      tar_path,
      tgz_archive_dir=str(daily_dir),
      disqualified_daily_tars=set(),
      defer_cap_exceeded=True,
  )
  assert defer is True
  assert reason == "populate_active"


def test_janitor_defer_cap_allows_proceed_when_hot_clear(monkeypatch, tmp_path):
  """F5: after cap, proceed only when write-lock/hot/populate/restore are clear."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-05.tar"))
  open(tar_path, "wb").close()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".archive_members_populate_shows_progress_for_day",
      lambda *_a, **_k: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".ingest_tar_hot_for_day",
      lambda *_a, **_k: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".daily_tar_restore_in_progress_for_day",
      lambda *_a, **_k: False,
  )
  defer, reason = daily_tar_janitor_mutation_should_defer(
      tar_path,
      tgz_archive_dir=str(daily_dir),
      disqualified_daily_tars=set(),
      defer_cap_exceeded=True,
  )
  assert defer is False
  assert reason == ""


def test_sticky_yield_event_cleared_when_hot_inactive(monkeypatch, tmp_path):
  """F11c: sticky yield Event alone must not defer after hot/populate clear."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-06.tar"))
  open(tar_path, "wb").close()
  signal_day_close_yield(tar_path, reason="test_sticky")
  assert day_close_yield_event_set(tar_path) is True
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".archive_members_populate_shows_progress_for_day",
      lambda *_a, **_k: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".ingest_tar_hot_for_day",
      lambda *_a, **_k: False,
  )
  requested, reason = day_close_yield_requested(
      tar_path,
      tgz_archive_dir=str(daily_dir),
  )
  assert requested is False
  assert reason == ""
  assert day_close_yield_event_set(tar_path) is False
  clear_day_close_yield(tar_path)


def test_daily_tar_restore_redis_signal_missing_tar(monkeypatch, tmp_path):
  fake = {}
  monkeypatch.setattr(
      members_redis,
      "archive_members_redis_enabled",
      lambda: True,
  )

  class _Client:
    def set(self, k, v, ex=None, nx=False):
      if nx and k in fake:
        return False
      fake[k] = v
      return True

    def delete(self, k):
      fake.pop(k, None)

    def exists(self, k):
      return k in fake

    def get(self, k):
      return fake.get(k)

    def eval(self, script, _n, key, *argv):
      if not argv:
        return 0
      if fake.get(key) != argv[0]:
        return 0
      if "expire" in script:
        return 1
      fake.pop(key, None)
      return 1

  monkeypatch.setattr(
      members_redis,
      "get_archive_members_redis_client",
      lambda required=False: _Client(),
  )
  monkeypatch.setattr(members_redis, "_populate_max_seconds", lambda: 60)
  monkeypatch.setattr(
      members_redis, "_daily_tar_restore_lease_seconds", lambda: 60,
  )
  token = members_redis.set_daily_tar_restore_in_progress(
      "2026-06-05",
      reason="missing_tar",
      caller="test",
  )
  assert token
  assert members_redis.daily_tar_restore_in_progress_for_day("2026-06-05")
  members_redis.clear_daily_tar_restore_in_progress(
      "2026-06-05",
      token=token,
      ok=True,
      reason="missing_tar",
  )
  assert not members_redis.daily_tar_restore_in_progress_for_day("2026-06-05")


def test_janitor_defers_dedupe_while_daily_tar_restore_in_progress(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-05.tar"))
  _write_tar_with_dupes(tar_path)
  log_fn = MagicMock()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir), log_fn=log_fn)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_janitor"
      ".tar_has_duplicate_file_members",
      lambda _p: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".daily_tar_restore_in_progress_for_day",
      lambda _d: True,
  )
  monkeypatch.setattr(janitor, "_seal_one_day", lambda *_a, **_k: True)
  result = janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert result is False
  defer_logs = [str(c) for c in log_fn.call_args_list if "daily_tar_restore" in str(c)]
  assert defer_logs


def test_set_and_clear_ingest_tar_hot(monkeypatch):
  fake = {}
  monkeypatch.setattr(members_redis, "archive_members_redis_enabled", lambda: True)
  monkeypatch.setattr(
      members_redis,
      "get_archive_members_redis_client",
      lambda required=False: type(
          "C",
          (),
          {
              "set": lambda self, k, v, ex=None: fake.__setitem__(k, v),
              "delete": lambda self, k: fake.pop(k, None),
              "exists": lambda self, k: k in fake,
          },
      )(),
  )
  monkeypatch.setattr(members_redis, "_populate_max_seconds", lambda: 60)
  members_redis.set_ingest_tar_hot("2026-06-05", reason="populate_wait")
  assert members_redis.ingest_tar_hot_for_day("2026-06-05")
  members_redis.clear_ingest_tar_hot("2026-06-05")
  assert not members_redis.ingest_tar_hot_for_day("2026-06-05")


def test_signal_day_close_yield_sets_event():
  tar = "/tmp/daily/2026-06-05.tar"
  signal_day_close_yield(tar, reason="chunk_prewarm", log_fn=None)
  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      day_close_yield_event_set,
  )
  assert day_close_yield_event_set(tar)


def test_seal_yields_during_zstd_subprocess_poll(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path_obj = tmp_path / "2026-06-05.tar"
  tar_path_obj.write_bytes(b"payload")
  zst_path = str(tmp_path / "2026-06-05.tar.zst")

  class _SlowProc:
    def __init__(self):
      self._done = False
      self.returncode = None
      self.stderr = io.BytesIO()
      self.stdout = None

    def poll(self):
      return 0 if self._done else None

    def terminate(self):
      self._done = True
      self.returncode = -15

    def wait(self, timeout=None):
      self._done = True
      self.returncode = -15

  proc = _SlowProc()

  def _fake_popen(*_a, **_k):
    return proc

  def _yield_immediately(*_a, **_k):
    raise DayCloseYieldError(str(tar_path_obj), phase="seal", reason="ingest_tar_hot")

  monkeypatch.setattr(zstd_cli, "_popen_zstd", _fake_popen)
  monkeypatch.setattr(zstd_cli, "zstd_executable", lambda: "zstd")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation"
      ".check_day_close_yield_or_continue",
      _yield_immediately,
  )
  with pytest.raises(DayCloseYieldError):
    zstd_cli.zstd_compress_tar_to_file(
        str(tar_path_obj),
        zst_path,
        1,
        3,
        tgz_archive_dir=str(daily_dir),
    )
  assert not os.path.isfile(zst_path)


def test_pre_seal_verify_defers_on_restore_conflict(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-02.tar"))
  logs = []
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=lambda msg, **_k: logs.append(str(msg)),
  )

  class _Coord:
    def pre_seal_verification_complete(self, _tar):
      return False

    def run_pre_seal_verify_sync(self, _tar):
      return False

  janitor.day_raw_removal_coordinator = _Coord()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".daily_tar_restore_in_progress_for_day",
      lambda _d: True,
  )
  monkeypatch.setattr(janitor, "_check_day_close_defer", lambda *a, **k: (False, ""))
  monkeypatch.setattr(janitor, "_day_phase_at_least", lambda *a, **k: False)
  enqueued = []
  monkeypatch.setattr(
      janitor,
      "_enqueue_day_close",
      lambda tar, persist=True: enqueued.append(os.path.normpath(tar)) or True,
  )
  monkeypatch.setattr(janitor, "_persist_hints", lambda: None)
  result = janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert result is False
  assert enqueued == [tar_path]
  assert any(
      "defer" in line and "daily_tar_restore" in line for line in logs
  ), logs
