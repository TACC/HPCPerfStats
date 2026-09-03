"""
Idle-clock suspend helpers for ingest workers on non-work waits.

Populate members-store wait and Manager write-lock *acquire* suspend the idle
stall clock (touch progress on exit). Internal wall SIGALRM soft-kill is
deleted — these helpers no longer arm setitimer for wall budgets.
"""
from __future__ import annotations

from typing import Any, Iterator

from contextlib import contextmanager


@contextmanager
def suspend_ingest_sigalrm_for_non_work_wait() -> Iterator[Any]:
  """
  Suspend idle-stall clock during a non-work wait; touch progress on exit.

  Use for members-store populate wait and Manager write-lock *acquire* only — not
  while holding the lock during ``bulk_create``.

  Yields:
    Iterator[Any]: Control to the wait body; no value is produced.

  Examples:
    >>> with suspend_ingest_sigalrm_for_non_work_wait():
    ...   pass  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_progress import (
      touch_ingest_progress,
  )

  try:
    yield
  finally:
    touch_ingest_progress()


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
    respect_ingest_deadline (Any): When truthy, do not suspend idle clock;
      when falsy, suspend and touch progress for the wait.

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
