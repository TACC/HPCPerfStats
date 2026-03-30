import os
import threading
import time

import pytest

from hpcperfstats.file_locking import file_read_lock_wait, file_write_lock


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


def test_file_read_lock_cleans_up_lock_file(tmp_path):
  target = tmp_path / "data.txt"
  target.write_text("ok")
  lock_path = tmp_path / "data.txt.fnctl.lock"
  assert not lock_path.exists()

  with file_read_lock_wait(str(target), timeout_seconds=1):
    assert lock_path.exists()

  # Lock file should be removed once the read lock context exits.
  assert not lock_path.exists()
