"""Thread-safe calendar-day lists of claimed tar-append identities.

Attributes:
  AppendDayClaimLists: Process-local day -> deque helper (coordinator only).
  _DAY_REMAINDER_LOCK: Mutex for the in-memory day remainder maps.
  _DAY_APPEND_LEFT: Calendar day -> queued append jobs still unacked.
  _DAY_INGEST_LEFT: Calendar day -> queued ingest jobs still unacked.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class AppendDayClaimLists:
  """
  Process-local day -> deque of append claims.

  The job-store append LIST remains durable SoT. This structure only
  groups claims already taken by the append-coordinator. Empty day keys
  are deleted. Spawn workers must not mutate an instance.

  Args:
    lock (threading.Lock | None): Optional shared lock (tests inject).

  Attributes:
    _lock: Mutex covering the day map.
    _days: Calendar day ``YYYY-MM-DD`` -> claim deque.
  """

  def __init__(self, lock: threading.Lock | None = None) -> None:
    """
    Create an empty day-keyed claim map.

    Args:
      lock (threading.Lock | None): Optional lock; default is a new Lock.

    Returns:
      None

    Examples:
      >>> AppendDayClaimLists().peek_len("2026-08-01")
      0
    """
    self._lock = lock if lock is not None else threading.Lock()
    self._days: dict[str, deque[Any]] = {}

  def add(self, day: str, claim: Any) -> None:
    """
    Append ``claim`` under ``day``, creating the deque when needed.

    Args:
      day (str): Calendar day ``YYYY-MM-DD``.
      claim (Any): Claimed append job (identity + lease token).

    Returns:
      None

    Examples:
      >>> lists = AppendDayClaimLists()
      >>> lists.add("2026-08-01", "c1")
      >>> lists.peek_len("2026-08-01")
      1
    """
    key = str(day or "")
    if not key:
      return
    with self._lock:
      bucket = self._days.get(key)
      if bucket is None:
        bucket = deque()
        self._days[key] = bucket
      bucket.append(claim)

  def pop_batch(self, day: str, n: int) -> list[Any]:
    """
    Pop up to ``n`` claims from ``day``; delete the key when empty.

    Args:
      day (str): Calendar day ``YYYY-MM-DD``.
      n (int): Maximum claims to pop (clamped to >= 0).

    Returns:
      list[Any]: Claims in FIFO order (may be shorter than ``n``).

    Examples:
      >>> lists = AppendDayClaimLists()
      >>> lists.add("2026-08-01", "a")
      >>> lists.add("2026-08-01", "b")
      >>> lists.pop_batch("2026-08-01", 2)
      ['a', 'b']
      >>> lists.peek_len("2026-08-01")
      0
    """
    key = str(day or "")
    take = max(0, int(n))
    if not key or take == 0:
      return []
    out: list[Any] = []
    with self._lock:
      bucket = self._days.get(key)
      if bucket is None:
        return []
      while take > 0 and bucket:
        out.append(bucket.popleft())
        take -= 1
      if not bucket:
        del self._days[key]
    return out

  def peek_len(self, day: str) -> int:
    """
    Return the claim count for ``day`` without popping.

    Args:
      day (str): Calendar day ``YYYY-MM-DD``.

    Returns:
      int: Length of that day's deque, or 0 when the key is absent.

    Examples:
      >>> AppendDayClaimLists().peek_len("missing")
      0
    """
    key = str(day or "")
    with self._lock:
      bucket = self._days.get(key)
      return 0 if bucket is None else len(bucket)

  def peek_first(self, day: str) -> Any:
    """
    Return the oldest claim for ``day`` without popping.

    Args:
      day (str): Calendar day ``YYYY-MM-DD``.

    Returns:
      Any: First claim, or ``None`` when the day key is absent.

    Examples:
      >>> AppendDayClaimLists().peek_first("missing") is None
      True
    """
    key = str(day or "")
    with self._lock:
      bucket = self._days.get(key)
      if not bucket:
        return None
      return bucket[0]

  def day_keys(self) -> tuple[str, ...]:
    """
    Return day keys that currently have at least one claim.

    Returns:
      tuple[str, ...]: Snapshot of non-empty day keys.

    Examples:
      >>> AppendDayClaimLists().day_keys()
      ()
    """
    with self._lock:
      return tuple(self._days.keys())

  def claim_identities(self) -> tuple[str, ...]:
    """
    Return stats-file identities held in every day list.

    Args:
      None

    Returns:
      tuple[str, ...]: Identities currently parked, oldest day-key order.

    Examples:
      >>> lists = AppendDayClaimLists()
      >>> lists.add("2026-08-01", type("C", (), {"identity": "/raw/a"})())
      >>> lists.claim_identities()
      ('/raw/a',)
    """
    with self._lock:
      out: list[str] = []
      for bucket in self._days.values():
        for claim in bucket:
          ident = str(getattr(claim, "identity", "") or "")
          if ident:
            out.append(ident)
      return tuple(out)

  def clear(self) -> None:
    """
    Drop every day key (tests / coordinator restart in-process).

    Returns:
      None

    Examples:
      >>> lists = AppendDayClaimLists()
      >>> lists.add("2026-08-01", "c")
      >>> lists.clear()
      >>> lists.day_keys()
      ()
    """
    with self._lock:
      self._days.clear()


_DAY_REMAINDER_LOCK = threading.Lock()
_DAY_APPEND_LEFT: dict[str, int] = {}
_DAY_INGEST_LEFT: dict[str, int] = {}


def note_day_remainder(kind: str, day: str, delta: int) -> None:
  """
  Adjust the in-memory count of queued ingest or append jobs for one day.

  Args:
    kind (str): ``ingest`` or ``append``.
    day (str): Calendar day ``YYYY-MM-DD``.
    delta (int): ``+1`` on enqueue, ``-1`` on ack or dead-letter.

  Returns:
    None

  Examples:
    >>> reset_day_remainder_for_tests()
    >>> note_day_remainder("append", "2026-08-01", 1)
    >>> day_remainder("append", "2026-08-01")
    1
  """
  key = str(day or "")
  which = str(kind or "")
  if not key or which not in ("ingest", "append") or int(delta) == 0:
    return
  bucket = _DAY_APPEND_LEFT if which == "append" else _DAY_INGEST_LEFT
  with _DAY_REMAINDER_LOCK:
    cur = bucket.get(key, 0) + int(delta)
    if cur <= 0:
      bucket.pop(key, None)
    else:
      bucket[key] = cur


def day_remainder(kind: str, day: str) -> int:
  """
  Return the in-memory queued-job count for one day and kind.

  Args:
    kind (str): ``ingest`` or ``append``.
    day (str): Calendar day ``YYYY-MM-DD``.

  Returns:
    int: Count, or 0 when the day is absent.

  Examples:
    >>> day_remainder("append", "missing")
    0
  """
  key = str(day or "")
  which = str(kind or "")
  if not key or which not in ("ingest", "append"):
    return 0
  bucket = _DAY_APPEND_LEFT if which == "append" else _DAY_INGEST_LEFT
  with _DAY_REMAINDER_LOCK:
    return int(bucket.get(key, 0))


def reset_day_remainder_for_tests() -> None:
  """
  Clear process-local day remainder counts.

  Returns:
    None

  Examples:
    >>> note_day_remainder("ingest", "2026-08-01", 1)
    >>> reset_day_remainder_for_tests()
    >>> day_remainder("ingest", "2026-08-01")
    0
  """
  with _DAY_REMAINDER_LOCK:
    _DAY_APPEND_LEFT.clear()
    _DAY_INGEST_LEFT.clear()
