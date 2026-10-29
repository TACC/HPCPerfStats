"""The exclusive tar lock must stay exclusive while sidecars are unlinked.

hpcperfstats04 2026-10-28: ``file_write_lock`` unlocked the sidecar before it
unlinked it, and read-lock callers unlinked it unconditionally. A waiter could
lock an inode that was about to be deleted while the next contender recreated
the file and locked that too, so an append ran while the seal "held" the lock
and zstd read a growing tar (``Incomplete read``, exit 27).
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from hpcperfstats.dbload.lib.file_locking import (
  file_read_lock_wait,
  file_write_lock,
  try_file_write_lock,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
  _remove_read_lock_sidecar,
)


def _target(tmp_path):
  target = tmp_path / "2026-08-22.tar"
  target.write_bytes(b"x")
  return str(target)


def test_contending_writers_never_overlap(tmp_path):
  target = _target(tmp_path)
  state = {"inside": 0, "max": 0}
  guard = threading.Lock()

  def worker():
    for _ in range(100):
      with file_write_lock(target, timeout_seconds=120):
        with guard:
          state["inside"] += 1
          state["max"] = max(state["max"], state["inside"])
        time.sleep(0.001)
        with guard:
          state["inside"] -= 1

  threads = [threading.Thread(target=worker) for _ in range(8)]
  [t.start() for t in threads]
  [t.join() for t in threads]
  assert state["max"] == 1
  assert not os.path.exists(target + ".fnctl.lock")


def test_writers_and_readers_never_overlap(tmp_path):
  target = _target(tmp_path)
  state = {"writers": 0, "readers": 0, "bad": 0}
  guard = threading.Lock()

  def writer():
    for _ in range(60):
      with file_write_lock(target, timeout_seconds=120):
        with guard:
          state["writers"] += 1
          state["bad"] += state["writers"] > 1 or state["readers"] > 0
        time.sleep(0.001)
        with guard:
          state["writers"] -= 1

  def reader():
    for _ in range(60):
      with file_read_lock_wait(target, timeout_seconds=120):
        with guard:
          state["readers"] += 1
          state["bad"] += state["writers"] > 0
        time.sleep(0.001)
        with guard:
          state["readers"] -= 1
      _remove_read_lock_sidecar(target)

  threads = [threading.Thread(target=writer) for _ in range(3)]
  threads += [threading.Thread(target=reader) for _ in range(5)]
  [t.start() for t in threads]
  [t.join() for t in threads]
  assert state["bad"] == 0


def test_reader_cleanup_cannot_unlock_a_held_write_lock(tmp_path):
  target = _target(tmp_path)
  with file_write_lock(target):
    _remove_read_lock_sidecar(target)
    with pytest.raises(TimeoutError):
      with try_file_write_lock(target):
        pass


def test_reader_cleanup_still_removes_an_unheld_sidecar(tmp_path):
  target = _target(tmp_path)
  with file_read_lock_wait(target):
    pass
  assert os.path.exists(target + ".fnctl.lock")
  _remove_read_lock_sidecar(target)
  assert not os.path.exists(target + ".fnctl.lock")


def test_reader_cleanup_keeps_the_sidecar_while_a_reader_holds_it(tmp_path):
  target = _target(tmp_path)
  with file_read_lock_wait(target):
    _remove_read_lock_sidecar(target)
    assert os.path.exists(target + ".fnctl.lock")
