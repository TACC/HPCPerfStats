"""
Default-off wait/hold timing for in-process sync_timedb store RLocks.

Wraps ``threading.RLock`` so job-store and members-store acquires record
wait and hold seconds when telemetry is enabled. Nested re-entrant acquires
on the same thread do not double-count hold time.

Attributes:
  STORE_LOCK_TELEM_KEYS: Bounded store lock telemetry key names.
  TimedRLock: RLock wrapper that records wait/hold when enabled.
  _store_lock_telem_on: Process-wide store-lock timing enable flag.
  _store_lock_totals: Accumulated wait/hold seconds by key.
  _store_lock_totals_lock: Mutex protecting store-lock timing totals.
"""
from __future__ import annotations

import threading
import time
from typing import Any

STORE_LOCK_TELEM_KEYS: tuple[str, ...] = (
    "job_store_wait_s",
    "job_store_hold_s",
    "members_store_wait_s",
    "members_store_hold_s",
)

_store_lock_telem_on = False
_store_lock_totals: dict[str, float] = {key: 0.0 for key in STORE_LOCK_TELEM_KEYS}
_store_lock_totals_lock = threading.Lock()


def reset_store_lock_timing(*, enabled: bool = False) -> None:
  """
  Zero store-lock wait/hold accumulators; optionally enable telemetry.

  Args:
    enabled (bool): When ``False``, ``TimedRLock`` skips timing holds.

  Returns:
    None

  Examples:
    >>> reset_store_lock_timing(enabled=False)
  """
  global _store_lock_telem_on
  with _store_lock_totals_lock:
    _store_lock_telem_on = bool(enabled)
    for key in STORE_LOCK_TELEM_KEYS:
      _store_lock_totals[key] = 0.0


def snapshot_store_lock_timing() -> dict[str, float]:
  """
  Return accumulated store lock wait/hold seconds when telemetry is enabled.

  Returns:
    dict[str, float]: Empty when disabled; else all ``STORE_LOCK_TELEM_KEYS``.

  Examples:
    >>> reset_store_lock_timing(enabled=False)
    >>> snapshot_store_lock_timing()
    {}
  """
  if not _store_lock_telem_on:
    return {}
  with _store_lock_totals_lock:
    return {
        key: float(_store_lock_totals.get(key, 0.0))
        for key in STORE_LOCK_TELEM_KEYS
    }


def _add_store_lock_timing(key: str, delta_s: float) -> None:
  """
  Accumulate non-negative seconds into the store-lock telemetry dict.

  Args:
    key (str): A key in ``STORE_LOCK_TELEM_KEYS``.
    delta_s (float): Wait or hold duration in seconds (non-positive ignored).

  Returns:
    None

  Examples:
    >>> reset_store_lock_timing(enabled=True)
    >>> _add_store_lock_timing("job_store_wait_s", 0.1)
  """
  if not _store_lock_telem_on:
    return
  delta = float(delta_s)
  if delta <= 0.0 or key not in STORE_LOCK_TELEM_KEYS:
    return
  with _store_lock_totals_lock:
    _store_lock_totals[key] = float(_store_lock_totals.get(key, 0.0)) + delta


class TimedRLock:
  """
  ``threading.RLock`` wrapper that records wait/hold when store telemetry is on.

  Compatible with ``threading.Condition``. Nested re-entrant acquires on the
  owning thread only charge wait/hold on the outermost acquisition.

  Attributes:
    kind: Store kind used to build ``{kind}_wait_s`` / ``{kind}_hold_s`` keys.
    _local: Thread-local nesting depth and hold start time.
    _lock: Underlying ``threading.RLock``.
  """

  def __init__(self, kind: str) -> None:
    """
    Create a timed re-entrant lock for one store family.

    Args:
      kind (str): ``job_store``, ``members_store``, or ``members_store_day``
        (day-shard locks roll up into ``members_store_*`` telem keys).

    Returns:
      None

    Raises:
      ValueError: When ``kind`` is not a supported store family.

    Examples:
      >>> TimedRLock("job_store").kind
      'job_store'
    """
    if kind not in ("job_store", "members_store", "members_store_day"):
      raise ValueError("unsupported store lock kind: %r" % kind)
    # Day shards share the members_store telem bucket.
    self.kind = (
        "members_store" if kind == "members_store_day" else kind
    )
    self._lock = threading.RLock()
    self._local = threading.local()

  def acquire(
      self,
      blocking: bool = True,
      timeout: float = -1,
  ) -> bool:
    """
    Acquire the underlying RLock and optionally record wait time.

    Args:
      blocking (bool): Passed to ``threading.RLock.acquire``.
      timeout (float): Passed to ``threading.RLock.acquire``.

    Returns:
      bool: True when the lock was acquired.

    Examples:
      >>> lock = TimedRLock("job_store")
      >>> lock.acquire()
      True
      >>> lock.release()
    """
    depth = int(getattr(self._local, "depth", 0))
    wait_t0 = time.monotonic()
    acquired = self._lock.acquire(blocking, timeout)
    if not acquired:
      return False
    if depth == 0 and _store_lock_telem_on:
      _add_store_lock_timing(
          "%s_wait_s" % self.kind,
          time.monotonic() - wait_t0,
      )
      self._local.hold_t0 = time.monotonic()
    self._local.depth = depth + 1
    return True

  def release(self) -> None:
    """
    Release the underlying RLock and optionally record hold time.

    Returns:
      None

    Raises:
      RuntimeError: When releasing a lock not owned by this thread.

    Examples:
      >>> lock = TimedRLock("job_store")
      >>> lock.acquire()
      True
      >>> lock.release()
    """
    depth = int(getattr(self._local, "depth", 0))
    if depth <= 0:
      raise RuntimeError("release unlocked TimedRLock")
    depth -= 1
    self._local.depth = depth
    if depth == 0 and _store_lock_telem_on:
      hold_t0 = float(getattr(self._local, "hold_t0", time.monotonic()))
      _add_store_lock_timing(
          "%s_hold_s" % self.kind,
          time.monotonic() - hold_t0,
      )
    self._lock.release()

  def locked(self) -> bool:
    """
    Return whether any thread currently holds the underlying RLock.

    Returns:
      bool: True when the lock is held.

    Examples:
      >>> TimedRLock("job_store").locked()
      False
    """
    return bool(self._lock.locked())

  def _is_owned(self) -> bool:
    """
    Return whether the current thread owns the underlying RLock.

    Returns:
      bool: True when this thread holds the lock (any nesting depth).

    Examples:
      >>> TimedRLock("job_store")._is_owned()
      False
    """
    is_owned = getattr(self._lock, "_is_owned", None)
    if callable(is_owned):
      return bool(is_owned())
    return int(getattr(self._local, "depth", 0)) > 0

  def __enter__(self) -> TimedRLock:
    """
    Context-manager acquire.

    Returns:
      TimedRLock: This lock after acquisition.

    Examples:
      >>> with TimedRLock("job_store"):
      ...   True
      True
    """
    self.acquire()
    return self

  def __exit__(self, *exc: Any) -> None:
    """
    Context-manager release.

    Args:
      *exc (Any): Exception triple from the ``with`` block (ignored).

    Returns:
      None

    Examples:
      >>> lock = TimedRLock("job_store")
      >>> lock.__enter__()
      TimedRLock(...)
      >>> lock.__exit__(None, None, None)
    """
    self.release()
