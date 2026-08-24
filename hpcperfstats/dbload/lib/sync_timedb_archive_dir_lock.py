"""
Exclusive ``archive_dir`` flock for the sync_timedb queue orchestrator.

One coordinator process per archive directory. Dual-run of old+new
orchestrators (or two greenfield processes) is forbidden.

Attributes:
  ORCHESTRATOR_LOCK_BASENAME: Lock sidecar name under ``archive_dir``.
"""
from __future__ import annotations

from typing import Iterator

from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
import os

ORCHESTRATOR_LOCK_BASENAME = ".sync_timedb_orchestrator.fnctl.lock"


def orchestrator_lock_path(archive_dir: str) -> str:
  """
  Return the exclusive flock sidecar path for one ``archive_dir``.

  Args:
    archive_dir (str): Archive data directory root.

  Returns:
    str: Absolute or joined path to the orchestrator lock sidecar.

  Raises:
    ValueError: When ``archive_dir`` is empty.

  Examples:
    >>> orchestrator_lock_path("/data/archive").endswith(
    ...   ".sync_timedb_orchestrator.fnctl.lock"
    ... )
    True
  """
  root = str(archive_dir or "").strip()
  if not root:
    raise ValueError("archive_dir is required for orchestrator lock path")
  return os.path.join(os.path.normpath(root), ORCHESTRATOR_LOCK_BASENAME)


@contextmanager
def exclusive_archive_dir_flock(
  archive_dir: str,
  *,
  blocking: bool = True,
) -> Iterator[int]:
  """
  Hold an exclusive ``fcntl`` flock on the orchestrator sidecar.

  Opens (creating if needed) ``{archive_dir}/.sync_timedb_orchestrator.fnctl.lock``
  and takes ``LOCK_EX``. When ``blocking`` is false, uses ``LOCK_NB`` and
  raises ``BlockingIOError`` / ``OSError`` if another holder exists.

  Args:
    archive_dir (str): Archive data directory root.
    blocking (bool): When True, wait for the lock; when False, fail immediately
      if contended.

  Yields:
    int: Open file descriptor holding the exclusive lock.

  Raises:
    ValueError: When ``archive_dir`` is empty.
    BlockingIOError: When non-blocking acquire fails because another process
      holds the lock (may surface as ``OSError`` on some platforms).
    OSError: When open or flock fails for other reasons.
    Exception: Re-raised when a bare ``raise`` propagates from flock failure
      handling.

  Examples:
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...   with exclusive_archive_dir_flock(d):
    ...     pass
  """
  path = orchestrator_lock_path(archive_dir)
  os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
  fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
  flags = LOCK_EX if blocking else (LOCK_EX | LOCK_NB)
  try:
    flock(fd, flags)
  except OSError:
    os.close(fd)
    raise
  try:
    yield fd
  finally:
    try:
      flock(fd, LOCK_UN)
    except OSError:
      pass
    try:
      os.close(fd)
    except OSError:
      pass
