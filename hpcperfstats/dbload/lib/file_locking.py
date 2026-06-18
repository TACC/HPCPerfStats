"""File lock helpers for coordinated readers/writers.

Uses fcntl advisory locks with a sidecar lock file per target file:
`<path>.fnctl.lock`.
"""

import errno
import os
import time
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock

LOCK_EXPIRY_SECONDS = 4 * 60 * 60
READ_WAIT_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 0.1
LOCK_SUFFIX = ".fnctl.lock"


def _lock_path(target_path):
  """Return the sidecar lock path for a target.

  This function is intentionally idempotent: callers may (incorrectly) pass
  an already-lock-path, and we must not keep appending lock suffixes
  repeatedly (which can lead to Errno 36: file name too long).
  """
  # Collapse repeated trailing occurrences, e.g.:
  #   /tmp/x.fnctl.lock.fnctl.lock  -> /tmp/x.fnctl.lock
  base = target_path
  while base.endswith(LOCK_SUFFIX):
    base = base[: -len(LOCK_SUFFIX)]
  return base + LOCK_SUFFIX


def _print_read_lock_timeout(lock_path, timeout_seconds):
  print(
      "ERROR: Timed out waiting %.1fs for read lock: %s"
      % (timeout_seconds, lock_path)
  )


def _open_lock_file(target_path):
  lock_path = _lock_path(target_path)
  parent_dir = os.path.dirname(lock_path) or "."
  if not os.path.exists(parent_dir):
    raise FileNotFoundError("Parent directory does not exist: %s" % parent_dir)
  return open(lock_path, "a+")


def _maybe_reset_stale_lock_file(target_path, now, expiry_seconds):
  lock_path = _lock_path(target_path)
  lock_fd = None
  try:
    if not os.path.exists(lock_path):
      return False
    age_seconds = now - os.path.getmtime(lock_path)
    if age_seconds > expiry_seconds:
      # Only remove stale lock files when we can prove no active holder exists.
      lock_fd = open(lock_path, "a+")
      flock(lock_fd, LOCK_EX | LOCK_NB)
      flock(lock_fd, LOCK_UN)
      lock_fd.close()
      lock_fd = None
      os.remove(lock_path)
      return True
  except OSError:
    # Best effort only; lock acquisition still provides correctness.
    return False
  finally:
    if lock_fd is not None:
      try:
        lock_fd.close()
      except OSError:
        pass
  return False


def _target_path_from_lock_sidecar(lock_path):
  """Map a lock sidecar path back to the locked target (collapse repeated suffixes)."""
  base = lock_path
  while base.endswith(LOCK_SUFFIX):
    base = base[: -len(LOCK_SUFFIX)]
  return base


def cleanup_stale_fnctl_lock_sidecars(
    directory,
    *,
    expiry_seconds=LOCK_EXPIRY_SECONDS,
    now=None,
):
  """Remove stale ``*.fnctl.lock`` sidecars under ``directory`` when safe.

  Read paths do not unlink the sidecar file, so empty lock files can linger.
  This walks the tree and, for each ``*.fnctl.lock`` older than ``expiry_seconds``,
  reuses the same safety check as :func:`_maybe_reset_stale_lock_file`: attempt a
  non-blocking exclusive flock on the sidecar; remove only if uncontended.

  Returns the number of sidecar files removed.
  """
  if not directory or not os.path.isdir(directory):
    return 0
  if now is None:
    now = time.time()
  removed = 0
  for root, _dirs, files in os.walk(directory):
    for name in files:
      if not name.endswith(LOCK_SUFFIX):
        continue
      lock_path = os.path.join(root, name)
      target_path = _target_path_from_lock_sidecar(lock_path)
      if not target_path or target_path == lock_path:
        continue
      if _maybe_reset_stale_lock_file(target_path, now, expiry_seconds):
        removed += 1
  return removed


@contextmanager
def file_write_lock(target_path,
                    timeout_seconds=READ_WAIT_TIMEOUT_SECONDS,
                    expiry_seconds=LOCK_EXPIRY_SECONDS):
  """Acquire an exclusive file lock for writes.

  Raises TimeoutError after `timeout_seconds` if the lock cannot be acquired.
  """
  start = time.time()
  lock_fd = None
  while True:
    now = time.time()
    _maybe_reset_stale_lock_file(target_path, now, expiry_seconds)
    try:
      lock_fd = _open_lock_file(target_path)
      flock(lock_fd, LOCK_EX | LOCK_NB)
      break
    except OSError as exc:
      if lock_fd is not None:
        try:
          lock_fd.close()
        except OSError:
          pass
      if exc.errno not in (errno.EACCES, errno.EAGAIN):
        raise
      if (now - start) >= timeout_seconds:
        raise TimeoutError(
            "Timed out waiting for write lock: %s" % target_path
        ) from exc
      time.sleep(POLL_INTERVAL_SECONDS)

  try:
    os.utime(_lock_path(target_path), None)
    yield
  finally:
    lock_path = _lock_path(target_path)
    try:
      flock(lock_fd, LOCK_UN)
    finally:
      try:
        lock_fd.close()
      finally:
        try:
          os.remove(lock_path)
        except FileNotFoundError:
          pass
        except OSError:
          # Best-effort cleanup; failure to remove the lock file should not
          # break callers once the advisory lock itself is released.
          print("WARNING: failed to remove lock sidecar: %s" % lock_path)


@contextmanager
def file_read_lock_wait(target_path,
                        timeout_seconds=READ_WAIT_TIMEOUT_SECONDS,
                        expiry_seconds=LOCK_EXPIRY_SECONDS):
  """Acquire a shared lock, waiting for active writer lock release.

  This acts as "check for lock and wait (up to timeout)" before reads.
  """
  start = time.time()
  lock_fd = None
  while True:
    now = time.time()
    _maybe_reset_stale_lock_file(target_path, now, expiry_seconds)
    try:
      lock_fd = _open_lock_file(target_path)
      flock(lock_fd, LOCK_SH | LOCK_NB)
      break
    except OSError as exc:
      if lock_fd is not None:
        try:
          lock_fd.close()
        except OSError:
          pass
      if exc.errno not in (errno.EACCES, errno.EAGAIN):
        raise
      if (now - start) >= timeout_seconds:
        lock_path = _lock_path(target_path)
        _print_read_lock_timeout(lock_path, timeout_seconds)
        raise TimeoutError(
            "Timed out waiting for read lock: %s" % lock_path
        ) from exc
      try:
        from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
            _raise_if_ingest_deadline_exceeded,
        )

        _raise_if_ingest_deadline_exceeded()
      except ImportError:
        pass
      except Exception:
        raise
      time.sleep(POLL_INTERVAL_SECONDS)

  try:
    yield
  finally:
    try:
      flock(lock_fd, LOCK_UN)
    finally:
      try:
        lock_fd.close()
      except OSError:
        pass
