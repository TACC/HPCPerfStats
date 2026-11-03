"""JobTableHandoff bounds parked frames by bytes, not only by count.

Regression for hpcperfstats04 2026-11-02: the barrier batch path ran metrics
for a 169-job batch before its prewarm, every decoded table waited in
``HANDOFF`` (count cap 256 only), anon RSS reached 219 GB and the pipeline
cgroup OOM-killed update_metrics.
"""

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  HANDOFF,
  JobTableHandoff,
)
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)


def _table(jid, n_rows):
  rows = [
    (1_000_000 * (i + 1), "h1", "host_cpu", "user", None, float(i), 0.0, 0.0)
    for i in range(n_rows)
  ]
  return table_from_records(rows, jid=jid)


def _frame_bytes(table):
  # Row columns plus the frame's cached time axis / index arrays.
  return sum(
    v.nbytes for v in vars(table.frame).values() if isinstance(v, np.ndarray)
  )


def test_handoff_evicts_oldest_past_byte_cap():
  one = _frame_bytes(_table("a", 100))
  h = JobTableHandoff(max_entries=256, max_bytes=int(one * 2.5))
  for jid in ("a", "b", "c", "d"):
    h.put(_table(jid, 100))
  assert h.pop("a") is None
  assert h.pop("b") is None
  assert h.pop("c").jid == "c"
  assert h.pop("d").jid == "d"
  assert h._total_bytes == 0


def test_handoff_does_not_keep_a_table_larger_than_the_cap():
  h = JobTableHandoff(max_bytes=1)
  h.put(_table("big", 50))
  assert h.pop("big") is None
  assert h._total_bytes == 0 and not h._tables


def test_handoff_replace_and_discard_keep_byte_total_exact():
  h = JobTableHandoff()
  h.put(_table("x", 10))
  h.put(_table("x", 40))
  assert h._total_bytes == _frame_bytes(_table("x", 40))
  h.put(_table("y", 5))
  h.discard("x")
  assert h._total_bytes == _frame_bytes(_table("y", 5))
  assert h.pop("y").jid == "y"
  assert h._total_bytes == 0


def test_handoff_count_cap_still_applies():
  h = JobTableHandoff(max_entries=2)
  for jid in ("a", "b", "c"):
    h.put(_table(jid, 3))
  assert h.pop("a") is None
  assert sorted(h._tables) == ["b", "c"]


def test_process_handoff_has_a_byte_cap_below_node_memory():
  assert 0 < HANDOFF.max_bytes <= 32 << 30
