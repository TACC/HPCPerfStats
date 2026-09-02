"""Thread-safe calendar-day lists of claimed tar-append identities.

Attributes:
  AppendDayClaimLists: Process-local day → deque helper (coordinator only).
"""
from __future__ import annotations

from collections import deque
from typing import Any
import threading


class AppendDayClaimLists:
  """
  Process-local day → deque of append claims.

  Redis ``job:v1`` append LIST remains durable SoT. This structure only
  groups claims already taken by the append-coordinator. Empty day keys
  are deleted. Spawn workers must not mutate an instance.

  Args:
    lock (threading.Lock | None): Optional shared lock (tests inject).

  Attributes:
    _lock: Mutex covering the day map.
    _days: Calendar day ``YYYY-MM-DD`` → claim deque.
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
