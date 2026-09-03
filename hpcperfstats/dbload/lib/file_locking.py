"""
File lock helpers for coordinated readers/writers.

Uses fcntl advisory locks with a sidecar lock file per target file:
`<path>.fnctl.lock`.

Attributes:
  LOCK_EXPIRY_SECONDS: Attribute.
  LOCK_SUFFIX: Attribute.
  POLL_INTERVAL_SECONDS: Attribute.
  READ_WAIT_TIMEOUT_SECONDS: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import errno
import os
import time
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock

LOCK_EXPIRY_SECONDS = 4 * 60 * 60
READ_WAIT_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 0.1
LOCK_SUFFIX = ".fnctl.lock"


def _lock_path(target_path: str) -> Any:
  """
  Return the sidecar lock path for a target.
  
  This function is intentionally idempotent: callers may (incorrectly) pass
  an already-lock-path, and we must not keep appending lock suffixes
  repeatedly (which can lead to Errno 36: file name too long).
  
  Args:
    target_path (str): String for target path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _lock_path("x")  # doctest: +SKIP
  """
  # Collapse repeated trailing occurrences, e.g.:
  #   /tmp/x.fnctl.lock.fnctl.lock  -> /tmp/x.fnctl.lock
  base = target_path
  while base.endswith(LOCK_SUFFIX):
    base = base[: -len(LOCK_SUFFIX)]
  return base + LOCK_SUFFIX


def _print_read_lock_timeout(lock_path: str, timeout_seconds: int) -> None:
  """
  Internal helper to print the read lock timeout.
  
  Args:
    lock_path (str): String for lock path.
    timeout_seconds (int): Integer value for timeout seconds.
  
  Returns:
    None
  
  Examples:
    >>> _print_read_lock_timeout("x", 0)  # doctest: +SKIP
  """
  print(
      "ERROR: Timed out waiting %.1fs for read lock: %s"
      % (timeout_seconds, lock_path)
  )


def _open_lock_file(target_path: str) -> Any:
  """
  Internal helper to open the lock file.
  
  Args:
    target_path (str): String for target path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    FileNotFoundError: Raised when ``_open_lock_file`` hits a
    ``FileNotFoundError`` failure path.
  
  Examples:
    >>> _open_lock_file("x")  # doctest: +SKIP
  """
  lock_path = _lock_path(target_path)
  parent_dir = os.path.dirname(lock_path) or "."
  if not os.path.exists(parent_dir):
    raise FileNotFoundError("Parent directory does not exist: %s" % parent_dir)
  return open(lock_path, "a+")


def _try_open_write_lock_fd(target_path: str) -> Any:
  """
  Open the sidecar and attempt a non-blocking exclusive flock in one step.
  
  Args:
    target_path (str): String for target path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_try_open_write_lock_fd`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> _try_open_write_lock_fd("x")  # doctest: +SKIP
  """
  lock_fd = _open_lock_file(target_path)
  try:
    flock(lock_fd, LOCK_EX | LOCK_NB)
  except OSError:
    try:
      lock_fd.close()
    except OSError:
      pass
    raise
  return lock_fd


def _refresh_lock_sidecar_mtime(lock_fd: Any) -> None:
  """
  Refresh sidecar mtime on the held fd (safe if path was unlinked).
  
  Args:
    lock_fd (Any): Lock fd passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _refresh_lock_sidecar_mtime(None)  # doctest: +SKIP
  """
  if hasattr(os, "futime"):
    os.futime(lock_fd.fileno(), None)
    return
  lock_path = lock_fd.name
  if lock_path:
    try:
      os.utime(lock_path, None)
    except FileNotFoundError:
      pass


def _maybe_reset_stale_lock_file(
  target_path: str,
  now: Any,
  expiry_seconds: int,
) -> Any:
  """
  Remove a lock sidecar when uncontended and optionally older than.
  
    ``expiry_seconds``.
  
  When ``expiry_seconds <= 0``, skip the mtime age gate (post-crash orphan
    cleanup).
  Removal still requires a successful non-blocking exclusive flock probe so
    active
  holders are never cleared.
  
  Args:
    target_path (str): String for target path.
    now (Any): Now passed to this helper.
    expiry_seconds (int): Integer value for expiry seconds.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _maybe_reset_stale_lock_file("x", None, 0)  # doctest: +SKIP
  """
  lock_path = _lock_path(target_path)
  lock_fd = None
  try:
    if not os.path.exists(lock_path):
      return False
    if expiry_seconds > 0:
      age_seconds = now - os.path.getmtime(lock_path)
      if age_seconds <= expiry_seconds:
        return False
    # Only remove when we can prove no active holder exists.
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


def _target_path_from_lock_sidecar(lock_path: str) -> Any:
  """
  Map a lock sidecar path back to the locked target (collapse repeated.
  
    suffixes).
  
  Args:
    lock_path (str): String for lock path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _target_path_from_lock_sidecar("x")  # doctest: +SKIP
  """
  base = lock_path
  while base.endswith(LOCK_SUFFIX):
    base = base[: -len(LOCK_SUFFIX)]
  return base


def cleanup_stale_fnctl_lock_sidecars(
  directory: Any,
  *,
  expiry_seconds: int = LOCK_EXPIRY_SECONDS,
  now: Any | None = None,
) -> Any:
  """
  Remove stale ``*.fnctl.lock`` sidecars under ``directory`` when safe.
  
  Read paths do not unlink the sidecar file, so empty lock files can linger.
  This walks the tree and, for each ``*.fnctl.lock`` older than
    ``expiry_seconds``,
  reuses the same safety check as :func:`_maybe_reset_stale_lock_file`: attempt
    a
  non-blocking exclusive flock on the sidecar; remove only if uncontended.
  
  Returns the number of sidecar files removed.
  
  Args:
    directory (Any): Directory passed to this helper.
    expiry_seconds (int): Integer value for expiry seconds.
    now (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cleanup_stale_fnctl_lock_sidecars(None, 0, None)  # doctest: +SKIP
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


def cleanup_orphan_fnctl_lock_sidecars(
  directory: Any,
  *,
  now: Any | None = None,
) -> Any:
  """
  Remove uncontended ``*.fnctl.lock`` sidecars regardless of mtime.
  
  Read-lock paths leave sidecars behind; after a crash the sidecar can linger
    with
  a recent mtime while no process holds the flock. Use at startup and on
    manifest
  trees so day-raw-removal deletes do not sit in the 60s write-lock wait loop.
  
  Args:
    directory (Any): Directory passed to this helper.
    now (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cleanup_orphan_fnctl_lock_sidecars(None, None)  # doctest: +SKIP
  """
  return cleanup_stale_fnctl_lock_sidecars(
      directory,
      expiry_seconds=0,
      now=now,
  )


def cleanup_orphan_fnctl_lock_sidecars_for_targets(
  target_paths: Any,
  *,
  now: Any | None = None,
) -> Any:
  """
  Remove uncontended lock sidecars for specific targets (no directory walk).
  
  Used for debt-day-targeted daily ``.tar`` / sealed sibling cleanup on janitor
  ticks. Never unlinks while an exclusive flock probe fails (live SH/EX holder).
  
  Args:
    target_paths (Any): Iterable of filesystem paths as strings.
    now (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cleanup_orphan_fnctl_lock_sidecars_for_targets(None, None)
  """
  if now is None:
    now = time.time()
  removed = 0
  seen = set()
  for path in target_paths or ():
    if not path:
      continue
    norm = os.path.normpath(path)
    if norm in seen:
      continue
    seen.add(norm)
    if _maybe_reset_stale_lock_file(norm, now, 0):
      removed += 1
  return removed


@contextmanager
def file_write_lock(
  target_path: str,
  timeout_seconds: int = READ_WAIT_TIMEOUT_SECONDS,
  expiry_seconds: int = LOCK_EXPIRY_SECONDS,
  *,
  already_held: bool = False,
) -> Iterator[Any]:
  """
  Acquire an exclusive file lock for writes.

  When ``already_held`` is True the caller already owns the exclusive flock
  and this helper yields without opening a second sidecar. Nested acquire on
  the same path deadlocks because the lock is non-reentrant.

  Raises TimeoutError after `timeout_seconds` if the lock cannot be acquired.

  Args:
    target_path (str): Filesystem path whose sidecar flock is acquired.
    timeout_seconds (int): Seconds to wait for a contended lock.
    expiry_seconds (int): Stale-sidecar expiry used by the reset helper.
    already_held (bool): Skip acquire when the caller already holds the lock.

  Yields:
    None: Control returns to the caller while the lock is held (or skipped).

  Raises:
    TimeoutError: When the exclusive flock cannot be acquired in time.
    OSError: When opening the sidecar fails for a non-contention reason.
    Exception: When a non-timeout error is re-raised from the acquire loop.

  Examples:
    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...   p = str(Path(d) / "x.tar")
    ...   Path(p).write_bytes(b"x")
    ...   with file_write_lock(p, already_held=True):
    ...     True
    True
  """
  if already_held:
    yield
    return
  start = time.time()
  lock_fd = None
  while True:
    now = time.time()
    _maybe_reset_stale_lock_file(target_path, now, 0)
    try:
      lock_fd = _try_open_write_lock_fd(target_path)
      break
    except OSError as exc:
      lock_fd = None
      if exc.errno not in (errno.EACCES, errno.EAGAIN):
        raise
      if (now - start) >= timeout_seconds:
        raise TimeoutError(
            "Timed out waiting for write lock: %s" % target_path
        ) from exc
      time.sleep(POLL_INTERVAL_SECONDS)

  try:
    _refresh_lock_sidecar_mtime(lock_fd)
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
def try_file_write_lock(target_path: str) -> Iterator[Any]:
  """
  Acquire an exclusive write lock without blocking (timeout 0).
  
  Raises TimeoutError immediately when the lock is contended.
  
  Args:
    target_path (str): String for target path.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> try_file_write_lock("x")  # doctest: +SKIP
  """
  with file_write_lock(target_path, timeout_seconds=0):
    yield


@contextmanager
def file_read_lock_wait(
  target_path: str,
  timeout_seconds: int = READ_WAIT_TIMEOUT_SECONDS,
  expiry_seconds: int = LOCK_EXPIRY_SECONDS,
) -> Iterator[Any]:
  """
  Acquire a shared lock, waiting for active writer lock release.
  
  This acts as "check for lock and wait (up to timeout)" before reads.
  
  Args:
    target_path (str): String for target path.
    timeout_seconds (int): Integer value for timeout seconds.
    expiry_seconds (int): Integer value for expiry seconds.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``file_read_lock_wait`` hits a ``Exception``
    failure path.
    TimeoutError: Raised when ``file_read_lock_wait`` hits a ``TimeoutError``
    failure path.
  
  Examples:
    >>> file_read_lock_wait("x", 0, 0)  # doctest: +SKIP
  """
  start = time.time()
  lock_fd = None
  while True:
    now = time.time()
    _maybe_reset_stale_lock_file(target_path, now, 0)
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
      lock_fd = None
      if exc.errno not in (errno.EACCES, errno.EAGAIN):
        raise
      if (now - start) >= timeout_seconds:
        lock_path = _lock_path(target_path)
        _print_read_lock_timeout(lock_path, timeout_seconds)
        raise TimeoutError(
            "Timed out waiting for read lock: %s" % lock_path
        ) from exc
      try:
        from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
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
