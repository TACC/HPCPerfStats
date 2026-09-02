"""Day-keyed append claim lists: empty-key delete and concurrent add/pop."""
from __future__ import annotations

import threading

from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
  AppendDayClaimLists,
)


def test_pop_last_claim_deletes_day_key():
  lists = AppendDayClaimLists()
  lists.add("2026-08-01", "a")
  lists.add("2026-08-01", "b")
  assert lists.pop_batch("2026-08-01", 1) == ["a"]
  assert "2026-08-01" in lists.day_keys()
  assert lists.pop_batch("2026-08-01", 8) == ["b"]
  assert lists.day_keys() == ()
  lists.add("2026-08-01", "c")
  assert lists.peek_len("2026-08-01") == 1


def test_pop_missing_day_is_empty():
  lists = AppendDayClaimLists()
  assert lists.pop_batch("2026-08-02", 4) == []
  assert lists.day_keys() == ()


def test_concurrent_add_pop_does_not_leave_empty_keys():
  lists = AppendDayClaimLists()
  n = 200
  errors: list[str] = []

  def _add() -> None:
    try:
      for i in range(n):
        lists.add("2026-08-03", i)
    except Exception as exc:
      errors.append(str(exc))

  def _pop() -> None:
    try:
      remaining = n
      while remaining > 0:
        got = lists.pop_batch("2026-08-03", 7)
        remaining -= len(got)
        if not got:
          remaining -= 1
    except Exception as exc:
      errors.append(str(exc))

  adder = threading.Thread(target=_add)
  popper = threading.Thread(target=_pop)
  adder.start()
  popper.start()
  adder.join(timeout=5.0)
  popper.join(timeout=5.0)
  assert errors == []
  leftover = lists.pop_batch("2026-08-03", n)
  assert lists.day_keys() == ()
  assert len(leftover) <= n
