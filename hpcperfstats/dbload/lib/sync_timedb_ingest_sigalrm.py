"""
SIGALRM suspend helpers for ingest workers on non-work waits.

Populate Redis wait and Manager write-lock *acquire* both suspend the
per-file SIGALRM and extend the monotonic deadline by wait elapsed. Time
spent holding the write lock (ORM / bulk_create) remains charged.
"""
from __future__ import annotations

from typing import Any, Iterator

import signal
import time
from contextlib import contextmanager

from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    extend_ingest_task_deadline_monotonic,
    get_ingest_task_deadline_monotonic,
)


@contextmanager
def suspend_ingest_sigalrm_for_non_work_wait() -> Iterator[Any]:
  """
  Disarm per-file SIGALRM during a non-work wait; extend deadline on exit.

  Use for Redis populate wait and Manager write-lock *acquire* only — not
  while holding the lock during ``bulk_create``.

  Yields:
    Iterator[Any]: Control to the wait body; no value is produced.

  Examples:
    >>> with suspend_ingest_sigalrm_for_non_work_wait():
    ...   pass  # doctest: +SKIP
  """
  deadline = get_ingest_task_deadline_monotonic()
  if deadline is None or not hasattr(signal, "SIGALRM"):
    yield
    return
  wait_t0 = time.monotonic()
  if hasattr(signal, "setitimer"):
    signal.setitimer(signal.ITIMER_REAL, 0)
  else:
    signal.alarm(0)
  try:
    yield
  finally:
    elapsed = time.monotonic() - wait_t0
    if elapsed > 0.0:
      extend_ingest_task_deadline_monotonic(elapsed)
    new_deadline = get_ingest_task_deadline_monotonic()
    if new_deadline is None:
      return
    remaining = float(new_deadline) - time.monotonic()
    if remaining <= 0.0:
      return
    if hasattr(signal, "setitimer"):
      signal.setitimer(signal.ITIMER_REAL, remaining)
    else:
      signal.alarm(max(1, int(remaining)))


@contextmanager
def suspend_ingest_sigalrm_for_populate_wait() -> Iterator[Any]:
  """
  Alias for :func:`suspend_ingest_sigalrm_for_non_work_wait` (populate wait).

  Yields:
    Iterator[Any]: Same as ``suspend_ingest_sigalrm_for_non_work_wait``.

  Examples:
    >>> with suspend_ingest_sigalrm_for_populate_wait():
    ...   pass  # doctest: +SKIP
  """
  with suspend_ingest_sigalrm_for_non_work_wait():
    yield


@contextmanager
def populate_wait_ingest_sigalrm_guard(
  *,
  respect_ingest_deadline: Any,
) -> Iterator[Any]:
  """
  No-op unless populate wait ignores per-file ingest deadline.

  Args:
    respect_ingest_deadline (Any): When truthy, do not suspend SIGALRM;
      when falsy, suspend and extend the deadline for the wait.

  Yields:
    Iterator[Any]: Control to the populate wait body.

  Examples:
    >>> with populate_wait_ingest_sigalrm_guard(respect_ingest_deadline=True):
    ...   pass
  """
  if respect_ingest_deadline:
    yield
    return
  with suspend_ingest_sigalrm_for_populate_wait():
    yield
