"""SIGALRM suspend helpers for ingest workers waiting on Redis populate."""
from __future__ import annotations

import signal
import time
from contextlib import contextmanager

from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    extend_ingest_task_deadline_monotonic,
    get_ingest_task_deadline_monotonic,
)


@contextmanager
def suspend_ingest_sigalrm_for_populate_wait():
  """Disarm per-file SIGALRM during Redis populate wait; extend deadline on exit."""
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
def populate_wait_ingest_sigalrm_guard(*, respect_ingest_deadline):
  """No-op unless populate wait ignores per-file ingest deadline."""
  if respect_ingest_deadline:
    yield
    return
  with suspend_ingest_sigalrm_for_populate_wait():
    yield
