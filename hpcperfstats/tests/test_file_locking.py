import errno
import os
import threading
import time

import pytest

from hpcperfstats.dbload.lib.file_locking import (
    cleanup_stale_fnctl_lock_sidecars,
    file_read_lock_wait,
    file_write_lock,
    _refresh_lock_sidecar_mtime,
    _try_open_write_lock_fd,
)


def test_file_write_lock_creates_corresponding_lock_file(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  lock_path = tmp_path / "data.txt.fnctl.lock"
  assert not lock_path.exists()

  with file_write_lock(str(target)):
    assert lock_path.exists()

  # Lock file should be cleaned up after the write lock is released.
  assert not lock_path.exists()


def test_file_write_lock_does_not_double_append_lock_suffix(tmp_path):
  """Passing an already lock-path should not create nested lock files."""
  lock_path = tmp_path / "data.txt.fnctl.lock"
  lock_path.write_text("ok")
  nested_lock_path = tmp_path / "data.txt.fnctl.lock.fnctl.lock"

  assert not nested_lock_path.exists()
  with file_write_lock(str(lock_path), timeout_seconds=1):
    assert lock_path.exists()
    assert not nested_lock_path.exists()


def test_file_write_lock_collapses_repeated_lock_suffixes(tmp_path):
  """Repeated '.fnctl.lock' suffixes should be collapsed to one."""
  repeated_lock_path = tmp_path / "data.txt.fnctl.lock.fnctl.lock.fnctl.lock"
  collapsed_lock_path = tmp_path / "data.txt.fnctl.lock"

  assert not collapsed_lock_path.exists()
  assert not repeated_lock_path.exists()

  with file_write_lock(str(repeated_lock_path), timeout_seconds=1):
    assert collapsed_lock_path.exists()
    assert not repeated_lock_path.exists()


def test_file_read_waits_for_writer_and_succeeds(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  held = threading.Event()
  release = threading.Event()

  def _hold_writer():
    with file_write_lock(str(target), timeout_seconds=1):
      held.set()
      release.wait(timeout=2)

  t = threading.Thread(target=_hold_writer, daemon=True)
  t.start()
  assert held.wait(timeout=1)

  start = time.time()
  release.set()
  with file_read_lock_wait(str(target), timeout_seconds=1):
    with open(target, "r") as fd:
      assert fd.read() == "ok"
  elapsed = time.time() - start
  assert elapsed >= 0
  t.join(timeout=1)


def test_file_read_times_out_when_writer_lock_held(tmp_path, capsys):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  held = threading.Event()
  release = threading.Event()

  def _hold_writer():
    with file_write_lock(str(target), timeout_seconds=1):
      held.set()
      release.wait(timeout=2)

  t = threading.Thread(target=_hold_writer, daemon=True)
  t.start()
  assert held.wait(timeout=1)

  with pytest.raises(TimeoutError):
    with file_read_lock_wait(str(target), timeout_seconds=0.2):
      pass
  captured = capsys.readouterr()
  assert "ERROR: Timed out waiting" in captured.out
  assert str(target) + ".fnctl.lock" in captured.out

  release.set()
  t.join(timeout=1)


def test_stale_lock_file_is_expired_before_acquire(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  lock_path = tmp_path / "data.txt.fnctl.lock"
  lock_path.write_text("")

  stale_ts = time.time() - (4 * 60 * 60 + 10)
  os.utime(lock_path, (stale_ts, stale_ts))

  with file_write_lock(str(target), timeout_seconds=1):
    assert lock_path.exists()


def test_cleanup_stale_fnctl_lock_sidecars_removes_old_orphan_sidecar(tmp_path):
  """Read paths leave sidecars; bulk cleanup removes stale uncontended ones."""
  target = tmp_path / "2026-04-02.tar"
  target.write_text("x")
  lock_path = tmp_path / "2026-04-02.tar.fnctl.lock"
  lock_path.write_text("")
  stale_ts = time.time() - (4 * 60 * 60 + 30)
  os.utime(lock_path, (stale_ts, stale_ts))
  assert cleanup_stale_fnctl_lock_sidecars(str(tmp_path)) == 1
  assert not lock_path.exists()


def test_cleanup_stale_fnctl_lock_sidecars_skips_recent_mtime(tmp_path):
  target = tmp_path / "recent.tar"
  target.write_text("x")
  lock_path = tmp_path / "recent.tar.fnctl.lock"
  lock_path.write_text("")
  assert cleanup_stale_fnctl_lock_sidecars(str(tmp_path)) == 0
  assert lock_path.exists()


def test_cleanup_stale_fnctl_lock_sidecars_does_not_remove_active_lock(tmp_path):
  target = tmp_path / "held.tar"
  target.write_text("x")
  lock_path = tmp_path / "held.tar.fnctl.lock"
  held = threading.Event()
  release = threading.Event()

  def _hold_writer():
    with file_write_lock(str(target), timeout_seconds=1):
      held.set()
      release.wait(timeout=2)

  t = threading.Thread(target=_hold_writer, daemon=True)
  t.start()
  assert held.wait(timeout=1)
  stale_ts = time.time() - (4 * 60 * 60 + 30)
  os.utime(lock_path, (stale_ts, stale_ts))
  assert cleanup_stale_fnctl_lock_sidecars(str(tmp_path)) == 0
  assert lock_path.exists()
  release.set()
  t.join(timeout=1)


def test_file_read_lock_does_not_delete_lock_file_on_release(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  lock_path = tmp_path / "data.txt.fnctl.lock"
  assert not lock_path.exists()

  with file_read_lock_wait(str(target), timeout_seconds=1):
    assert lock_path.exists()

  # Read locks must not unlink the sidecar lock file, or concurrent readers/writers
  # can race on a different inode.
  assert lock_path.exists()


def test_stale_lock_cleanup_does_not_remove_active_writer_lock(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  held = threading.Event()
  release = threading.Event()
  lock_path = tmp_path / "data.txt.fnctl.lock"

  def _hold_writer():
    with file_write_lock(str(target), timeout_seconds=1):
      held.set()
      release.wait(timeout=2)

  t = threading.Thread(target=_hold_writer, daemon=True)
  t.start()
  assert held.wait(timeout=1)
  assert lock_path.exists()

  # Force an old mtime while lock is active; stale cleanup must not remove it.
  stale_ts = time.time() - (4 * 60 * 60 + 10)
  os.utime(lock_path, (stale_ts, stale_ts))
  with pytest.raises(TimeoutError):
    with file_write_lock(str(target), timeout_seconds=0.1):
      pass

  assert lock_path.exists()
  release.set()
  t.join(timeout=1)


def test_file_locking_repeated_read_write_cycles(tmp_path):
  """Small soak: repeated write/read lock cycles should not deadlock or error."""
  target = tmp_path / "data.txt"
  target.write_text("ok")
  for _ in range(40):
    with file_write_lock(str(target), timeout_seconds=1):
      with open(target, "w", encoding="utf-8") as fd:
        fd.write("ok")
    with file_read_lock_wait(str(target), timeout_seconds=1):
      with open(target, "r", encoding="utf-8") as fd:
        assert fd.read() == "ok"


def test_file_write_lock_logs_sidecar_cleanup_failures(monkeypatch, tmp_path, capsys):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  real_remove = os.remove

  def _remove(path):
    if str(path).endswith(".fnctl.lock"):
      raise OSError("simulated unlink failure")
    return real_remove(path)

  monkeypatch.setattr(os, "remove", _remove)
  with file_write_lock(str(target), timeout_seconds=1):
    pass

  captured = capsys.readouterr()
  assert "WARNING: failed to remove lock sidecar" in captured.out


def test_file_write_lock_survives_path_utime_enoent(monkeypatch, tmp_path):
  """Path-based utime on sidecar must not abort write lock after acquisition."""
  target = tmp_path / "data.tar"
  target.write_text("x")
  real_utime = os.utime

  def _utime(path, times):
    if str(path).endswith(".fnctl.lock"):
      raise FileNotFoundError(
          errno.ENOENT, "No such file or directory", str(path)
      )
    return real_utime(path, times)

  monkeypatch.setattr(os, "utime", _utime)
  with file_write_lock(str(target), timeout_seconds=1):
    pass


def test_file_write_lock_refreshes_mtime_via_futime(monkeypatch, tmp_path):
  target = tmp_path / "data.tar"
  target.write_text("x")
  refresh_calls = []
  real_refresh = _refresh_lock_sidecar_mtime

  def _spy_refresh(lock_fd):
    refresh_calls.append(lock_fd.fileno())
    return real_refresh(lock_fd)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.file_locking._refresh_lock_sidecar_mtime",
      _spy_refresh,
  )
  with file_write_lock(str(target), timeout_seconds=1):
    pass

  assert len(refresh_calls) == 1


def test_refresh_lock_sidecar_mtime_survives_unlinked_path(tmp_path):
  """Held flock fd remains valid after sidecar path is unlinked."""
  target = tmp_path / "2026-04-02.tar"
  target.write_text("x")
  lock_path = tmp_path / "2026-04-02.tar.fnctl.lock"
  lock_path.write_text("")
  lock_fd = open(lock_path, "a+")
  os.remove(lock_path)
  from fcntl import LOCK_EX, flock

  flock(lock_fd, LOCK_EX)
  _refresh_lock_sidecar_mtime(lock_fd)
  lock_fd.close()


def test_file_write_lock_survives_stale_cleanup_during_acquire(tmp_path):
  """Concurrent stale sidecar cleanup must not break write lock acquisition."""
  target = tmp_path / "held.tar"
  target.write_text("x")
  lock_path = tmp_path / "held.tar.fnctl.lock"
  lock_path.write_text("")
  stale_ts = time.time() - (4 * 60 * 60 + 30)
  os.utime(lock_path, (stale_ts, stale_ts))
  errors = []

  def _cleanup_loop():
    for _ in range(50):
      cleanup_stale_fnctl_lock_sidecars(str(tmp_path))
      time.sleep(0.001)

  def _acquire_loop():
    try:
      for _ in range(50):
        with file_write_lock(str(target), timeout_seconds=1):
          time.sleep(0.001)
    except Exception as exc:
      errors.append(exc)

  t_cleanup = threading.Thread(target=_cleanup_loop, daemon=True)
  t_acquire = threading.Thread(target=_acquire_loop, daemon=True)
  t_cleanup.start()
  t_acquire.start()
  t_cleanup.join(timeout=5)
  t_acquire.join(timeout=5)
  assert not errors


def test_try_open_write_lock_fd_opens_and_locks(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  lock_fd = _try_open_write_lock_fd(str(target))
  try:
    assert lock_fd.fileno() >= 0
  finally:
    lock_fd.close()
